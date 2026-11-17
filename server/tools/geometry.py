"""Exact geometry operations scoped to a saved document and page."""

import math

from core.drawing import CDR_CURVE_SHAPE, CDR_POLYGON_SHAPE, CDR_TEXT_SHAPE
from core.models import ToolResult
from core.structure import walk_layer
from core.units import unit_code
from tools.inspection import (
    _CDR_UNIFORM_FILL,
    _STRUCTURE_BATCH,
    _exact_shapes,
    _fill_details,
    _on_shape,
    _on_shapes,
    _outline_details,
    _query,
    _query_path,
)
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
_UNITS = {unit: unit_code(unit) for unit in ("mm", "cm", "in", "inch", "pt")}
_CDR_TENTH_MICRON = 0
_WELDABLE = (1, 2, CDR_CURVE_SHAPE, CDR_POLYGON_SHAPE, CDR_TEXT_SHAPE, 26)


def set_shape_position_exact(
    document_path: str, page_index: int, shape_id: str, anchor: str, unit: str,
    x: float | None = None, y: float | None = None,
) -> ToolResult:
    """Move an exact shape to absolute page coordinates and read its anchor position back.

    Use mm, cm, in (inch), or pt. Anchors: center, top_left, top_center, top_right,
    middle_left, middle_right, bottom_left, bottom_center, bottom_right.
    An omitted coordinate preserves that axis. The requested page must be active.
    """
    if anchor not in _ANCHORS or unit not in _UNITS:
        return ToolResult.fail("Specify a supported anchor and unit (mm, cm, in (inch), or pt)")
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
    """Duplicate one exact shape, offset by dx/dy in mm, cm, in (inch), or pt, and return its new ID."""
    if unit not in _UNITS or not all(math.isfinite(value) for value in (dx, dy)):
        return ToolResult.fail("Specify mm, cm, in (inch), or pt and finite offsets")

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
        return ToolResult.fail("Specify mm, cm, in (inch), or pt and finite offsets")

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
        if any(shape.Type not in _WELDABLE for shape in shapes):
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
            _exact_shapes(doc, doc.Pages(page_index), [*shape_ids, result_id])
            return {"source_shape_ids": shape_ids, "result_shape_id": result_id, "layer_name": result.Layer.Name}
        except Exception as exc:
            if remaining := _cleanup_copies(live_copies):
                raise RuntimeError(f"{exc}. Verify remaining copy IDs: {remaining}") from exc
            raise
        finally:
            doc.EndCommandGroup()

    return _on_shapes(document_path, page_index, shape_ids, weld, write=True)


def _top_level_index(layer, shape):
    """Return the shape's one-based stacking index if it is a direct child of the layer."""
    index = shape.ZOrder
    shapes = layer.Shapes
    if not 1 <= index <= shapes.Count or _static_id(shapes.Item(index)) != _static_id(shape):
        return None
    return index


def _neighbor_id(layer, index):
    shapes = layer.Shapes
    return _static_id(shapes.Item(index)) if 1 <= index <= shapes.Count else None


def _bounds(shape):
    return shape.LeftX, shape.BottomY, shape.RightX, shape.TopY


def _union(boxes):
    left, bottom, right, top = zip(*boxes, strict=True)
    return min(left), min(bottom), max(right), max(top)


def _paint(shape):
    """Native fill, outline, and overprint identity that welding must preserve."""
    return {
        "fill": _fill_details(shape), "outline": _outline_details(shape),
        "overprint_fill": bool(shape.OverprintFill), "overprint_outline": bool(shape.OverprintOutline),
    }


def weld_consecutive_shapes(document_path: str, page_index: int, shape_ids: list[str]) -> ToolResult:
    """Weld two or more exact, consecutively stacked shapes on one layer into one shape in their place.

    Every shape must be a direct child of the same editable layer on the page, adjacent in stacking
    order, without effects, and share one uniform fill (spot name and tint included), outline, and
    overprint flags. The result keeps that paint and the stacking position between the original
    neighbors. Returns the result ID, its neighbors, bounds, and area. The edit is one undo step.
    A failure before verification undoes the partial weld.
    """
    if len(shape_ids) < 2:
        return ToolResult.fail("Provide at least two unique shape IDs")

    def weld(app, doc, shapes):
        layer = shapes[0].Layer
        indices = [_top_level_index(layer, shape) for shape in shapes]
        if None in indices:
            raise ValueError("Each shape must be a direct child of the same layer. Ungroup or inspect parent IDs first.")
        ordered = sorted(zip(indices, shape_ids, shapes, strict=True), key=lambda item: item[0])
        top, bottom = ordered[0][0], ordered[-1][0]
        if bottom - top + 1 != len(ordered):
            raise ValueError(f"The shapes are not consecutive in stacking order (positions {sorted(indices)})")
        if any(shape.Type not in _WELDABLE for shape in shapes):
            raise ValueError("Weld requires individual vector shapes. Inspect groups for child IDs.")
        if any(shape.Effects.Count for shape in shapes):
            raise ValueError("Remove or separate effects before welding")
        paint = _paint(shapes[0])
        if paint["fill"]["type"] != _CDR_UNIFORM_FILL:
            raise ValueError("The shapes must have a uniform fill")
        color = shapes[0].Fill.UniformColor
        for shape_id, shape in zip(shape_ids[1:], shapes[1:], strict=True):
            if _paint(shape) != paint or not shape.Fill.UniformColor.IsSame(color):
                raise ValueError(f"Shape {shape_id} has a different fill, tint, outline, or overprint flag")
        above, below = _neighbor_id(layer, top - 1), _neighbor_id(layer, bottom + 1)
        expected_box = _union([_bounds(shape) for shape in shapes])
        areas = [shape.DisplayCurve.Area for shape in shapes]

        doc.BeginCommandGroup("Weld consecutive shapes")
        try:
            result = ordered[0][2]
            for _, _, shape in ordered[1:]:
                result = shape.Weld(result, False, False)
                if result is None:
                    raise RuntimeError("CorelDRAW did not return a weld result")
        except Exception as exc:
            doc.EndCommandGroup()
            if _surviving(layer, shape_ids) != set(shape_ids):
                doc.Undo(1)
                if _surviving(layer, shape_ids) != set(shape_ids):
                    raise RuntimeError(f"{exc}. Undo did not restore every source ID; inspect the layer") from exc
                raise RuntimeError(f"{exc}. The partial weld was undone") from exc
            raise
        doc.EndCommandGroup()

        result_id = _static_id(result)
        index = _top_level_index(layer, result)
        area = result.DisplayCurve.Area
        tolerance = app.ConvertUnits(1, _CDR_TENTH_MICRON, doc.Unit)
        data = {
            "source_shape_ids": shape_ids, "result_shape_id": result_id, "layer_name": layer.Name,
            "stack_index": index, "above_shape_id": above, "below_shape_id": below,
            "bounds": dict(zip(("left", "bottom", "right", "top"), _bounds(result), strict=True)),
            "area": area, "source_area_sum": sum(areas), "largest_source_area": max(areas),
            "paint": _paint(result),
        }
        problems = []
        if result_id in shape_ids or _surviving(layer, shape_ids):
            problems.append("source shapes remain")
        if index is None or _neighbor_id(layer, index - 1) != above or _neighbor_id(layer, index + 1) != below:
            problems.append("the stacking position changed")
        if data["paint"] != paint or not result.Fill.UniformColor.IsSame(color):
            problems.append("the paint changed")
        if any(abs(a - b) > tolerance for a, b in zip(_bounds(result), expected_box, strict=True)):
            problems.append("the bounds differ from the union of the sources")
        area_tolerance = 1e-6 * sum(areas) + tolerance * tolerance
        if not max(areas) - area_tolerance <= area <= sum(areas) + area_tolerance:
            problems.append("the area is outside the expected range")
        if problems:
            data["verification_error"] = f"Weld verification failed: {'; '.join(problems)}. Inspect or undo the last step."
        return data

    return _on_shapes(document_path, page_index, shape_ids, weld, write=True)


def _surviving(layer, shape_ids):
    """Return the IDs that still exist among the layer's direct children."""
    count = layer.Shapes.Count
    if not count:
        return set()
    anchor = layer.Shapes.Item(1)
    present = set()
    for start in range(1, count + 1, _STRUCTURE_BATCH):
        indices = range(start, min(count, start + _STRUCTURE_BATCH - 1) + 1)
        present.update(str(value) for value in _query(anchor, [f"{_query_path((index,))}.StaticID" for index in indices]))
    return present & set(shape_ids)


def delete_shape_by_id(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """Delete the exact shape from the scoped page and verify that its ID is gone."""
    def delete(app, doc, shape):
        shape.Delete()
        # A fresh batched walk of each layer (not the cache): seconds faster than one COM read per shape.
        if any(shape_id in walk_layer(layer).position for layer in doc.Pages(page_index).AllLayers):
            return {"verification_error": "The requested shape still exists after deletion"}
        return {"deleted": True}

    return _on_shape(document_path, page_index, shape_id, delete, write=True)
