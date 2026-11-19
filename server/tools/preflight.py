"""Preflight check on the active page: page size. Text overflow and missing fonts are in inspect_text."""

from core.connection import get_connection
from core.models import ToolResult
from core.results import respond
from core.targets import call_on_active
from core.units import MILLIMETER, convert


def _active(conn):
    doc = conn.app.ActiveDocument
    if not doc:
        raise RuntimeError("CorelDRAW has no active document; open one with open_document")
    return doc


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
