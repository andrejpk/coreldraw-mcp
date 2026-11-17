"""Inspect CorelDRAW's view and print configuration without submitting output."""

from pathlib import Path
from tempfile import TemporaryDirectory

from core.connection import get_connection
from core.models import ToolResult
from tools.document import _scoped_document, _scoped_page

_PDF_PAGE_RANGE = 3
_PDF_NATIVE_COLOR = 3
_PDF_SPOT_AS_SPOT = 0


def _overprint_view(document_path: str, enabled: bool | None = None) -> ToolResult:
    conn = get_connection()

    def read_view():
        doc, actual_path = _scoped_document(conn.app, document_path)
        view = doc.ActiveWindow.ActiveView
        if enabled is not None:
            view.SimulateOverprints = enabled
        return {"document_path": actual_path, "simulate_overprints": bool(view.SimulateOverprints)}

    result = conn.safe_call(read_view)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if enabled is not None and data["simulate_overprints"] != enabled:
        return ToolResult(success=False, error="CorelDRAW did not apply the requested view setting", data=data)
    return ToolResult.ok(**data)


def get_simulate_overprints(document_path: str) -> ToolResult:
    """Read Simulate Overprints in the window of the specified active saved drawing."""
    return _overprint_view(document_path)


def set_simulate_overprints(document_path: str, enabled: bool) -> ToolResult:
    """Set Simulate Overprints in the specified active drawing's window and read the state back."""
    return _overprint_view(document_path, enabled)


def get_print_separation_settings(document_path: str) -> ToolResult:
    """Read the active drawing's print configuration and CorelDRAW's available separation plates.

    Plate availability and enabled flags describe configuration for the print range. They
    cannot establish which pages emit a non-empty plate, its image, or RIP output. This
    tool reads current settings without submitting a print job.
    """
    conn = get_connection()

    def read_settings():
        doc, actual_path = _scoped_document(conn.app, document_path)
        settings = doc.PrintSettings
        separations = settings.Separations
        fields = {
            "printer": lambda: settings.Printer.Name, "copies": lambda: settings.Copies,
            "print_range": lambda: settings.PrintRange, "page_range": lambda: settings.PageRange,
            "page_set": lambda: settings.PageSet, "print_to_file": lambda: bool(settings.PrintToFile),
            "separations_enabled": lambda: bool(separations.Enabled), "spot_to_cmyk": lambda: bool(separations.SpotToCMYK),
            "print_empty_plates": lambda: bool(separations.EmptyPlates),
            "preserve_overprints": lambda: bool(separations.PreserveOverprints),
            "available_plates": lambda: _available_plates(separations),
        }
        return {"document_path": actual_path, **_read_fields(fields)}

    result = conn.safe_call(read_settings)
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


def _available_plates(separations):
    plates = separations.Plates
    configured = []
    for index in range(1, plates.Count + 1):
        plate = plates.Item(index)
        fields = {
            "plate_type": lambda: plate.Type, "enabled": lambda: bool(plate.Enabled),
            "label": lambda: plate.Color, "angle": lambda: plate.Angle,
            "frequency": lambda: plate.Frequency,
        }
        configured.append({"index": index, **_read_fields(fields)})
    return configured


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


def export_page_proof_pdf(document_path: str, page_index: int, output_path: str) -> ToolResult:
    """Publish one page as a composite proof PDF with native colors, spots, and overprint instructions.

    Use a new absolute PDF path in an existing directory. CorelDRAW's Publish to PDF
    engine supplies the artwork. This PDF can be rendered by a separations-capable
    renderer. It does not reproduce the print dialog, active printer layout, or RIP.
    The active view and original PDF options are preserved. No print job is sent.
    """
    conn = get_connection()
    try:
        destination = _proof_destination(output_path)
    except ValueError as exc:
        return ToolResult.fail(str(exc))

    def publish():
        doc, actual_path = _scoped_document(conn.app, document_path)
        _scoped_page(doc, page_index)
        size = _publish_page(doc, page_index, destination)
        return {
            "document_path": actual_path, "page_index": page_index, "output_path": str(destination),
            "bytes": size, "proof_source": "CorelDRAW Publish to PDF",
        }

    result = conn.safe_call(publish)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def _layer_label(layer):
    return f"{layer.Name} ({'shared' if layer.Master else 'page'})"


def export_layer_proof_pdf(document_path: str, page_index: int, layer_name: str, output_path: str) -> ToolResult:
    """Publish a proof PDF of one page containing only one named page layer, with native spots and overprints.

    layer_name must match exactly one ordinary layer local to the page. Every other layer on the
    page, including shared master layers, is made non-printing only while publishing. Printability
    decides which layers are published. A hidden layer is published as a PDF layer that is off by
    default, which renderers show as blank, so a hidden target is made visible while publishing.
    Afterward the tool restores and verifies each layer's printability and visibility, the PDF
    settings, the active page, the document unit, and the unsaved-changes flag, after success or
    failure. No print job is sent.
    """
    conn = get_connection()
    try:
        destination = _proof_destination(output_path)
    except ValueError as exc:
        return ToolResult.fail(str(exc))

    def publish():
        doc, actual_path = _scoped_document(conn.app, document_path)
        page = _scoped_page(doc, page_index)
        layers = list(page.AllLayers)
        targets = [layer for layer in layers if layer.Name == layer_name and not layer.Master
                   and not layer.IsSpecialLayer]
        if len(targets) != 1:
            raise ValueError(f"Expected one page layer named {layer_name!r}; found {len(targets)}")
        target = targets[0]
        # (layer, property, original value, value while publishing)
        flags = [(layer, "Printable", bool(layer.Printable), layer is target) for layer in layers]
        target_visible = bool(target.Visible)
        flags.append((target, "Visible", target_visible, True))
        state = (doc.Dirty, doc.ActivePage.Index, doc.Unit)
        excluded = [_layer_label(layer) for layer, name, value, _ in flags
                    if name == "Printable" and value and layer is not target and layer.Shapes.Count]
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
            doc.Dirty = state[0]
            if failed:
                raise RuntimeError(f"Failed to restore layer flags: {', '.join(failed)}")
            if (doc.Dirty, doc.ActivePage.Index, doc.Unit) != state:
                raise RuntimeError("The unsaved-changes flag, active page, or document unit changed")
        return {
            "document_path": actual_path, "page_index": page_index, "layer_name": layer_name,
            "layer_visible": target_visible, "layer_shape_count": target.Shapes.Count,
            "excluded_printable_layers": excluded, "output_path": str(destination), "bytes": size,
            "proof_source": "CorelDRAW Publish to PDF",
        }

    result = conn.safe_call(publish)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])
