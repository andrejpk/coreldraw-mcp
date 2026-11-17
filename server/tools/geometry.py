"""Exact geometry edits on one page: transform, arrange, duplicate, convert to curves, and boolean combinations.

Every edit activates its document, refuses unsupported targets before changing anything, is one undo step
(a failure part way is undone, ``core.shapeops.undoable``), drops the structure cache, and returns verified
after-state (IDs, layers, bounds and areas in the document unit).
"""

import math
from typing import Literal

from core.connection import get_connection
from core.models import ToolResult
from core.shapeops import (
    ANY_TYPE,
    BITMAP,
    EDIT_BUDGET_SECONDS,
    VECTOR_TYPES,
    Budget,
    geometry,
    nested_refusals,
    page_ids,
    refusal,
    rounded,
    target_layer,
    undoable,
)
from core.structure import StaleStructureError, invalidate, locate_entries, read_entries, walk_layer
from core.units import MEASUREMENT_UNITS, convert, unit_name
from tools.colors import _com_shape, _edit, _read, _RefusedError, _states

Anchor = Literal["center", "top_left", "top_center", "top_right", "middle_left", "middle_right", "bottom_left",
                 "bottom_center", "bottom_right"]
_ANCHORS = {"center": (0.5, 0.5), "top_left": (0, 1), "top_center": (0.5, 1), "top_right": (1, 1),
            "middle_left": (0, 0.5), "middle_right": (1, 0.5), "bottom_left": (0, 0), "bottom_center": (0.5, 0),
            "bottom_right": (1, 0)}
Unit = Literal["pt", "mm", "in", "cm"]
_EDGES = ["$item.LeftX", "$item.BottomY", "$item.RightX", "$item.TopY"]
_BOUNDS = "array($item.LeftX, $item.BottomY, $item.RightX, $item.TopY)"
# Each CorelDRAW command (move, weld, copy...) runs inside one bridge call, which is cancelled at 60 s: an edit stops
# and is undone once it has run this long.
_BUDGET_SECONDS = EDIT_BUDGET_SECONDS
_TOLERANCE_PT = 0.01  # bounds and offsets (CorelDRAW stores coordinates in tenths of a micron)
_RELATIVE_TOLERANCE = 1e-5  # scaled bounds (text glyph boxes round)


class _ArgumentError(ValueError):
    pass


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pair(spec, keys, label, positive=False):
    """A dict with one or both of ``keys`` as a tuple (missing: None)."""
    if not isinstance(spec, dict) or not spec or set(spec) - set(keys):
        raise _ArgumentError(f"{label} is an object with {' and/or '.join(keys)}")
    values = tuple(spec.get(key) for key in keys)
    if not all(value is None or (_finite(value) and (value > 0 or not positive)) for value in values):
        raise _ArgumentError(f"{label} values must be finite numbers" + (" above 0" if positive else ""))
    return values


def _point(box, anchor):
    fx, fy = _ANCHORS[anchor]
    return box[0] + fx * (box[2] - box[0]), box[1] + fy * (box[3] - box[1])


def _union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _out(box, document_unit, unit):
    return [round(convert(value, document_unit, unit), 6) for value in box]


def _in_place(targets, extra=None):
    """Refuse targets an in-place edit cannot change: locked/master layers, effect groups and generated objects,
    PowerClip contents, shapes inside a targeted group (and ``extra`` reasons)."""
    nested = nested_refusals(targets)

    def reason(shape_id, view, entry):
        return (refusal(view, entry, consume=True, groups=True, bitmaps=True, types=ANY_TYPE) or nested.get(shape_id)
                or (extra(shape_id, view, entry) if extra else None))
    _refuse(targets, reason)


def _range_of(app, shapes):
    selection = app.CreateShapeRange()
    for shape in shapes:
        selection.Add(shape)
    return selection


def _edges(targets):
    return {shape_id: tuple(row) for shape_id, row in _read(targets, _EDGES).items()}


def _box_problems(ids, after, expected, tolerance):
    problems = []
    for shape_id in ids:
        want, got = expected[shape_id], after[shape_id]
        slack = tolerance + _RELATIVE_TOLERANCE * max(abs(want[2] - want[0]), abs(want[3] - want[1]))
        if any(abs(a - b) > slack for a, b in zip(got, want, strict=True)):
            problems.append({"id": shape_id, "problem": "bounds differ from the request",
                             "expected": [round(v, 6) for v in want], "actual": [round(v, 6) for v in got]})
    return problems


# --- transform_shapes -----------------------------------------------------------------------------------------

def transform_shapes(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    move_to: dict[str, float] | None = None,
    move_by: dict[str, float] | None = None,
    size: dict[str, float] | None = None,
    scale: float | dict[str, float] | None = None,
    rotate: float | None = None,
    anchor: Anchor = "center",
    unit: Unit | None = None,
    as_group: bool = False,
) -> ToolResult:
    """Move, resize, scale and/or rotate exact shapes (groups, text, bitmaps too); one verified undo step.

    unit (in, mm, cm, pt) is required with move_to, move_by or size. Coordinates as inspect_shapes bounds: x right,
    y up from the document origin (normally the page's bottom-left).
    move_to {"x", "y"} (either optional) puts the anchor there; move_by {"dx", "dy"}; size {"width", "height"}
    (one keeps the aspect ratio) or scale (1.5 or {"x", "y"}); rotate in degrees counterclockwise.
    Size/scale and rotate happen about the anchor (center, top_left, top_center, top_right, middle_left,
    middle_right, bottom_left, bottom_center, bottom_right), then the move. as_group: one combined bounding box
    instead of each shape's own. Refuses locked/master layers, effect groups and effect-generated objects (use the
    control shape), PowerClip contents, shapes inside a targeted group. Returns bounds [left, bottom, right, top]
    before and after (and rotation_angle), checked against the request.
    """
    try:
        if move_to is not None and move_by is not None:
            raise _ArgumentError("Pass move_to or move_by, not both")
        if size is not None and scale is not None:
            raise _ArgumentError("Pass size or scale, not both")
        target = _pair(move_to, ("x", "y"), "move_to") if move_to is not None else None
        shift = _pair(move_by, ("dx", "dy"), "move_by") if move_by is not None else None
        dimensions = _pair(size, ("width", "height"), "size", positive=True) if size is not None else None
        if isinstance(scale, dict):
            factors = tuple(1.0 if value is None else value for value in _pair(scale, ("x", "y"), "scale", True))
        elif scale is not None:
            if not _finite(scale) or scale <= 0:
                raise _ArgumentError("scale must be a number above 0 or {\"x\", \"y\"}")
            factors = (float(scale), float(scale))
        else:
            factors = None
        if rotate is not None and not _finite(rotate):
            raise _ArgumentError("rotate must be a finite number of degrees")
        if all(value is None for value in (target, shift, dimensions, factors, rotate)):
            raise _ArgumentError("Pass at least one of move_to, move_by, size, scale or rotate")
        if (target or shift or dimensions) and unit not in MEASUREMENT_UNITS:
            raise _ArgumentError(f"unit ({', '.join(MEASUREMENT_UNITS)}) is required with move_to, move_by or size")
        if anchor not in _ANCHORS:
            raise _ArgumentError(f"anchor must be one of {', '.join(_ANCHORS)}")
    except _ArgumentError as exc:
        return ToolResult.fail(str(exc))

    def body(app, doc, page, targets):
        document_unit = doc.Unit
        out_unit = unit or unit_name(document_unit)

        def length(value):
            return convert(value, unit, document_unit)

        _in_place(targets)
        before = _edges(targets)
        angles = _angles(targets) if rotate else {}
        shapes = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in targets.items()}
        blocks = [list(shape_ids)] if as_group else [[shape_id] for shape_id in shape_ids]
        handles = [shapes[ids[0]] if len(ids) == 1 else _range_of(app, [shapes[i] for i in ids]) for ids in blocks]
        expected = dict(before)
        stretches = []
        for ids in blocks:
            box = _union(before[i] for i in ids)
            sx, sy = factors or (1.0, 1.0)
            if dimensions is not None:
                width, height = (length(v) if v is not None else None for v in dimensions)
                current = (box[2] - box[0], box[3] - box[1])
                for wanted, have, axis in ((width, current[0], "width"), (height, current[1], "height")):
                    if wanted is not None and have <= 0:
                        raise _RefusedError([{"id": i, "layer": targets[i][0].name,
                                              "reason": f"has no {axis} to resize"} for i in ids])
                sx = width / current[0] if width is not None else height / current[1]
                sy = height / current[1] if height is not None else sx
            px, py = _point(box, anchor)
            stretches.append((sx, sy, px, py))
            for i in ids:
                b = before[i]
                expected[i] = (px + sx * (b[0] - px), py + sy * (b[1] - py), px + sx * (b[2] - px),
                               py + sy * (b[3] - py))
        budget = Budget(_BUDGET_SECONDS)
        with undoable(doc, page, "MCP transform_shapes"):
            for done, (handle, (sx, sy, px, py)) in enumerate(zip(handles, stretches, strict=True)):
                budget.check(done, len(handles), "resizing and rotating")
                if (sx, sy) != (1.0, 1.0):
                    handle.StretchEx(px, py, sx, sy, True)
                if rotate:
                    handle.RotateEx(rotate, px, py)
            current = _edges(targets) if rotate else expected
            for done, (ids, handle) in enumerate(zip(blocks, handles, strict=True)):
                budget.check(done, len(blocks), "moving")
                box = _union(current[i] for i in ids)
                dx = dy = 0.0
                if target is not None:
                    ax, ay = _point(box, anchor)
                    dx = length(target[0]) - ax if target[0] is not None else 0.0
                    dy = length(target[1]) - ay if target[1] is not None else 0.0
                elif shift is not None:
                    dx, dy = (length(v) if v is not None else 0.0 for v in shift)
                if dx or dy:
                    handle.Move(dx, dy)
                for i in ids:
                    b = current[i]
                    expected[i] = (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)
        after = _edges(targets)
        tolerance = _tolerance(document_unit)
        problems = _box_problems(shape_ids, after, expected, tolerance) if not rotate else []
        if rotate:
            if target is not None:
                for ids in blocks:
                    ax, ay = _point(_union(after[i] for i in ids), anchor)
                    want = [length(v) if v is not None else None for v in target]
                    if any(w is not None and abs(w - a) > tolerance for w, a in zip(want, (ax, ay), strict=True)):
                        problems.append({"ids": ids, "problem": "the anchor is not at move_to"})
            turned = _angles(targets) if angles else {}
            for shape_id, angle in turned.items():
                delta = (angle - angles[shape_id] - rotate) % 360.0
                if min(delta, 360.0 - delta) > 1e-3:
                    problems.append({"id": shape_id, "problem": "rotation angle differs",
                                     "rotation_angle": round(angle, 6)})
        records = []
        for shape_id in shape_ids:
            record = {"id": shape_id, "bounds_before": _out(before[shape_id], document_unit, out_unit),
                      "bounds": _out(after[shape_id], document_unit, out_unit)}
            if rotate and shape_id in angles:
                record["rotation_angle"] = round(turned.get(shape_id, 0.0), 6)
            records.append(record)
        data = {"unit": out_unit, "anchor": anchor, "shapes": records}
        if as_group:
            data["group_bounds"] = _out(_union(after.values()), document_unit, out_unit)
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _angles(targets):
    """RotationAngle per shape, or {} when some shape type has none."""
    try:
        return {shape_id: float(row[0]) for shape_id, row in _read(targets, ["$item.RotationAngle"]).items()}
    except StaleStructureError:
        raise
    except Exception:
        return {}


# --- arrange_shapes --------------------------------------------------------------------------------------------

Align = Literal["left", "center", "right", "top", "middle", "bottom"]
ZOrder = Literal["front", "back", "forward", "backward", "above", "below"]
_ALIGN = {"left": (0, 0.0), "center": (0, 0.5), "right": (0, 1.0), "bottom": (1, 0.0), "middle": (1, 0.5),
          "top": (1, 1.0)}


def arrange_shapes(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    align: Align | None = None,
    distribute: Literal["horizontal", "vertical"] | None = None,
    z_order: ZOrder | None = None,
    reference: str = "selection",
    spacing: float | None = None,
    unit: Unit | None = None,
) -> ToolResult:
    """Align, distribute or restack exact shapes on one page; one verified undo step. Pass one of:

    align: left/center/right or top/middle/bottom to reference "selection" (combined box), "page", or a shape ID
    (it stays put). distribute: horizontal/vertical, equal gaps (ordered by center, top to bottom), the outermost
    shapes fixed ("selection") or on the page edges ("page"); spacing + unit sets the gap from the first shape.
    z_order: front/back of their layer or group, forward/backward one step, or above/below the shape ID in
    reference (same layer and group); relative order kept. Refuses as transform_shapes. Returns bounds, or each
    shape's stacking index (1 = front) and its neighbours above and below.
    """
    chosen = [name for name, value in (("align", align), ("distribute", distribute), ("z_order", z_order))
              if value is not None]
    if len(chosen) != 1:
        return ToolResult.fail("Pass exactly one of align, distribute or z_order")
    if align is not None and align not in _ALIGN:
        return ToolResult.fail(f"align must be one of {', '.join(_ALIGN)}")
    if distribute is not None and distribute not in ("horizontal", "vertical"):
        return ToolResult.fail("distribute must be horizontal or vertical")
    if z_order is not None and z_order not in _Z_ACTIONS:
        return ToolResult.fail(f"z_order must be one of {', '.join(_Z_ACTIONS)}")
    if spacing is not None and (distribute is None or not _finite(spacing) or unit not in MEASUREMENT_UNITS):
        return ToolResult.fail("spacing is a finite gap for distribute and needs unit")
    if unit is not None and unit not in MEASUREMENT_UNITS:
        return ToolResult.fail(f"unit must be one of {', '.join(MEASUREMENT_UNITS)}")
    if z_order in ("above", "below") and reference in ("selection", "page"):
        return ToolResult.fail("z_order above/below needs reference = the shape ID to stack against")
    if distribute is not None and reference not in ("selection", "page"):
        return ToolResult.fail('distribute takes reference "selection" or "page"')
    if isinstance(shape_ids, list) and reference in shape_ids:
        return ToolResult.fail("reference cannot also be in shape_ids")

    def body(app, doc, page, targets):
        _in_place(targets)
        if z_order is not None:
            return _restack(doc, page, targets, shape_ids, z_order, reference)
        document_unit = doc.Unit
        out_unit = unit or unit_name(document_unit)
        before = _edges(targets)
        if reference == "page":
            frame = (page.LeftX, page.BottomY, page.RightX, page.TopY)
        elif reference == "selection":
            frame = _union(before.values())
        else:
            view, entry = locate_entries(doc, page, [reference])[reference]
            frame = tuple(read_entries(view, [entry], _EDGES)[0])
        if align is not None:
            axis, fraction = _ALIGN[align]
            line = frame[axis] + fraction * (frame[axis + 2] - frame[axis])
            moves = {}
            for shape_id, box in before.items():
                delta = line - (box[axis] + fraction * (box[axis + 2] - box[axis]))
                moves[shape_id] = (delta, 0.0) if axis == 0 else (0.0, delta)
        else:
            gap = convert(spacing, unit, document_unit) if spacing is not None else None
            moves = _distribution(before, frame, distribute, gap)
        shapes = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in targets.items()}
        budget = Budget(_BUDGET_SECONDS)
        with undoable(doc, page, f"MCP arrange_shapes {chosen[0]}"):
            for done, (shape_id, (dx, dy)) in enumerate(moves.items()):
                budget.check(done, len(moves), "moving")
                if dx or dy:
                    shapes[shape_id].Move(dx, dy)
        after = _edges(targets)
        expected = {shape_id: (b[0] + moves[shape_id][0], b[1] + moves[shape_id][1], b[2] + moves[shape_id][0],
                               b[3] + moves[shape_id][1]) for shape_id, b in before.items()}
        data = {"unit": out_unit, "reference_bounds": _out(frame, document_unit, out_unit),
                "shapes": [{"id": shape_id, "bounds_before": _out(before[shape_id], document_unit, out_unit),
                            "bounds": _out(after[shape_id], document_unit, out_unit)} for shape_id in shape_ids]}
        if problems := _box_problems(shape_ids, after, expected, _tolerance(document_unit)):
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _distribution(before, frame, direction, gap):
    """{id: (dx, dy)}: boxes in order along the axis with equal (or fixed) gaps."""
    if len(before) < 2:
        raise ValueError("distribute needs at least two shapes")
    if direction == "horizontal":
        order = sorted(before, key=lambda i: (before[i][0] + before[i][2]) / 2)
        sizes = [before[i][2] - before[i][0] for i in order]
        start, end = frame[0], frame[2]
    else:
        order = sorted(before, key=lambda i: -(before[i][1] + before[i][3]) / 2)
        sizes = [before[i][3] - before[i][1] for i in order]
        start, end = frame[3], frame[1]
    if gap is None:
        gap = (abs(end - start) - sum(sizes)) / (len(order) - 1)
    moves, position = {}, start
    for shape_id, extent in zip(order, sizes, strict=True):
        box = before[shape_id]
        if direction == "horizontal":
            moves[shape_id] = (position - box[0], 0.0)
            position += extent + gap
        else:
            moves[shape_id] = (0.0, position - box[3])
            position -= extent + gap
    return moves


_Z_ACTIONS = ("front", "back", "forward", "backward", "above", "below")


def _siblings(structure, parent_id, top_level):
    """Shape IDs with this parent (stacking order, front first); PowerClip contents excluded."""
    return [entry.shape_id for entry in structure.entries if entry.parent_id == parent_id
            and (len(entry.path) == 1) == top_level and entry.path[-1] > 0]


def _restack(doc, page, targets, shape_ids, action, reference):
    groups = {}
    for shape_id, (view, entry) in targets.items():
        key = (view.ordinal, view.name, entry.parent_id)
        groups.setdefault(key, (view, entry.parent_id, len(entry.path) == 1, []))[3].append(shape_id)
    ref_shape = None
    if action in ("above", "below"):
        ref_view, ref_entry = locate_entries(doc, page, [reference])[reference]
        (view, parent_id, _, _), *rest = groups.values()
        if rest or (ref_view.ordinal, ref_view.name, ref_entry.parent_id) != (view.ordinal, view.name, parent_id):
            raise ValueError("above/below needs every shape and the reference on the same layer and in the same "
                             "group (move_shapes_to_layer first)")
        ref_shape = _com_shape(ref_view, ref_entry, reference)
    shapes = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in targets.items()}
    plans = []
    for view, parent_id, top_level, ids in groups.values():
        stack = _siblings(view.structure, parent_id, top_level)
        ordered = sorted(ids, key=stack.index)  # front first
        expected, calls = _plan(stack, ordered, action, reference)
        plans.append((view, parent_id, top_level, expected, calls))
    budget = Budget(_BUDGET_SECONDS)
    with undoable(doc, page, f"MCP arrange_shapes z_order {action}"):
        for *_, calls in plans:
            for done, (shape_id, method) in enumerate(calls):
                budget.check(done, len(calls), "restacking")
                if method in ("OrderFrontOf", "OrderBackOf"):
                    getattr(shapes[shape_id], method)(ref_shape)
                else:
                    getattr(shapes[shape_id], method)()
    records, problems = {}, []
    for view, parent_id, top_level, expected, _ in plans:
        actual = _siblings(walk_layer(view.layer), parent_id, top_level)
        if actual != expected:
            problems.append({"layer": view.name, "problem": "stacking order differs from the request",
                             "expected": expected[:30], "actual": actual[:30]})
        for shape_id in shape_ids:
            if shape_id in actual:
                index = actual.index(shape_id)
                records[shape_id] = {"id": shape_id, "layer": view.name, "index": index + 1,
                                     "above": actual[index - 1] if index else None,
                                     "below": actual[index + 1] if index + 1 < len(actual) else None}
    data = {"z_order": action, "shapes": [records.get(shape_id, {"id": shape_id}) for shape_id in shape_ids]}
    if problems:
        data["verification_errors"] = problems
    return data


def _plan(stack, ordered, action, reference):
    """Expected sibling order (front first) and the CorelDRAW calls that produce it."""
    others = [shape_id for shape_id in stack if shape_id not in ordered]
    if action == "front":
        return ordered + others, [(i, "OrderToFront") for i in reversed(ordered)]
    if action == "back":
        return others + ordered, [(i, "OrderToBack") for i in ordered]
    if action == "above":
        at = others.index(reference)
        return others[:at] + ordered + others[at:], [(i, "OrderFrontOf") for i in ordered]
    if action == "below":
        at = others.index(reference) + 1
        return others[:at] + ordered + others[at:], [(i, "OrderBackOf") for i in reversed(ordered)]
    stack, calls, blocked = list(stack), [], set()
    forward = action == "forward"
    for shape_id in ordered if forward else reversed(ordered):
        index = stack.index(shape_id)
        neighbour = index - 1 if forward else index + 1
        if not 0 <= neighbour < len(stack) or stack[neighbour] in blocked:
            blocked.add(shape_id)
            continue
        stack[index], stack[neighbour] = stack[neighbour], stack[index]
        calls.append((shape_id, "OrderForwardOne" if forward else "OrderBackOne"))
    return stack, calls


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
    effect-generated objects and consumed shapes on locked or master layers. Stops and undoes itself after 35 s
    (split large selections). Returns result_ids and per result layer, area (holes subtracted), perimeter,
    subpaths, bounds and paint; sources' areas.
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
        budget = Budget(_BUDGET_SECONDS)
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
            results = _OPERATIONS[operation](shapes, shape_ids, keep_sources, layer, order, budget)
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


def _work(shapes, ids, keep, layer, budget):
    """The shapes an operation consumes: the originals, or copies (on the target layer when given)."""
    if not keep:
        return [shapes[i] for i in ids]
    copies = []
    for done, shape_id in enumerate(ids):
        budget.check(done, len(ids), "copying")
        copies.append(shapes[shape_id].CopyToLayer(layer) if layer is not None else shapes[shape_id].Duplicate(0, 0))
    return copies


def _weld(shapes, ids, keep, layer, order, budget):
    if order is not None:
        ids = order["stack"]  # weld into the topmost shape: it keeps its name and stacking position
    work = _work(shapes, ids, keep, layer, budget)
    result = work[0]
    for done, shape in enumerate(work[1:]):
        budget.check(done, len(work) - 1, "welding")
        result = shape.Weld(result, False, False)
        if result is None:
            raise RuntimeError("CorelDRAW did not return a weld result")
    return [result]


def _trim(shapes, ids, keep, layer, order, budget):
    result = _work(shapes, ids[:1], keep, layer, budget)[0]
    for done, cutter in enumerate(ids[1:]):
        budget.check(done, len(ids) - 1, "trimming")
        result = shapes[cutter].Trim(result, True, False)
        if result is None:
            raise RuntimeError("Trimming removed the whole shape")
    if not keep:
        for cutter in ids[1:]:
            shapes[cutter].Delete()
    return [result]


def _intersect(shapes, ids, keep, layer, order, budget):
    current = shapes[ids[0]]
    for done, other in enumerate(ids[1:]):
        budget.check(done, len(ids) - 1, "intersecting")
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


def _combine(shapes, ids, keep, layer, order, budget):
    work = _work(shapes, ids, keep, layer, budget)
    shape_range = get_connection().app.CreateShapeRange()
    for shape in work:
        shape_range.Add(shape)
    result = shape_range.Combine()
    if result is None:
        raise RuntimeError("CorelDRAW did not return a combined shape")
    return [result]


def _break_apart(shapes, ids, keep, layer, order, budget):
    pieces = []
    work = _work(shapes, ids, keep, layer, budget)
    for done, shape in enumerate(work):
        budget.check(done, len(work), "breaking apart")
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
        problems.extend({"id": r["id"], "problem": f"on layer {r['layer']!r}"} for r in records
                        if r["layer"] != layer_name)
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

