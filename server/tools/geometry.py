"""Exact geometry operations scoped to a saved document and page."""

import math

from core.models import ToolResult
from tools.document import _UNIT_MAP
from tools.inspection import _on_shape
from tools.shapes import _static_id

_ANCHORS = {
    "top_right": (1, "RightX", "TopY"),
    "top_center": (2, "CenterX", "TopY"),
    "top_left": (3, "LeftX", "TopY"),
    "middle_left": (4, "LeftX", "CenterY"),
    "bottom_left": (5, "LeftX", "BottomY"),
    "bottom_center": (6, "CenterX", "BottomY"),
    "bottom_right": (7, "RightX", "BottomY"),
    "middle_right": (8, "RightX", "CenterY"),
    "center": (9, "CenterX", "CenterY"),
}
_UNITS = {unit: _UNIT_MAP[unit] for unit in ("mm", "cm", "inch", "pt")}
_CDR_TENTH_MICRON = 0


def set_shape_position_exact(
    document_path: str, page_index: int, shape_id: str, anchor: str, unit: str,
    x: float | None = None, y: float | None = None,
) -> ToolResult:
    """Move an exact shape to absolute page coordinates and read its anchor position back.

    Use mm, cm, inch, or pt. Anchors: center, top_left, top_center, top_right,
    middle_left, middle_right, bottom_left, bottom_center, bottom_right.
    An omitted coordinate preserves that axis. The requested page must be active.
    """
    if anchor not in _ANCHORS or unit not in _UNITS:
        return ToolResult.fail("Specify a supported anchor and unit (mm, cm, inch, or pt)")
    if (x is None and y is None) or any(value is not None and not math.isfinite(value) for value in (x, y)):
        return ToolResult.fail("Provide at least one finite coordinate")

    def move(app, doc, shape):
        if doc.ActivePage.Index != page_index:
            raise ValueError("The requested page must be active to set absolute page coordinates")
        reference, horizontal, vertical = _ANCHORS[anchor]
        doc_unit = doc.Unit
        source_unit = _UNITS[unit]
        target_x = getattr(shape, horizontal) if x is None else app.ConvertUnits(x, source_unit, doc_unit)
        target_y = getattr(shape, vertical) if y is None else app.ConvertUnits(y, source_unit, doc_unit)
        shape.SetPositionEx(reference, target_x, target_y)
        actual_x, actual_y = getattr(shape, horizontal), getattr(shape, vertical)
        tolerance = app.ConvertUnits(1, _CDR_TENTH_MICRON, doc_unit)
        data = {
            "anchor": anchor, "unit": unit,
            "x": app.ConvertUnits(actual_x, doc_unit, _UNITS[unit]),
            "y": app.ConvertUnits(actual_y, doc_unit, _UNITS[unit]),
        }
        if abs(actual_x - target_x) > tolerance or abs(actual_y - target_y) > tolerance:
            data["verification_error"] = "The actual anchor position differs from the requested position"
        return data

    return _on_shape(document_path, page_index, shape_id, move, write=True)


def duplicate_shape_by_id(
    document_path: str, page_index: int, shape_id: str, unit: str, dx: float = 0, dy: float = 0,
) -> ToolResult:
    """Duplicate one exact shape, offset by dx/dy in mm, cm, inch, or pt, and return its new ID."""
    if unit not in _UNITS or not all(math.isfinite(value) for value in (dx, dy)):
        return ToolResult.fail("Specify mm, cm, inch, or pt and finite offsets")

    def duplicate(app, doc, shape):
        horizontal = app.ConvertUnits(dx, _UNITS[unit], doc.Unit)
        vertical = app.ConvertUnits(dy, _UNITS[unit], doc.Unit)
        copy = shape.Duplicate(horizontal, vertical)
        duplicate_id = _static_id(copy)
        data = {"duplicate_shape_id": duplicate_id, "layer_name": copy.Layer.Name}
        if duplicate_id == shape_id or copy.Layer.Page.Index != page_index:
            data["verification_error"] = "CorelDRAW did not return a distinct duplicate on the requested page"
        return data

    return _on_shape(document_path, page_index, shape_id, duplicate, write=True)
