"""Inspect CorelDRAW's view and print configuration without submitting output."""

from pathlib import Path
from tempfile import TemporaryDirectory

from core.connection import get_connection
from core.models import ToolResult
from core.targets import resolve_document, resolve_page

_PDF_PAGE_RANGE = 3
_PDF_NATIVE_COLOR = 3
_PDF_SPOT_AS_SPOT = 0


def simulate_overprints(document_path: str, enabled: bool | None = None) -> ToolResult:
    """Read or set Simulate Overprints in the window of an exact open document.

    enabled omitted: read only (the document stays as it is). enabled true/false: activates the
    document, sets the view option, and reads it back. A view setting only: it changes no artwork and
    no output; render_plates shows what overprints do to each film.
    """
    conn = get_connection()

    def view_state():
        # Only the active document has an ActiveWindow; reading uses its first window instead.
        doc, actual_path = resolve_document(conn.app, document_path, activate=enabled is not None)
        window = doc.ActiveWindow or doc.Windows.Item(1)
        view = window.ActiveView
        if enabled is not None:
            view.SimulateOverprints = enabled
        return {"document_path": actual_path, "simulate_overprints": bool(view.SimulateOverprints)}

    result = conn.safe_call(view_state) if enabled is not None else conn.read_call(view_state)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if enabled is not None and data["simulate_overprints"] != enabled:
        return ToolResult(success=False, error="CorelDRAW did not apply the requested view setting", data=data)
    return ToolResult.ok(**data)


def get_print_separation_settings(document_path: str) -> ToolResult:
    """Read an exact open document's print and separation settings without printing.

    Returns the printer, copies, print range, whether separations are on, spot_to_cmyk (spots
    printed as process), print_empty_plates, preserve_overprints, and disabled_plates (plates
    switched off in the print settings, which will not print). Which plates each page produces,
    and the objects behind them, come from get_ink_inventory.
    """
    conn = get_connection()

    def read_settings():
        doc, actual_path = resolve_document(conn.app, document_path)
        settings = doc.PrintSettings
        separations = settings.Separations
        fields = {
            "printer": lambda: settings.Printer.Name, "copies": lambda: settings.Copies,
            "print_range": lambda: settings.PrintRange, "page_range": lambda: settings.PageRange,
            "page_set": lambda: settings.PageSet, "print_to_file": lambda: bool(settings.PrintToFile),
            "separations_enabled": lambda: bool(separations.Enabled), "spot_to_cmyk": lambda: bool(separations.SpotToCMYK),
            "print_empty_plates": lambda: bool(separations.EmptyPlates),
            "preserve_overprints": lambda: bool(separations.PreserveOverprints),
            "disabled_plates": lambda: _disabled_plates(separations),
        }
        return {"document_path": actual_path, **_read_fields(fields)}

    result = conn.read_call(read_settings)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def _read_fields(fields):
    result = {}
    unavailable = {}
    for field, read in fields.items():
        try:
            result[field] = read()
        except Exception as exc:
            result[field] = None
            unavailable[field] = str(exc)
    return {**result, "unavailable": unavailable}


def _disabled_plates(separations):
    """Labels of the plates switched off for printing.

    CorelDRAW's plate list is the color set's configuration: it lists inks that no page uses (for example
    six Hexachrome plates), all enabled, so only the plates switched off are informative.
    """
    plates = separations.Plates
    return [str(plate.Color) for plate in (plates.Item(index) for index in range(1, plates.Count + 1))
            if not plate.Enabled]


def _proof_destination(output_path):
    destination = Path(output_path)
    if not destination.is_absolute() or destination.suffix.lower() != ".pdf":
        raise ValueError("output_path must be an absolute PDF path")
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Choose a new filename in an existing directory")
    return destination


def _publish_page(doc, page_index, destination):
    """Publish one page with native spots and overprints, restoring the PDF settings. Returns bytes written."""
    settings = doc.PDFSettings
    proof = {
        "PublishRange": _PDF_PAGE_RANGE, "PageRange": str(page_index), "ColorMode": _PDF_NATIVE_COLOR,
        "OutputSpotColorsAs": _PDF_SPOT_AS_SPOT, "Overprints": True,
        "CropMarks": False, "RegistrationMarks": False,
    }
    original = {name: getattr(settings, name) for name in proof}
    with TemporaryDirectory(prefix="coreldraw-proof-", dir=destination.parent) as directory:
        staged = Path(directory, "page.pdf")
        try:
            for name, value in proof.items():
                setattr(settings, name, value)
            if any(getattr(settings, name) != value for name, value in proof.items()):
                raise RuntimeError("CorelDRAW did not accept the requested PDF proof settings")
            doc.PublishToPDF(str(staged))
        finally:
            failed = []
            for name, value in original.items():
                try:
                    setattr(settings, name, value)
                    if getattr(settings, name) != value:
                        failed.append(name)
                except Exception:
                    failed.append(name)
            if failed:
                raise RuntimeError(f"Failed to restore PDF settings: {', '.join(failed)}")
        with staged.open("rb") as pdf:
            if pdf.read(5) != b"%PDF-":
                raise RuntimeError("CorelDRAW did not produce a PDF")
        size = staged.stat().st_size
        staged.rename(destination)
    return size


def _layer_label(layer):
    return f"{layer.Name} ({'shared' if layer.Master else 'page'})"


def _hidden_printable(layer):
    return bool(layer.Printable) and not bool(layer.Visible) and not layer.IsSpecialLayer and layer.Shapes.Count > 0


def publish_proof(app, document_path, page_index, destination, layer_name=None, include_hidden_printable=False):
    """Publish one page (or one page layer) to ``destination`` on the COM worker; restore and verify all state.

    Layer printability and visibility are changed only while publishing (layer proof, or hidden-but-printable
    layers made visible). Afterwards every changed flag, the dirty flag, the active page and the unit are
    verified; PDF settings are restored by _publish_page. Raises on any failure, after restoring.
    """
    changes_flags = layer_name is not None or include_hidden_printable
    # Layer flags change while publishing, so the document is activated like any edit.
    doc, actual_path = resolve_document(app, document_path, activate=changes_flags)
    page = resolve_page(doc, page_index)
    layers = list(page.AllLayers)
    flags = []  # (layer, property, original value, value while publishing)
    info = {}
    if layer_name is not None:
        targets = [layer for layer in layers if layer.Name == layer_name and not layer.Master
                   and not layer.IsSpecialLayer]
        if len(targets) != 1:
            raise ValueError(f"Expected one page layer named {layer_name!r}; found {len(targets)}")
        target = targets[0]
        flags = [(layer, "Printable", bool(layer.Printable), layer is target) for layer in layers]
        info["layer_visible"] = bool(target.Visible)
        flags.append((target, "Visible", info["layer_visible"], True))
        info["layer_shape_count"] = target.Shapes.Count
        info["excluded_printable_layers"] = [_layer_label(layer) for layer, name, value, _ in flags
                                             if name == "Printable" and value and layer is not target
                                             and layer.Shapes.Count]
    else:
        hidden = [layer for layer in layers if _hidden_printable(layer)]
        labels = [_layer_label(layer) for layer in hidden]
        if include_hidden_printable:
            flags = [(layer, "Visible", False, True) for layer in hidden]
            info["hidden_printable_layers_included"] = labels
        else:
            info["hidden_printable_layers_omitted"] = labels
    state = (doc.Dirty, doc.ActivePage.Index, doc.Unit)
    try:
        for layer, name, value, wanted in flags:
            if value != wanted:
                setattr(layer, name, wanted)
            if bool(getattr(layer, name)) != wanted:
                raise RuntimeError(f"CorelDRAW did not change {name} of layer {_layer_label(layer)}")
        size = _publish_page(doc, page_index, destination)
    finally:
        failed = []
        for layer, name, value, _ in flags:
            try:
                if bool(getattr(layer, name)) != value:
                    setattr(layer, name, value)
                if bool(getattr(layer, name)) != value:
                    failed.append(f"{_layer_label(layer)} {name}")
            except Exception:
                failed.append(f"{_layer_label(layer)} {name}")
        if doc.Dirty != state[0]:
            doc.Dirty = state[0]
        if failed:
            raise RuntimeError(f"Failed to restore layer flags: {', '.join(failed)}")
        if (doc.Dirty, doc.ActivePage.Index, doc.Unit) != state:
            raise RuntimeError("The unsaved-changes flag, active page, or document unit changed")
    return {"document_path": actual_path, "page_index": page_index, "layer_name": layer_name, **info,
            "output_path": str(destination), "bytes": size, "proof_source": "CorelDRAW Publish to PDF"}


def export_page_proof_pdf(document_path: str, page_index: int, output_path: str, layer_name: str | None = None,
                          include_hidden_printable: bool = False) -> ToolResult:
    """Publish one page, or one page layer, as a composite proof PDF with native spots and overprints.

    output_path: a new absolute .pdf path in an existing directory. CorelDRAW's Publish to PDF supplies
    the artwork; it is not the print dialog, printer layout or RIP. No print job is sent.
    Publish to PDF omits hidden layers even when they are printable (CorelDRAW print may include them):
    their names are returned in hidden_printable_layers_omitted; include_hidden_printable=true makes
    them visible while publishing. layer_name: proof of exactly one page-local layer (not master or
    special); every other layer, including shared master layers, is non-printing while publishing, and
    a hidden target is made visible. When layer flags change the document is activated. Afterwards the
    tool restores and verifies layer printability and visibility, the PDF settings, the active page, the
    document unit and the unsaved-changes flag, after success or failure. render_plates renders the films.
    """
    conn = get_connection()
    try:
        destination = _proof_destination(output_path)
    except ValueError as exc:
        return ToolResult.fail(str(exc))
    if layer_name is not None and include_hidden_printable:
        return ToolResult.fail("include_hidden_printable applies to whole-page proofs, not layer_name proofs")
    result = conn.safe_call(lambda: publish_proof(conn.app, document_path, page_index, destination, layer_name,
                                                  include_hidden_printable))
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])
