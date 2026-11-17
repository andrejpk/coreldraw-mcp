"""Exact geometry edits on one page: move, duplicate, delete, convert to curves, and boolean combinations.

Every edit activates its document, refuses unsupported targets before changing anything, is one undo step
(a failure part way is undone, ``core.shapeops.undoable``), drops the structure cache, and returns verified
after-state (IDs, layers, bounds and areas in the document unit).
"""

import math
from typing import Literal

from core.models import ToolResult
from core.shapeops import (
    BITMAP,
    VECTOR_TYPES,
    geometry,
    page_ids,
    refusal,
    rounded,
    target_layer,
    undoable,
)
from core.structure import invalidate, locate_entries, walk_layer
from core.units import MEASUREMENT_UNITS, convert, unit_code
from tools.colors import _com_shape, _edit, _read, _RefusedError, _states
from tools.inspection import _on_shape

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
_BOUNDS = "array($item.LeftX, $item.BottomY, $item.RightX, $item.TopY)"
_TOLERANCE_PT = 0.01  # bounds and offsets (CorelDRAW stores coordinates in tenths of a micron)
Unit = Literal["pt", "mm", "in", "cm"]


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


def delete_shape_by_id(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """Delete the exact shape from the scoped page and verify that its ID is gone."""
    def delete(app, doc, shape):
        shape.Delete()
        # A fresh batched walk of each layer (not the cache): seconds faster than one COM read per shape.
        if any(shape_id in walk_layer(layer).position for layer in doc.Pages(page_index).AllLayers):
            return {"verification_error": "The requested shape still exists after deletion"}
        return {"deleted": True}

    return _on_shape(document_path, page_index, shape_id, delete, write=True)


# --- shared --------------------------------------------------------------------------------------------------

def _tolerance(unit):
    return convert(_TOLERANCE_PT, "pt", unit)


def _layer_for(page, name):
    return target_layer(page, name) if name is not None else None


def _located(doc, page, ids):
    invalidate()
    return locate_entries(doc, page, ids) if ids else {}


def _geometry(shape, unit):
    """geometry() or None when the shape has no readable display curve (text before conversion)."""
    try:
        return geometry(shape, unit)
    except Exception:
        return None


def _results(doc, page, ids, unit, paint=True):
    """Verified records for new shapes: layer, type, bounds, geometry and (optionally) paint."""
    located = _located(doc, page, ids)
    states = _states(located, unit, ("fill", "outline", "flags")) if paint and located else {}
    records = []
    for shape_id, (view, entry) in located.items():
        record = {"id": shape_id, "layer": view.name, "type": entry.type}
        if entry.type in VECTOR_TYPES and (measured := _geometry(_com_shape(view, entry, shape_id), unit)):
            record.update(rounded(measured))
        if shape_id in states:
            state = states[shape_id]
            record.update({key: state[key] for key in ("fill", "outline", "overprint") if key in state})
        records.append(record)
    return records


# --- duplicate_shapes -----------------------------------------------------------------------------------------

def duplicate_shapes(
    document_path: str, page_index: int, shape_ids: list[str], dx: float = 0.0, dy: float = 0.0,
    unit: Unit = "pt", target_layer: str | None = None,
) -> ToolResult:
    """Duplicate exact shapes (groups too), offset by dx/dy in unit, as one undo step.

    Copies go above their source, or on top of target_layer (editable layer of this page). Refuses effect
    groups, effect-generated and master-layer objects, and copies onto a locked layer. Returns new_ids in
    order with each copy's layer and verified bounds.
    """
    if unit not in MEASUREMENT_UNITS or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (dx, dy)):
        return ToolResult.fail(f"unit must be one of {', '.join(MEASUREMENT_UNITS)} and dx/dy finite numbers")

    def body(app, doc, page, targets):
        document_unit = doc.Unit
        layer = _layer_for(page, target_layer)

        def extra(shape_id, view, entry):
            reason = refusal(view, entry, consume=False, groups=True, bitmaps=True,
                             types=VECTOR_TYPES | {BITMAP})
            if reason is None and layer is None and not view.editable:
                reason = "on a locked layer (the copy would land there); pass target_layer"
            return reason

        _refuse(targets, extra)
        before = _read(targets, [_BOUNDS])
        move_x, move_y = (convert(value, unit, document_unit) for value in (dx, dy))
        # Resolve every source first: each copy shifts the stacking indices of the shapes below it.
        resolved = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in targets.items()}
        copies = []
        with undoable(doc, page, "MCP duplicate_shapes") as step:
            for source in resolved.values():
                if layer is not None:
                    copy = source.CopyToLayer(layer)
                    if move_x or move_y:
                        copy.Move(move_x, move_y)
                else:
                    copy = source.Duplicate(move_x, move_y)
                new_id = str(copy.StaticID)
                if new_id in step.before or new_id in copies:
                    raise RuntimeError("CorelDRAW did not return a distinct copy")
                copies.append(new_id)
        located = _located(doc, page, copies)
        after = _read(located, [_BOUNDS])
        tolerance = _tolerance(document_unit)
        records, problems = [], []
        for source, copy in zip(shape_ids, copies, strict=True):
            view, _ = located[copy]
            old, new = before[source][0], after[copy][0]
            records.append({"source": source, "id": copy, "layer": view.name, "bounds": [round(v, 6) for v in new]})
            shift = (new[0] - old[0], new[1] - old[1], new[2] - old[2], new[3] - old[3])
            if any(abs(s - want) > tolerance for s, want in zip(shift, (move_x, move_y, move_x, move_y), strict=True)):
                problems.append({"id": copy, "problem": "offset differs", "shift": [round(s, 6) for s in shift]})
            if target_layer is not None and view.name != target_layer:
                problems.append({"id": copy, "problem": f"landed on {view.name!r}"})
        if missing := set(shape_ids) - page_ids(doc, page):
            problems.append({"problem": f"sources missing after duplicating: {sorted(missing)}"})
        data = {"new_ids": copies, "copies": records}
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _refuse(targets, extra):
    """Refuse (changing nothing) every target for which ``extra`` gives a reason."""
    refused = [{"id": shape_id, "layer": view.name, "reason": reason}
               for shape_id, (view, entry) in targets.items() if (reason := extra(shape_id, view, entry))]
    if refused:
        raise _RefusedError(refused)


# --- convert_to_curves -----------------------------------------------------------------------------------------

def convert_to_curves(document_path: str, page_index: int, shape_ids: list[str]) -> ToolResult:
    """Convert exact shapes (text, rectangles, ellipses, polygons) to curves, as one undo step.

    Curves are left as they are. Refuses groups, bitmaps, effect-generated objects, locked or master
    layers. Returns each shape's type before and after and its ID afterwards.
    """
    def body(app, doc, page, targets):
        _refuse(targets, lambda shape_id, view, entry: refusal(view, entry, consume=True))
        todo = {shape_id: target for shape_id, target in targets.items() if target[1].type != 3}
        converted = {}
        shapes = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in todo.items()}
        with undoable(doc, page, "MCP convert_to_curves") as step:
            for shape_id, shape in shapes.items():
                shape.ConvertToCurves()
                converted[shape_id] = str(shape.StaticID)
        after_ids = page_ids(doc, page)
        located = _located(doc, page, [i for i in converted.values() if i in after_ids])
        records, problems = [], []
        new = sorted(after_ids - step.before, key=int)
        for shape_id, (view, entry) in targets.items():
            record = {"id": shape_id, "type_before": entry.type}
            if shape_id not in todo:
                record.update(type_after=entry.type, id_after=shape_id, already_curve=True)
            else:
                id_after = converted[shape_id]
                if id_after in located:
                    record.update(id_after=id_after, type_after=located[id_after][1].type)
                else:
                    record.update(id_after=None, new_ids=new)
                if record.get("type_after") not in (3, 7):
                    problems.append({"id": shape_id, "type_after": record.get("type_after")})
            records.append(record)
        data = {"shapes": records}
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


# --- combine_shapes ----------------------------------------------------------------------------------------------

Operation = Literal["weld", "trim", "intersect", "combine", "break_apart"]
_LABELS = {"weld": "Weld", "trim": "Trim", "intersect": "Intersect", "combine": "Combine", "break_apart": "Break apart"}


def combine_shapes(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    operation: Operation,
    keep_sources: bool = False,
    target_layer: str | None = None,
    preserve_order: bool = True,
) -> ToolResult:
    """Weld, trim, intersect, combine or break apart exact shapes on one page. One verified undo step.

    weld: union. With preserve_order the shapes must be adjacent direct children of one layer with identical
    paint and no effects; the result keeps their place. Otherwise everything is welded into the first ID.
    trim: the first ID is cut by the others. intersect: the common area. combine: one curve of all paths.
    break_apart: each subpath becomes a shape. keep_sources=false replaces the inputs; true works on copies.
    target_layer (editable layer of this page) receives the results. Refuses groups, bitmaps, effects,
    effect-generated objects and consumed shapes on locked or master layers. Returns result_ids and per
    result layer, area (holes subtracted), perimeter, subpaths, bounds and paint; sources' areas.
    """
    if operation not in _LABELS:
        return ToolResult.fail(f"operation must be one of {', '.join(_LABELS)}")
    minimum = 1 if operation == "break_apart" else 2
    if isinstance(shape_ids, list) and len(shape_ids) < minimum:
        return ToolResult.fail(f"{operation} needs at least {minimum} shape IDs")
    ordered_weld = operation == "weld" and preserve_order
    if ordered_weld and keep_sources:
        return ToolResult.fail("preserve_order welds in place; pass preserve_order=false with keep_sources=true")

    def body(app, doc, page, targets):
        unit = doc.Unit
        layer = _layer_for(page, target_layer)

        def extra(shape_id, view, entry):
            reason = refusal(view, entry, consume=not keep_sources)
            if reason is None and keep_sources and layer is None and not view.editable:
                reason = "on a locked layer (the copy would land there); pass target_layer"
            return reason

        _refuse(targets, extra)
        effects = _read(targets, ["$item.Effects.Count"])
        _refuse(targets, lambda shape_id, view, entry: "has an effect; separate_effect or remove it first"
                if effects[shape_id][0] else None)
        shapes = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in targets.items()}
        sources = {shape_id: _geometry(shape, unit) for shape_id, shape in shapes.items()}
        order = _check_order(targets, shapes, target_layer) if ordered_weld else None
        with undoable(doc, page, f"MCP {_LABELS[operation]}") as step:
            results = _OPERATIONS[operation](shapes, shape_ids, keep_sources, layer, order)
            for result in results:
                if layer is not None and result.Layer.Name != layer.Name:
                    result.MoveToLayer(layer)
            result_ids = [str(result.StaticID) for result in results]
        after = page_ids(doc, page)
        records = _results(doc, page, result_ids, unit)
        data = {"operation": operation, "result_ids": result_ids, "results": records,
                "sources": [{"id": shape_id, **rounded(value or {})} for shape_id, value in sources.items()],
                "sources_kept": keep_sources}
        problems = _verify_combination(operation, records, sources, step.before, after, shape_ids, keep_sources,
                                       target_layer, unit)
        if order is not None:
            data.update(_stack_check(doc, page, order, result_ids, problems))
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _paint(shape):
    """Native fill, outline and overprint identity that an ordered weld must preserve."""
    fill, outline = shape.Fill, shape.Outline
    return (fill.Type, outline.Type, round(outline.Width, 9) if outline.Type else 0, bool(shape.OverprintFill),
            bool(shape.OverprintOutline))


def _check_order(targets, shapes, layer_name):
    """For an ordered weld: same layer, top level, consecutive, identical paint. Returns the stacking info."""
    views = {id(view) for view, _ in targets.values()}
    view = next(iter(targets.values()))[0]
    if len(views) != 1:
        raise _RefusedError([{"id": i, "layer": v.name, "reason": "preserve_order needs every shape on one layer"}
                             for i, (v, _) in targets.items()])
    if layer_name is not None and layer_name != view.name:
        raise ValueError("preserve_order keeps the result on its layer; omit target_layer or pass "
                         "preserve_order=false")
    nested = [i for i, (_, entry) in targets.items() if len(entry.path) != 1]
    if nested:
        raise ValueError(f"Shapes {nested} are inside groups; preserve_order needs direct children of the layer")
    indices = sorted(entry.path[0] for _, entry in targets.values())
    if indices[-1] - indices[0] + 1 != len(indices):
        raise ValueError(f"The shapes are not consecutive in stacking order (positions {indices}); pass "
                         "preserve_order=false to weld them anyway")
    first_id = next(iter(shapes))
    first = shapes[first_id]
    paint, color = _paint(first), first.Fill.UniformColor if first.Fill.Type == 1 else None
    if paint[0] != 1:
        raise ValueError("preserve_order needs a uniform fill on every shape")
    for shape_id, shape in shapes.items():
        if _paint(shape) != paint or (color is not None and not shape.Fill.UniformColor.IsSame(color)) \
                or (shape.Outline.Type and not shape.Outline.Color.IsSame(first.Outline.Color)):
            raise ValueError(f"Shape {shape_id} has a different fill, tint, outline or overprint flag")
    tops = [entry for entry in view.structure.entries if len(entry.path) == 1]
    by_index = {entry.path[0]: entry.shape_id for entry in tops}
    return {"view": view, "top": indices[0], "bottom": indices[-1],
            "above": by_index.get(indices[0] - 1), "below": by_index.get(indices[-1] + 1),
            "stack": [by_index[index] for index in range(indices[0], indices[-1] + 1)]}


def _stack_check(doc, page, order, result_ids, problems):
    """Read the layer's top level again: the result must sit between the original neighbors."""
    view = order["view"]
    tops = [entry.shape_id for entry in walk_layer(view.layer).entries if len(entry.path) == 1]
    result = result_ids[0]
    index = tops.index(result) if result in tops else None
    above = tops[index - 1] if index else None
    below = tops[index + 1] if index is not None and index + 1 < len(tops) else None
    if index is None or above != order["above"] or below != order["below"]:
        problems.append({"problem": "the stacking position changed", "above": above, "below": below})
    return {"stack_index": None if index is None else index + 1, "above_shape_id": above, "below_shape_id": below}


def _work(shapes, ids, keep, layer):
    """The shapes an operation consumes: the originals, or copies (on the target layer when given)."""
    if not keep:
        return [shapes[i] for i in ids]
    return [shapes[i].CopyToLayer(layer) if layer is not None else shapes[i].Duplicate(0, 0) for i in ids]


def _weld(shapes, ids, keep, layer, order):
    if order is not None:
        ids = order["stack"]  # weld into the topmost shape: it keeps its name and stacking position
    work = _work(shapes, ids, keep, layer)
    result = work[0]
    for shape in work[1:]:
        result = shape.Weld(result, False, False)
        if result is None:
            raise RuntimeError("CorelDRAW did not return a weld result")
    return [result]


def _trim(shapes, ids, keep, layer, order):
    result = _work(shapes, ids[:1], keep, layer)[0]
    for cutter in ids[1:]:
        result = shapes[cutter].Trim(result, True, False)
        if result is None:
            raise RuntimeError("Trimming removed the whole shape")
    if not keep:
        for cutter in ids[1:]:
            shapes[cutter].Delete()
    return [result]


def _intersect(shapes, ids, keep, layer, order):
    current = shapes[ids[0]]
    for other in ids[1:]:
        common = shapes[other].Intersect(current, True, True)
        if current is not shapes[ids[0]]:
            current.Delete()
        if common is None:
            raise RuntimeError("The shapes have no area in common")
        current = common
    if not keep:
        for shape_id in ids:
            shapes[shape_id].Delete()
    return [current]


def _combine(shapes, ids, keep, layer, order):
    from core.connection import get_connection

    work = _work(shapes, ids, keep, layer)
    shape_range = get_connection().app.CreateShapeRange()
    for shape in work:
        shape_range.Add(shape)
    result = shape_range.Combine()
    if result is None:
        raise RuntimeError("CorelDRAW did not return a combined shape")
    return [result]


def _break_apart(shapes, ids, keep, layer, order):
    pieces = []
    for shape in _work(shapes, ids, keep, layer):
        parts = shape.BreakApartEx()
        pieces.extend(parts.Item(index) for index in range(1, parts.Count + 1))
    return pieces


_OPERATIONS = {"weld": _weld, "trim": _trim, "intersect": _intersect, "combine": _combine,
               "break_apart": _break_apart}


def _verify_combination(operation, records, sources, before, after, ids, keep, layer_name, unit):
    problems = []
    tolerance = _tolerance(unit)
    area_tolerance = tolerance * max((record.get("perimeter", 0) for record in records), default=0) + tolerance ** 2
    result_ids = {record["id"] for record in records}
    if missing := result_ids - after:
        problems.append({"problem": f"results not found on the page: {sorted(missing)}"})
    if keep and (gone := set(ids) - after):
        problems.append({"problem": f"sources were changed or removed: {sorted(gone)}"})
    if not keep and (left := set(ids) & after - result_ids):
        problems.append({"problem": f"sources remain: {sorted(left)}"})
    if layer_name is not None:
        problems.extend({"id": r["id"], "problem": f"on layer {r['layer']!r}"} for r in records if r["layer"] != layer_name)
    if any(value is None for value in sources.values()):
        return problems  # text sources have no display curve to measure before the edit
    areas = [value["area"] for value in sources.values()]
    boxes = [value["bounds"] for value in sources.values()]
    if operation in ("weld", "trim", "intersect") and records:
        record = records[0]
        area, box = record.get("area", 0.0), record.get("bounds")
        if operation == "weld":
            union = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                     max(b[3] for b in boxes)]
            if not max(areas) - area_tolerance <= area <= sum(areas) + area_tolerance:
                problems.append({"problem": "the weld area is outside [largest source, sum of sources]",
                                 "area": area})
            if box and any(abs(a - b) > tolerance for a, b in zip(box, union, strict=True)):
                problems.append({"problem": "the weld bounds differ from the union of the sources", "bounds": box})
        elif operation == "intersect" and area > min(areas) + area_tolerance:
            problems.append({"problem": "the intersection is larger than the smallest source", "area": area})
        elif operation == "trim" and area > sources[ids[0]]["area"] + area_tolerance:
            problems.append({"problem": "the trimmed shape grew", "area": area})
    if operation == "break_apart":
        expected = sum(value["subpaths"] for value in sources.values())
        if sum(record.get("subpaths", 0) for record in records) != expected:
            problems.append({"problem": "the pieces do not hold every subpath of the sources", "expected": expected})
    if operation == "combine" and records:
        expected = sum(value["subpaths"] for value in sources.values())
        if records[0].get("subpaths") != expected:
            problems.append({"problem": "the combined curve does not hold every subpath", "expected": expected})
    return problems

