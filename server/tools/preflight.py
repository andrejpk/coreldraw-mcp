"""Preflight checks on the active page: page size, paragraph text overflow and missing fonts."""

from core.connection import error_text, get_connection
from core.drawing import CDR_TEXT_SHAPE
from core.models import ToolResult
from core.results import respond
from core.targets import call_on_active
from core.units import MILLIMETER, convert

_CDR_PARAGRAPH_TEXT = 1


def _active(conn):
    doc = conn.app.ActiveDocument
    if not doc:
        raise RuntimeError("CorelDRAW has no active document; open one with open_document")
    return doc


def _overflows(text):
    """Whether paragraph text overflows its frame (the property name differs between versions)."""
    try:
        return bool(text.Overflows)
    except Exception:
        return bool(text.IsOverflowing)


def check_dimensions(expected_width: float, expected_height: float, tolerance: float = 0.5,
                     document_path: str = "") -> ToolResult:
    """Check the active page size against expected_width x expected_height within tolerance (all mm).
    Reads the active document (document_path, if given, must be active)."""
    conn = get_connection()

    def _check():
        doc = _active(conn)
        page = doc.ActivePage
        actual_w = convert(page.SizeWidth, doc.Unit, MILLIMETER, conn.app)
        actual_h = convert(page.SizeHeight, doc.Unit, MILLIMETER, conn.app)
        w_diff = abs(actual_w - expected_width)
        h_diff = abs(actual_h - expected_height)
        return {
            "passed": w_diff <= tolerance and h_diff <= tolerance,
            "expected": {"width": expected_width, "height": expected_height},
            "actual": {"width": round(actual_w, 2), "height": round(actual_h, 2)},
            "diff": {"width": round(w_diff, 2), "height": round(h_diff, 2)},
            "tolerance": tolerance,
            "unit": "mm",
        }

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        passed = result["result"]["passed"]
        return ToolResult.ok("Page size within tolerance" if passed else "Page size out of tolerance",
                             **result["result"])
    return respond(result, document_path=document_path)


def check_text_overflow_all(document_path: str = "") -> ToolResult:
    """List paragraph text on the active page that overflows its frame (top-level shapes). Reads the active
    document (document_path, if given, must be active)."""
    conn = get_connection()

    def _check():
        page = _active(conn).ActivePage
        overflow_items, unreadable = [], []
        total_text = 0
        for shape in page.Shapes:
            if shape.Type != CDR_TEXT_SHAPE:
                continue
            total_text += 1
            try:
                if shape.Text.Type == _CDR_PARAGRAPH_TEXT and _overflows(shape.Text):
                    content = shape.Text.Story.Text
                    overflow_items.append({"name": shape.Name, "shape_id": str(shape.StaticID),
                                           "content_preview": content[:50]})
            except Exception as exc:
                unreadable.append({"shape_id": str(shape.StaticID), "error": error_text(exc)})
        data = {"passed": not overflow_items and not unreadable, "total_text_shapes": total_text,
                "overflow_count": len(overflow_items), "overflow_items": overflow_items}
        return {**data, "unreadable": unreadable} if unreadable else data

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        r = result["result"]
        message = (f"No overflow in {r['total_text_shapes']} text shapes" if r["passed"]
                   else f"{r['overflow_count']} text shapes overflow")
        if r.get("unreadable"):
            message += f"; {len(r['unreadable'])} could not be checked (see unreadable)"
        return ToolResult.ok(message, **r)
    return respond(result, document_path=document_path)


def check_missing_fonts(document_path: str = "") -> ToolResult:
    """List fonts used by text on the active page that are not installed. Reads the active document
    (document_path, if given, must be active)."""
    conn = get_connection()

    def _check():
        doc = _active(conn)
        warnings = []
        # The installed font list is the reliable source; without it, test each font with a temporary shape.
        available_fonts: set[str] = set()
        try:
            font_col = conn.app.Fonts
            available_fonts = {font_col.Item(i).Name.lower() for i in range(1, font_col.Count + 1)}
        except Exception as exc:
            warnings.append(f"installed font list unavailable ({error_text(exc)}); fonts were tested on a "
                            "temporary text shape")
        use_font_list = bool(available_fonts)

        missing_fonts: set[str] = set()
        page = doc.ActivePage
        for shape in page.Shapes:
            if shape.Type != CDR_TEXT_SHAPE:
                continue
            # Character-level fonts first, the shape's font as the fallback.
            shape_fonts: set[str] = set()
            try:
                chars = shape.Text.Characters
                shape_fonts = {chars.Item(ci).Properties.Font for ci in range(1, chars.Count + 1)}
            except Exception as exc:
                warnings.append(f"shape {shape.StaticID}: character fonts unreadable ({error_text(exc)})")
            if not shape_fonts:
                try:
                    shape_fonts.add(shape.Text.FontProperties.Name)
                except Exception as exc:
                    warnings.append(f"shape {shape.StaticID}: font unreadable ({error_text(exc)})")
            for font_name in filter(None, shape_fonts):
                if use_font_list:
                    if font_name.lower() not in available_fonts:
                        missing_fonts.add(font_name)
                    continue
                # Create a test shape and read back the font: CorelDRAW silently substitutes missing fonts.
                try:
                    test = page.ActiveLayer.CreateArtisticText(0, 0, "T", Font=font_name)
                    actual = test.Text.FontProperties.Name
                    test.Delete()
                    if actual.lower() != font_name.lower():
                        missing_fonts.add(font_name)
                except Exception:
                    missing_fonts.add(font_name)

        missing_list = sorted(missing_fonts)
        data = {"passed": not missing_list, "missing_fonts": missing_list, "count": len(missing_list)}
        return {**data, "warnings": warnings} if warnings else data

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        r = result["result"]
        message = "All fonts are installed" if r["passed"] else f"{r['count']} fonts are missing"
        return ToolResult.ok(message, **r)
    return respond(result, document_path=document_path)
