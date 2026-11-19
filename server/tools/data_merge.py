"""Data merge: read Excel/CSV records, fill named text shapes in templates, and place barcodes or QR codes."""

import os
import tempfile
from contextlib import suppress

from core.connection import error_text, get_connection
from core.drawing import CDR_TEXT_SHAPE
from core.models import ToolResult
from core.results import respond
from core.targets import call_on_active, private_document, save_as


def read_excel_data(path: str, sheet: int = 0, has_header: bool = True) -> ToolResult:
    """Read records from an .xlsx, .xls or .csv file; sheet is a zero-based index. Returns records and columns."""
    try:
        import pandas as pd
    except ImportError:
        return ToolResult.fail("pandas is not installed: pip install pandas openpyxl xlrd")

    if not os.path.isfile(path):
        return ToolResult.fail(f"File not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
        elif ext == ".xls":
            df = pd.read_excel(path, sheet_name=sheet, engine="xlrd")
        elif ext == ".csv":
            df = pd.read_csv(path)
        else:
            return ToolResult.fail(f"Unsupported file type {ext}; use .xlsx, .xls or .csv")
    except Exception as exc:
        return ToolResult.fail(f"Could not read {path}: {exc}")

    if not has_header:
        df.columns = [f"col_{i}" for i in range(len(df.columns))]
    # Empty cells become "", other non-JSON values strings.
    records = [{key: "" if pd.isna(value) else value if isinstance(value, (int, float, bool, str)) else str(value)
                for key, value in row.items()} for row in df.to_dict(orient="records")]
    return ToolResult.ok(f"Read {len(records)} records", path=path, sheet=sheet, total=len(records),
                         records=records, columns=list(df.columns))


def merge_record(template_path: str, data: dict, output_path: str) -> ToolResult:
    """Open a .cdr template privately, set each text shape named like a data key (active page, top level) to
    its value, and save it as output_path. Returns replaced, not_found and failed keys."""
    conn = get_connection()

    def _merge():
        with private_document(conn.app, template_path) as doc:
            shapes = {shape.Name: shape for shape in doc.ActivePage.Shapes if shape.Type == CDR_TEXT_SHAPE}
            replaced, failed = {}, {}
            for key, value in data.items():
                if key not in shapes:
                    continue
                try:
                    shapes[key].Text.Story.Text = str(value)
                    replaced[key] = str(value)[:30]
                except Exception as exc:
                    failed[key] = error_text(exc)
            dirpath = os.path.dirname(output_path)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)
            save_as(conn.app, doc, output_path)
        result = {"template": template_path, "output": output_path, "replaced": replaced,
                  "not_found": [key for key in data if key not in replaced and key not in failed]}
        return {**result, "failed": failed} if failed else result

    result = conn.safe_call(_merge)
    if result["success"]:
        return ToolResult.ok(f"Merged {len(result['result']['replaced'])} fields into {output_path}",
                             **result["result"])
    return respond(result)


def batch_merge(template_path: str, data_path: str, output_dir: str, naming_pattern: str = "{room}") -> ToolResult:
    """Run merge_record for every record of an Excel/CSV file into output_dir; naming_pattern names each file
    from columns ("{room}"). Returns per-record success or error."""
    read_result = read_excel_data(data_path)
    if not read_result.success:
        return read_result

    records = read_result.data["records"]
    os.makedirs(output_dir, exist_ok=True)
    results = []
    for i, record in enumerate(records):
        filename = naming_pattern
        for key, value in record.items():
            filename = filename.replace("{" + key + "}", str(value))
        if "{" in filename:
            filename = f"output_{i:03d}"  # the pattern names a column the data does not have
        merge_result = merge_record(template_path, record, os.path.join(output_dir, f"{filename}.cdr"))
        results.append({"index": i, "filename": f"{filename}.cdr", "success": merge_result.success,
                        "error": merge_result.error or ""})

    succeeded = sum(1 for r in results if r["success"])
    return ToolResult.ok(f"Merged {succeeded} of {len(results)} records", output_dir=output_dir,
                         total=len(results), succeeded=succeeded, failed=len(results) - succeeded, results=results)


def _place_svg(conn, svg_path, x, y, width, height, name):
    """Import an SVG into the active document and place the new top shape at x, y (document units)."""
    doc = conn.app.ActiveDocument
    doc.Import(svg_path)
    shapes = doc.ActivePage.Shapes
    if shapes.Count == 0:
        raise RuntimeError("The SVG import added no shape")
    shape = shapes.Last
    shape.SetPosition(x, y)
    shape.SetSize(width, height)
    shape.Name = name
    return shape


def generate_barcode(document_path: str, barcode_type: str, data: str, x: float, y: float, width: float,
                     height: float) -> ToolResult:
    """Generate a code128, ean13 or code39 barcode and place it on the active page at x, y with width, height
    (document units). document_path must be active."""
    try:
        import barcode
        from barcode.writer import SVGWriter
    except ImportError:
        return ToolResult.fail("python-barcode is not installed: pip install python-barcode")

    conn = get_connection()

    def _generate():
        if barcode_type not in ("code128", "ean13", "code39"):
            raise ValueError(f"Unsupported barcode_type {barcode_type!r}; use code128, ean13 or code39")
        code = barcode.get_barcode_class(barcode_type)(data, writer=SVGWriter())
        with tempfile.TemporaryDirectory() as folder:
            svg_path = code.save(os.path.join(folder, "barcode"))
            if not os.path.exists(svg_path):
                raise RuntimeError("The barcode SVG was not generated")
            shape = _place_svg(conn, svg_path, x, y, width, height, f"barcode_{barcode_type}_{data}")
        return {"shape_id": str(shape.StaticID), "type": barcode_type, "data": data, "x": x, "y": y,
                "width": width, "height": height}

    return respond(call_on_active(conn, document_path, _generate), f"Placed a {barcode_type} barcode",
                   document_path=document_path)


def generate_qrcode(document_path: str, data: str, x: float, y: float, size: float) -> ToolResult:
    """Generate a QR code for data and place it on the active page at x, y, size x size (document units).
    document_path must be active."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ToolResult.fail("qrcode is not installed: pip install qrcode[pil]")

    conn = get_connection()

    def _generate():
        qr = qrcode.QRCode(version=None, box_size=10, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        image = qr.make_image(image_factory=qrcode.image.svg.SvgImage)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
            svg_path = tmp.name
        try:
            image.save(svg_path)
            shape = _place_svg(conn, svg_path, x, y, size, size, f"qrcode_{data[:20]}")
        finally:
            with suppress(OSError):  # a temporary file; nothing to report if it is already gone
                os.remove(svg_path)
        return {"shape_id": str(shape.StaticID), "type": "qr", "data": data, "x": x, "y": y, "size": size}

    return respond(call_on_active(conn, document_path, _generate), "Placed a QR code", document_path=document_path)
