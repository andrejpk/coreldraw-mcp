"""Exact geometry operations scoped to a saved document and page."""

import math

from core.drawing import CDR_CURVE_SHAPE, CDR_POLYGON_SHAPE, CDR_TEXT_SHAPE, walk_shapes
from core.models import ToolResult
from tools.document import _UNIT_MAP
from tools.inspection import _exact_shapes, _on_shape, _on_shapes
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


def _cleanup_copies(copies):
    failed = []
    for shape_id, shape in copies.items():
        try:
            shape.Delete()
        except Exception:
            failed.append(shape_id)
    return failed


def duplicate_shapes_by_id(
    document_path: str, page_index: int, shape_ids: list[str], unit: str, dx: float = 0, dy: float = 0,
) -> ToolResult:
    """Duplicate the exact shapes in one undo group and return a source-to-copy ID mapping."""
    if unit not in _UNITS or not all(math.isfinite(value) for value in (dx, dy)):
        return ToolResult.fail("Specify mm, cm, inch, or pt and finite offsets")

    def duplicate(app, doc, shapes):
        horizontal = app.ConvertUnits(dx, _UNITS[unit], doc.Unit)
        vertical = app.ConvertUnits(dy, _UNITS[unit], doc.Unit)
        copies, mapping = {}, {}
        doc.BeginCommandGroup("Duplicate shapes")
        try:
            for source, shape in zip(shape_ids, shapes, strict=True):
                copy = shape.Duplicate(horizontal, vertical)
                new_id = _static_id(copy)
                if new_id in shape_ids or new_id in copies:
                    raise RuntimeError("CorelDRAW did not return a distinct duplicate ID")
                copies[new_id] = copy
                mapping[source] = new_id
                if copy.Layer.Page.Index != page_index:
                    raise RuntimeError("A duplicate landed on a different page")
        except Exception as exc:
            if remaining := _cleanup_copies(copies):
                raise RuntimeError(f"{exc}. Verify remaining copy IDs: {remaining}") from exc
            raise
        finally:
            doc.EndCommandGroup()
        return {"duplicates": mapping}

    return _on_shapes(document_path, page_index, shape_ids, duplicate, write=True)


def weld_copies_by_id(document_path: str, page_index: int, shape_ids: list[str]) -> ToolResult:
    """Weld private copies of at least two exact vector objects. Preserve the original objects.

    The first ID is the weld target. Return the welded result ID and its actual layer.
    The operation uses one undo group. Use the returned ID for further edits.
    """
    if len(shape_ids) < 2:
        return ToolResult.fail("Provide at least two unique source shape IDs")

    def weld(app, doc, shapes):
        if any(shape.Type not in (1, 2, CDR_CURVE_SHAPE, CDR_POLYGON_SHAPE, CDR_TEXT_SHAPE, 26) for shape in shapes):
            raise ValueError("Weld requires individual vector shapes or editable text. Inspect groups for child IDs.")
        live_copies = {}
        doc.BeginCommandGroup("Weld shape copies")
        try:
            result = None
            for shape in shapes:
                copy = shape.Duplicate(0, 0)
                copy_id = _static_id(copy)
                if copy_id in shape_ids or copy_id in live_copies:
                    raise RuntimeError("CorelDRAW did not return a distinct temporary copy ID")
                live_copies[copy_id] = copy
                if result is None:
                    result = copy
                    continue
                target_id = _static_id(result)
                welded = copy.Weld(result, False, False)
                if welded is None:
                    raise RuntimeError("CorelDRAW did not return a weld result")
                del live_copies[copy_id], live_copies[target_id]
                result = welded
                result_id = _static_id(result)
                if result_id in shape_ids:
                    raise RuntimeError("Cannot identify a new weld result")
                live_copies[result_id] = result
            result_id = _static_id(result)
            _exact_shapes(doc.Pages(page_index), [*shape_ids, result_id])
            return {"source_shape_ids": shape_ids, "result_shape_id": result_id, "layer_name": result.Layer.Name}
        except Exception as exc:
            if remaining := _cleanup_copies(live_copies):
                raise RuntimeError(f"{exc}. Verify remaining copy IDs: {remaining}") from exc
            raise
        finally:
            doc.EndCommandGroup()

    return _on_shapes(document_path, page_index, shape_ids, weld, write=True)


def delete_shape_by_id(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """Delete the exact shape from the scoped page and verify that its ID is gone."""
    def delete(app, doc, shape):
        shape.Delete()
        if any(_static_id(item) == shape_id for layer in doc.Pages(page_index).AllLayers
               for item, _ in walk_shapes(layer.Shapes)):
            return {"verification_error": "The requested shape still exists after deletion"}
        return {"deleted": True}

    return _on_shape(document_path, page_index, shape_id, delete, write=True)
