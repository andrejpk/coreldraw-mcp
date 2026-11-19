"""Export tools for the active page: PDF, DXF, AI, SVG, PNG, JPEG, a PNG preview and per-page batches."""

import os

from core.connection import error_text, get_connection
from core.models import ToolResult
from core.results import respond
from core.targets import call_on_active
from core.units import MILLIMETER, convert

# Export filter IDs (CorelDRAW X6/16.1 COM filter numbers)
_CDR_DXF = 1296
_CDR_AI = 1305
_CDR_SVG = 1345
_CDR_PNG = 802
_CDR_JPEG = 774

_CDR_ALL_PAGES = 0
_CDR_CURRENT_PAGE = 1
_PDF_CURRENT_PAGE = 1
_CDR_RGB_COLOR_IMAGE = 4
_CDR_NORMAL_ANTIALIASING = 1
_CDR_COMPRESSION_NONE = 0

_DXF_VERSION = {"R12": 0, "R14": 1, "R2000": 3, "R2004": 4}
_PDFX_VERSIONS = {"PDFX1a": 0, "PDFX3": 1, "PDFX4": 2}


def _ensure_dir(path: str) -> None:
    """Create the output folder if needed."""
    dirpath = os.path.dirname(path)
    if dirpath and not os.path.isdir(dirpath):
        os.makedirs(dirpath, exist_ok=True)


def _attempt(warnings: list, label: str, action) -> bool:
    """Run an optional export step; on failure record why in ``warnings`` instead of hiding it."""
    try:
        action()
        return True
    except Exception as exc:
        warnings.append(f"{label}: {error_text(exc)}")
        return False


def _page_size_mm(doc):
    """Active page size converted to mm, without changing the document unit."""
    page = doc.ActivePage
    app = get_connection().app
    return convert(page.SizeWidth, doc.Unit, MILLIMETER, app), convert(page.SizeHeight, doc.Unit, MILLIMETER, app)


def _prepare_current_page_export(doc, warnings: list) -> int:
    """Make export state explicit so CorelDRAW does not reuse stale UI settings. Returns the page's shape count."""
    page = doc.ActivePage
    _attempt(warnings, "clear selection", doc.ClearSelection)
    _attempt(warnings, "activate page", page.Activate)
    return page.Shapes.Count


def _finish_export_filter(export_filter) -> None:
    if export_filter is not None:
        export_filter.Finish()


def _verify_exported(path: str) -> int:
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size <= 0:
        raise RuntimeError(f"Export failed: the file is missing or empty: {path}")
    return size


def _page_export_area(app, doc):
    """The active page as a rectangle in the document unit."""
    page = doc.ActivePage
    width, height = page.SizeWidth, page.SizeHeight
    if width <= 0 or height <= 0:
        return None
    return app.CreateRect(0, 0, width, height)


def _export_bitmap(conn, doc, path, filter_id, size_px, dpi, transparent, warnings, quality=None) -> str:
    """ExportBitmap of the active page (fallback: Document.Export, whose behaviour differs between versions).
    Returns the method used; a fallback records why in ``warnings``."""
    def bitmap():
        export_filter = doc.ExportBitmap(
            path, filter_id, _CDR_CURRENT_PAGE, _CDR_RGB_COLOR_IMAGE, size_px[0], size_px[1], dpi, dpi,
            _CDR_NORMAL_ANTIALIASING, False, transparent, True, False, _CDR_COMPRESSION_NONE, None,
            _page_export_area(conn.app, doc))
        if quality is not None:
            _attempt(warnings, "JPEG quality", lambda: setattr(export_filter, "JPEGQuality", quality))
        _finish_export_filter(export_filter)

    if _attempt(warnings, "ExportBitmap failed, used Export", bitmap):
        return "ExportBitmap"
    doc.Export(path, filter_id, _CDR_CURRENT_PAGE, None, None)
    return "Export"


def _pixels(page_mm, dpi):
    width_mm, height_mm = page_mm
    if width_mm > 0 and height_mm > 0:
        return max(1, int(width_mm / 25.4 * dpi)), max(1, int(height_mm / 25.4 * dpi))
    return 0, 0


def _active(conn):
    doc = conn.app.ActiveDocument
    if not doc:
        raise RuntimeError("CorelDRAW has no active document; open one with open_document")
    return doc


def _done(result, message, document_path):
    return respond(result, message, document_path=document_path)


def export_pdf(
    document_path: str,
    path: str,
    color_profile: str = "ISO_Coated_v2",
    bleed: float = 3.0,
    crop_marks: bool = True,
    multi_page: bool = False,
    pdfx_version: str = "",
) -> ToolResult:
    """Export the active page (or all pages, multi_page) as a CMYK print PDF with bleed (mm), crop marks, an
    optional ICC profile name and PDF/X version ("PDFX1a", "PDFX3", "PDFX4"). document_path must be active.
    For a separation proof use export_page_proof_pdf."""
    conn = get_connection()

    def _export():
        doc = _active(conn)
        _ensure_dir(path)
        warnings = []
        shapes_count = _prepare_current_page_export(doc, warnings)
        settings = doc.PDFSettings
        values = {"ColorMode": 1, "BleedingLimit": bleed, "PrintCropMarks": crop_marks, "MultiPage": multi_page,
                  "PublishRange": _CDR_ALL_PAGES if multi_page else _PDF_CURRENT_PAGE,
                  "PageRange": "" if multi_page else str(doc.ActivePage.Index), "TextAsCurves": True}
        for name, value in values.items():  # ColorMode 1 = cdrPDFCMYK
            _attempt(warnings, f"PDF setting {name}", lambda name=name, value=value: setattr(settings, name, value))
        if pdfx_version:
            _attempt(warnings, "PDF/X output", lambda: setattr(settings, "OutputAsPDFX", True))
            if not _attempt(warnings, "PDF/X version by name", lambda: setattr(settings, "PDFXVersion", pdfx_version)):
                _attempt(warnings, "PDF/X version by number",
                         lambda: setattr(settings, "PDFXVersion", _PDFX_VERSIONS.get(pdfx_version, 0)))
        if color_profile:
            # The ICC profile property is named differently across CorelDRAW versions.
            errors = []
            if not any(_attempt(errors, attr, lambda attr=attr: setattr(settings, attr, color_profile))
                       for attr in ("ColorProfileName", "ColorProfile", "OutputColorProfile", "ICCProfileName")):
                warnings.append(f"color_profile {color_profile!r} was not applied: {'; '.join(errors)}")
        doc.PublishToPDF(path)
        data = {"path": path, "color_profile": color_profile, "bleed": bleed, "crop_marks": crop_marks,
                "pdfx_version": pdfx_version, "shapes_count": shapes_count, "file_size": _verify_exported(path)}
        return {**data, "warnings": warnings} if warnings else data

    return _done(call_on_active(conn, document_path, _export), f"Exported PDF: {path}", document_path)


def export_dxf(document_path: str, path: str, version: str = "R14", layer_filter: str = "",
               export_hidden: bool = False) -> ToolResult:
    """Export the active page as DXF (laser cutting/engraving; text as curves). version R12, R14, R2000 or
    R2004; layer_filter: comma-separated layer names. document_path must be active."""
    conn = get_connection()
    dxf_ver = _DXF_VERSION.get(version, 1)

    def _export():
        doc = _active(conn)
        _ensure_dir(path)
        warnings = []
        shapes_count = _prepare_current_page_export(doc, warnings)

        def export_ex():
            # ExportEx (newer CorelDRAW); X6 often rejects it, so Document.Export is the fallback.
            export_filter = doc.ExportEx(path, _CDR_DXF, _CDR_CURRENT_PAGE, None, None)
            for attr, value in [("BitmapType", 0), ("TextAsCurves", True), ("Version", dxf_ver), ("Units", 3),
                                ("FillUnmapped", True)]:
                _attempt(warnings, f"DXF {attr}", lambda attr=attr, value=value: setattr(export_filter, attr, value))
            names = [name.strip() for name in layer_filter.split(",") if name.strip()]
            if names:
                _attempt(warnings, "DXF layer_filter",
                         lambda: setattr(export_filter, "LayerFilter", ",".join(names)))
            _finish_export_filter(export_filter)

        if not _attempt(warnings, "ExportEx failed, used Export (no version or layer filter)", export_ex):
            doc.Export(path, _CDR_DXF, _CDR_CURRENT_PAGE, None, None)
        data = {"path": path, "version": version, "text_as_curves": True, "shapes_count": shapes_count,
                "file_size": _verify_exported(path)}
        return {**data, "warnings": warnings} if warnings else data

    return _done(call_on_active(conn, document_path, _export), f"Exported DXF: {path} ({version})", document_path)


def _export_vector(document_path, path, filter_id, label):
    conn = get_connection()

    def _export():
        doc = _active(conn)
        _ensure_dir(path)
        warnings = []
        shapes_count = _prepare_current_page_export(doc, warnings)
        doc.Export(path, filter_id, _CDR_CURRENT_PAGE, None, None)
        data = {"path": path, "format": label, "shapes_count": shapes_count, "file_size": _verify_exported(path)}
        return {**data, "warnings": warnings} if warnings else data

    return _done(call_on_active(conn, document_path, _export), f"Exported {label.upper()}: {path}", document_path)


def export_ai(document_path: str, path: str) -> ToolResult:
    """Export the active page as Adobe Illustrator (.ai). document_path must be active."""
    return _export_vector(document_path, path, _CDR_AI, "ai")


def export_svg(document_path: str, path: str) -> ToolResult:
    """Export the active page as SVG. document_path must be active."""
    return _export_vector(document_path, path, _CDR_SVG, "svg")


def export_png(document_path: str, path: str, dpi: int = 300, width: int = 0,
               background_transparent: bool = False) -> ToolResult:
    """Export the active page as PNG at dpi, or at width pixels; optional transparent background.
    document_path must be active."""
    conn = get_connection()

    def _export():
        doc = _active(conn)
        _ensure_dir(path)
        warnings = []
        shapes_count = _prepare_current_page_export(doc, warnings)
        page_mm = _page_size_mm(doc)
        export_dpi = int(width / (page_mm[0] / 25.4)) if width > 0 and page_mm[0] > 0 else dpi
        export_dpi = max(72, export_dpi)
        size_px = _pixels(page_mm, export_dpi)
        method = _export_bitmap(conn, doc, path, _CDR_PNG, size_px, export_dpi, background_transparent, warnings)
        data = {"path": path, "dpi": export_dpi, "width_px": size_px[0], "height_px": size_px[1], "format": "png",
                "method": method, "shapes_count": shapes_count, "file_size": _verify_exported(path)}
        return {**data, "warnings": warnings} if warnings else data

    return _done(call_on_active(conn, document_path, _export), f"Exported PNG: {path}", document_path)


def export_jpeg(document_path: str, path: str, quality: int = 85, dpi: int = 300) -> ToolResult:
    """Export the active page as JPEG at dpi with quality 0-100. document_path must be active."""
    conn = get_connection()

    def _export():
        doc = _active(conn)
        _ensure_dir(path)
        warnings = []
        shapes_count = _prepare_current_page_export(doc, warnings)
        size_px = _pixels(_page_size_mm(doc), dpi)
        method = _export_bitmap(conn, doc, path, _CDR_JPEG, size_px, dpi, False, warnings, quality=quality)
        data = {"path": path, "quality": quality, "dpi": dpi, "width_px": size_px[0], "height_px": size_px[1],
                "format": "jpeg", "method": method, "shapes_count": shapes_count, "file_size": _verify_exported(path)}
        return {**data, "warnings": warnings} if warnings else data

    return _done(call_on_active(conn, document_path, _export), f"Exported JPEG: {path}", document_path)


def export_preview_png(document_path: str, path: str, width: int = 800) -> ToolResult:
    """Export a low-resolution PNG preview of the active page about width pixels wide (72-300 dpi), for a
    visual check. document_path must be active."""
    conn = get_connection()

    def _export():
        doc = _active(conn)
        _ensure_dir(path)
        warnings = []
        shapes_count = _prepare_current_page_export(doc, warnings)
        page_mm = _page_size_mm(doc)
        export_dpi = int(width / (page_mm[0] / 25.4)) if page_mm[0] > 0 else 72
        export_dpi = max(72, min(export_dpi, 300))
        size_px = _pixels(page_mm, export_dpi)
        if size_px == (0, 0):
            size_px = (width, 0)
        method = _export_bitmap(conn, doc, path, _CDR_PNG, size_px, export_dpi, False, warnings)
        data = {"path": path, "width_px": width, "actual_width_px": size_px[0], "actual_height_px": size_px[1],
                "dpi": export_dpi, "method": method, "shapes_count": shapes_count, "file_size": _verify_exported(path)}
        return {**data, "warnings": warnings} if warnings else data

    return _done(call_on_active(conn, document_path, _export), f"Exported preview: {path}", document_path)


def batch_export(document_path: str, pages: str, format: str, output_dir: str) -> ToolResult:
    """Export pages ("1,2,3", "1-5" or "all") of the active document as pdf, dxf, png (150 dpi) or svg into
    output_dir, one file per page; each page reports success or its error. document_path must be active."""
    conn = get_connection()
    format = format.lower()

    def _batch():
        doc = _active(conn)
        _ensure_dir(os.path.join(output_dir, "dummy.txt"))
        all_pages = list(doc.Pages)
        if pages.strip().lower() == "all":
            selected_pages = all_pages
        elif "-" in pages:
            start, end = [int(p.strip()) for p in pages.split("-")]
            selected_pages = [p for p in all_pages if start <= p.Index <= end]
        else:
            indices = [int(p.strip()) for p in pages.split(",") if p.strip()]
            selected_pages = [p for p in all_pages if p.Index in indices]

        results = []
        base = os.path.splitext(doc.FileName)[0] if doc.FileName else "output"
        for page in selected_pages:
            page.Activate()
            out_path = os.path.join(output_dir, f"{base}_p{page.Index}.{format}")
            warnings = []
            try:
                _prepare_current_page_export(doc, warnings)
                if format == "pdf":
                    doc.PDFSettings.PublishRange = _PDF_CURRENT_PAGE
                    doc.PDFSettings.PageRange = str(page.Index)
                    doc.PublishToPDF(out_path)
                elif format == "dxf":
                    def export_ex():
                        export_filter = doc.ExportEx(out_path, _CDR_DXF, _CDR_CURRENT_PAGE, None, None)
                        export_filter.BitmapType = 0
                        export_filter.TextAsCurves = True
                        export_filter.Version = 1
                        export_filter.Units = 3
                        _finish_export_filter(export_filter)

                    if not _attempt(warnings, "ExportEx failed, used Export", export_ex):
                        doc.Export(out_path, _CDR_DXF, _CDR_CURRENT_PAGE, None, None)
                elif format == "png":
                    size_px = _pixels(_page_size_mm(doc), 150)
                    export_filter = doc.ExportBitmap(
                        out_path, _CDR_PNG, _CDR_CURRENT_PAGE, _CDR_RGB_COLOR_IMAGE, size_px[0], size_px[1], 150,
                        150, _CDR_NORMAL_ANTIALIASING, False, False, True, False, _CDR_COMPRESSION_NONE, None,
                        _page_export_area(conn.app, doc))
                    _finish_export_filter(export_filter)
                elif format == "svg":
                    doc.Export(out_path, _CDR_SVG, _CDR_CURRENT_PAGE, None, None)
                else:
                    raise ValueError(f"Unsupported format {format!r}; use pdf, dxf, png or svg")
                _verify_exported(out_path)
                entry = {"page": page.Index, "path": out_path, "status": "success"}
            except Exception as exc:
                entry = {"page": page.Index, "path": out_path, "status": "failed", "error": error_text(exc)}
            if warnings:
                entry["warnings"] = warnings
            results.append(entry)
        return {"format": format, "total": len(results), "results": results}

    result = call_on_active(conn, document_path, _batch)
    if result["success"]:
        data = result["result"]
        succeeded = sum(1 for entry in data["results"] if entry["status"] == "success")
        return ToolResult.ok(f"Batch export: {succeeded} of {data['total']} pages exported", **data)
    return _done(result, None, document_path)
