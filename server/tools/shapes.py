"""Create, import, group, PowerClip and delete exact shapes on one page.

Every edit activates its document, locates every target before the first change, refuses unsupported targets
(locked or master layers, effect groups and effect-generated objects, PowerClip contents) before changing anything,
is one undo step (a failure part way is undone), drops the structure cache, and returns verified after-state.
"""

from __future__ import annotations

import math
import os
from typing import Literal

from core import colorspec
from core.connection import get_connection
from core.models import ToolResult
from core.shapeops import (
    ANY_TYPE,
    BITMAP,
    GROUP,
    VECTOR_TYPES,
    descendants,
    nested_refusals,
    page_ids,
    refusal,
    target_layer,
    undoable,
)
from core.structure import invalidate, locate_entries, page_layers, shape_range, walk_layer
from core.targets import resolve_document, resolve_page
from core.units import MEASUREMENT_UNITS, convert, unit_name
from tools.colors import _com_shape, _edit, _outline_range, _read, _RefusedError, _resolve_color, _states

Unit = Literal["pt", "mm", "in", "cm"]
ColorSpec = str | dict
_EDGES = ["$item.LeftX", "$item.BottomY", "$item.RightX", "$item.TopY"]
_TOLERANCE_PT = 0.01
_AUTO_SENSE = 0  # cdrFilter: detect the format from the file


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _in_place(targets, extra=None):
    """Refuse (changing nothing) locked/master layers, effect groups and generated objects, PowerClip contents,
    shapes inside a targeted group, and ``extra`` reasons."""
    nested = nested_refusals(targets)
    refused = []
    for shape_id, (view, entry) in targets.items():
        reason = (refusal(view, entry, consume=True, groups=True, bitmaps=True, types=ANY_TYPE)
                  or nested.get(shape_id) or (extra(shape_id, view, entry) if extra else None))
        if reason:
            refused.append({"id": shape_id, "layer": view.name, "reason": reason})
    if refused:
        raise _RefusedError(refused)


def _out(box, document_unit, unit):
    return [round(convert(value, document_unit, unit), 6) for value in box]


def _union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _page_edit(document_path, page_index, body) -> ToolResult:
    """Run ``body(app, doc, page)`` as one bridge edit on an exact page (activated document, cache dropped after)."""
    conn = get_connection()

    def run():
        doc, actual_path = resolve_document(conn.app, document_path, activate=True)
        page = resolve_page(doc, page_index)
        try:
            return {"document_path": actual_path, "page_index": page_index, "document_unit": unit_name(doc.Unit),
                    **body(conn.app, doc, page)}
        finally:
            invalidate(actual_path)

    result = conn.safe_call(run)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if data.get("verification_errors"):
        return ToolResult(success=False, data=data, error=f"Read-back differs ({len(data['verification_errors'])} "
                                                          "problems); the edit is one undo step (undo)")
    return ToolResult.ok(**data)


def _drawing_layer(doc, page, name):
    """The named editable layer of the page, else the active layer (if on this page and editable), else the top
    editable ordinary layer of the page."""
    if name is not None:
        return target_layer(page, name)
    active = doc.ActiveLayer
    if active is not None and not active.Master and not active.IsSpecialLayer and active.Editable \
            and active.Page.Index == page.Index:
        return active
    for layer in page.Layers:
        if not layer.Master and not layer.IsSpecialLayer and layer.Editable:
            return layer
    raise ValueError(f"Page {page.Index} has no editable layer; create_layer or set_layer_properties first")


def _new_records(doc, page, ids, document_unit, unit, paint=False):
    """Layer, type and bounds (and paint) of new shapes, read back through a fresh structure."""
    invalidate()
    located = locate_entries(doc, page, ids)
    edges = _read(located, _EDGES)
    states = _states(located, document_unit, ("fill", "outline")) if paint else {}
    records = []
    for shape_id in ids:
        view, entry = located[shape_id]
        record = {"id": shape_id, "layer": view.name, "type": entry.type,
                  "bounds": _out(edges[shape_id], document_unit, unit)}
        if shape_id in states:
            record.update({key: states[shape_id][key] for key in ("fill", "outline") if key in states[shape_id]})
        records.append(record)
    return records, edges


def _top_level_new(doc, page, before):
    """New shapes on the page that are not inside another new shape (an imported group, not its members)."""
    new = []
    for view in page_layers(doc, page, fresh=True):
        for entry in view.structure.entries:
            if entry.shape_id not in before and (entry.parent_id is None or entry.parent_id in before):
                new.append(entry.shape_id)
    return new


# --- create_shape ---------------------------------------------------------------------------------------------

def create_shape(
    document_path: str,
    page_index: int,
    kind: Literal["rectangle", "ellipse", "line"],
    x: float,
    y: float,
    unit: Unit,
    width: float | None = None,
    height: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    layer: str | None = None,
    fill: ColorSpec | None = None,
    outline: ColorSpec | None = None,
    outline_width: float | None = None,
    corner_radius: float = 0.0,
    name: str | None = None,
) -> ToolResult:
    """Create a rectangle, ellipse or line on an exact page and layer; one verified undo step.

    Lengths in unit; coordinates as inspect_shapes bounds (y up). rectangle/ellipse: bottom-left x, y and width,
    height (corner_radius for rectangles); line: x, y to x2, y2. layer: an editable layer of this page (default:
    the active layer if on this page, else its top editable layer). fill/outline: color specs as set_fill
    ("PANTONE 485 C", "CMYK 0/0/0/100", "none", ...); outline_width in unit. Returns shape_id, layer, bounds, paint.
    """
    if kind not in ("rectangle", "ellipse", "line"):
        return ToolResult.fail("kind must be rectangle, ellipse or line")
    if unit not in MEASUREMENT_UNITS:
        return ToolResult.fail(f"unit must be one of {', '.join(MEASUREMENT_UNITS)}")
    if kind == "line":
        if not _finite(x, y, x2, y2) or width is not None or height is not None:
            return ToolResult.fail("A line takes x, y, x2 and y2 (no width or height)")
        box = (min(x, x2), min(y, y2), max(x, x2), max(y, y2))
    else:
        if not _finite(x, y, width, height) or width <= 0 or height <= 0 or x2 is not None or y2 is not None:
            return ToolResult.fail(f"A {kind} takes x, y and width, height above 0 (no x2, y2)")
        box = (x, y, x + width, y + height)
    if not _finite(corner_radius) or corner_radius < 0 or (corner_radius and kind != "rectangle"):
        return ToolResult.fail("corner_radius is a length of 0 or more, for rectangles only")
    if outline_width is not None and (not _finite(outline_width) or outline_width < 0):
        return ToolResult.fail("outline_width is a length of 0 or more")
    try:
        fill_spec = colorspec.parse(fill) if fill is not None else None
        outline_spec = colorspec.parse(outline) if outline is not None else None
    except colorspec.ColorSpecError as exc:
        return ToolResult.fail(str(exc))

    def body(app, doc, page):
        document_unit = doc.Unit
        on = _drawing_layer(doc, page, layer)
        left, bottom, right, top = (convert(v, unit, document_unit) for v in box)
        fill_color = _resolve_color(app, doc, page, fill_spec) if fill_spec is not None else None
        outline_color = _resolve_color(app, doc, page, outline_spec) if outline_spec is not None else None
        with undoable(doc, page, f"MCP create_shape {kind}") as step:
            if kind == "rectangle":
                radius = convert(corner_radius, unit, document_unit)
                shape = on.CreateRectangle2(left, bottom, right - left, top - bottom, radius, radius, radius, radius)
            elif kind == "ellipse":
                shape = on.CreateEllipse2((left + right) / 2, (bottom + top) / 2, (right - left) / 2,
                                          (top - bottom) / 2)
            else:
                shape = on.CreateLineSegment(*(convert(v, unit, document_unit) for v in (x, y, x2, y2)))
            if name:
                shape.Name = name
            if fill_color is not None:
                if fill_color.none:
                    shape.Fill.ApplyNoFill()
                else:
                    shape.Fill.ApplyUniformFill(fill_color.color)
            if outline_color is not None and outline_color.none:
                shape.Outline.SetNoOutline()
            elif outline_color is not None or outline_width is not None:
                width_doc = convert(outline_width, unit, document_unit) if outline_width is not None else -1.0
                selection = app.CreateShapeRange()
                selection.Add(shape)
                _outline_range(selection, width_doc, outline_color.color if outline_color is not None else None)
            shape_id = str(shape.StaticID)
        records, edges = _new_records(doc, page, [shape_id], document_unit, unit, paint=True)
        record, problems = records[0], []
        if page_ids(doc, page) - step.before != {shape_id}:
            problems.append({"problem": "other shapes changed on the page"})
        tolerance = convert(_TOLERANCE_PT, "pt", document_unit)
        if any(abs(a - b) > tolerance for a, b in zip(edges[shape_id], (left, bottom, right, top), strict=True)):
            problems.append({"problem": "bounds differ from the request", "bounds": record["bounds"]})
        if record["layer"] != on.Name:
            problems.append({"problem": f"created on layer {record['layer']!r}"})
        if fill_color is not None and not colorspec.same(record.get("fill") or {}, fill_color.identity):
            problems.append({"problem": "fill differs", "fill": record.get("fill")})
        line = record.get("outline") or {}
        if outline_color is not None and not colorspec.same(line.get("color") or line, outline_color.identity):
            problems.append({"problem": "outline color differs", "outline": line})
        if outline_width is not None and abs(line.get("width_pt", -1) - convert(outline_width, unit, "pt")) > 0.001:
            problems.append({"problem": "outline width differs", "outline": line})
        data = {"shape_id": shape_id, "kind": kind, "unit": unit, "name": name, **record}
        if problems:
            data["verification_errors"] = problems
        return data

    return _page_edit(document_path, page_index, body)


# --- import_file ----------------------------------------------------------------------------------------------

def import_file(
    document_path: str,
    page_index: int,
    path: str,
    layer: str | None = None,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
    unit: Unit | None = None,
) -> ToolResult:
    """Import a file CorelDRAW reads (SVG, AI, EPS, PDF, PNG, JPEG, TIFF...) onto an exact page and layer;
    one verified undo step.

    layer as create_shape. x, y (unit) place the bottom-left of the imported objects' box (default: page center);
    width and/or height (one keeps the aspect ratio) resize it. The active page is kept. Returns new_ids
    (top-level objects, e.g. one group) with type, layer and bounds.
    """
    if not isinstance(path, str) or not os.path.isabs(path) or not os.path.isfile(path):
        return ToolResult.fail(f"Not a file (absolute path required): {path!r}")
    lengths = [v for v in (x, y, width, height) if v is not None]
    if lengths and (unit not in MEASUREMENT_UNITS or not _finite(*lengths)):
        return ToolResult.fail(f"x, y, width and height are finite lengths with unit ({', '.join(MEASUREMENT_UNITS)})")
    if any(v is not None and v <= 0 for v in (width, height)):
        return ToolResult.fail("width and height must be above 0")

    def body(app, doc, page):
        document_unit = doc.Unit
        out_unit = unit or unit_name(document_unit)
        on = _drawing_layer(doc, page, layer)
        active_page = doc.ActivePage.Index
        hidden = not on.Visible
        try:
            with undoable(doc, page, "MCP import_file") as step:
                try:
                    if hidden:  # importing onto a hidden layer fails inside CorelDRAW: show it meanwhile
                        on.Visible = True
                    on.Import(path, _AUTO_SENSE, app.CreateStructImportOptions())
                except Exception as exc:
                    raise RuntimeError(f"CorelDRAW could not import {os.path.basename(path)!r}: {exc}") from exc
                finally:
                    if hidden:
                        on.Visible = False
                new_ids = _top_level_new(doc, page, step.before)
                if not new_ids:
                    raise RuntimeError("The import produced no objects on the page")
                located = locate_entries(doc, page, new_ids)
                selection = app.CreateShapeRange()
                for shape_id, (view, entry) in located.items():
                    selection.Add(_com_shape(view, entry, shape_id))
                box = _union(_read(located, _EDGES).values())
                if width is not None or height is not None:
                    sx = convert(width, unit, document_unit) / (box[2] - box[0]) if width is not None else None
                    sy = convert(height, unit, document_unit) / (box[3] - box[1]) if height is not None else None
                    selection.StretchEx(box[0], box[1], sx or sy, sy or sx, True)
                if x is not None or y is not None:
                    dx = convert(x, unit, document_unit) - box[0] if x is not None else 0.0
                    dy = convert(y, unit, document_unit) - box[1] if y is not None else 0.0
                    selection.Move(dx, dy)
        finally:
            if doc.ActivePage.Index != active_page:
                doc.Pages(active_page).Activate()
        records, edges = _new_records(doc, page, new_ids, document_unit, out_unit)
        box = _union(edges.values())
        problems = []
        if wrong := [r["id"] for r in records if r["layer"] != on.Name]:
            problems.append({"problem": f"imported objects not on layer {on.Name!r}", "ids": wrong})
        tolerance = convert(_TOLERANCE_PT, "pt", document_unit) + 1e-5 * max(box[2] - box[0], box[3] - box[1])
        for value, actual in ((x, box[0]), (y, box[1]), (width, box[2] - box[0]), (height, box[3] - box[1])):
            if value is not None and abs(convert(value, unit, document_unit) - actual) > tolerance:
                problems.append({"problem": "position or size differs", "bounds": _out(box, document_unit, out_unit)})
                break
        data = {"path": path, "layer": on.Name, "new_ids": new_ids,
                "objects_added": len(page_ids(doc, page) - step.before), "unit": out_unit,
                "bounds": _out(box, document_unit, out_unit), "shapes": records, "active_page": doc.ActivePage.Index}
        if problems:
            data["verification_errors"] = problems
        return data

    return _page_edit(document_path, page_index, body)


# --- group_shapes / powerclip -----------------------------------------------------------------------------------

def group_shapes(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    action: Literal["group", "ungroup", "ungroup_all"] = "group",
    name: str | None = None,
) -> ToolResult:
    """Group exact top-level shapes of one layer, or ungroup exact groups; one verified undo step.

    group: the group takes the topmost member's stacking place and keeps their order (name optional).
    ungroup: direct children replace the group; ungroup_all also dissolves nested groups. Refuses locked/master
    layers, effect objects, PowerClip contents. Returns group_id and member_ids, or each group's child_ids.
    """
    if action not in ("group", "ungroup", "ungroup_all"):
        return ToolResult.fail("action must be group, ungroup or ungroup_all")
    if action == "group" and isinstance(shape_ids, list) and len(shape_ids) < 2:
        return ToolResult.fail("group needs at least two shape IDs")
    if action != "group" and name is not None:
        return ToolResult.fail("name is for action=group")

    def body(app, doc, page, targets):
        if action == "group":
            layers = {(view.ordinal, view.name) for view, _ in targets.values()}
            _in_place(targets, lambda i, view, entry: "inside a group; group top-level shapes" if entry.parent
                      else "not on the same layer as the others" if len(layers) > 1 else None)
            return _group(app, doc, page, targets, shape_ids, name)
        _in_place(targets, lambda i, view, entry: None if entry.type == GROUP else "not a group")
        return _ungroup(doc, page, targets, shape_ids, action == "ungroup_all")

    return _edit(document_path, page_index, shape_ids, body)


def _group(app, doc, page, targets, shape_ids, name):
    view = next(iter(targets.values()))[0]
    stack = [e.shape_id for e in view.structure.entries if len(e.path) == 1]
    members = sorted(shape_ids, key=stack.index)
    # CorelDRAW puts a new group at the back of the active layer: place it where the topmost member was, directly
    # above the first non-member below it (or at the back of the layer).
    below = next((i for i in stack[stack.index(members[0]) + 1:] if i not in targets), None)
    shapes = [_com_shape(v, e, i) for i, (v, e) in targets.items()]
    below_shape = _com_shape(view, view.structure.entries[view.structure.position[below]], below) if below else None
    layer = view.layer
    with undoable(doc, page, "MCP group_shapes") as step:
        selection = app.CreateShapeRange()
        for shape in shapes:
            selection.Add(shape)
        group = selection.Group()
        if group.Layer.Name != view.name or bool(group.Layer.Master):
            group.MoveToLayer(layer)
        if below_shape is not None:
            group.OrderFrontOf(below_shape)
        else:
            group.OrderToBack()
        if name:
            group.Name = name
        group_id = str(group.StaticID)
    structure = walk_layer(layer)
    tops = [e.shape_id for e in structure.entries if len(e.path) == 1]
    children = [e.shape_id for e in structure.entries if e.parent_id == group_id and e.path[-1] > 0]
    expected = [i for i in stack if i not in targets]
    expected.insert(expected.index(below) if below else len(expected), group_id)
    problems = []
    if children != members:
        problems.append({"problem": "the group does not hold the shapes in their order", "children": children})
    if tops != expected:
        problems.append({"problem": "the group is not where the topmost member was", "layer": tops[:30]})
    if page_ids(doc, page) != step.before | {group_id}:
        problems.append({"problem": "other shapes changed on the page"})
    index = tops.index(group_id) if group_id in tops else None
    data = {"action": "group", "group_id": group_id, "layer": view.name, "member_ids": children, "name": name,
            "index": None if index is None else index + 1}
    if problems:
        data["verification_errors"] = problems
    return data


def _ungroup(doc, page, targets, shape_ids, recursive):
    children, removed = {}, set(shape_ids)
    for shape_id, (view, entry) in targets.items():
        below = [e for e in descendants(view, entry) if e.clip is None]  # PowerClip contents stay in containers
        if recursive:
            groups = {e.shape_id for e in below if e.type == GROUP}
            removed |= groups
            children[shape_id] = [e.shape_id for e in below if e.shape_id not in groups]
        else:
            children[shape_id] = [e.shape_id for e in below if e.parent_id == shape_id]
    shapes = [_com_shape(v, e, i) for i, (v, e) in targets.items()]
    with undoable(doc, page, "MCP group_shapes ungroup") as step:
        for shape in shapes:
            if recursive:
                shape.UngroupAll()
            else:
                shape.Ungroup()
    after = page_ids(doc, page)
    data = {"action": "ungroup_all" if recursive else "ungroup",
            "groups": [{"id": i, "layer": targets[i][0].name, "child_ids": children[i]} for i in shape_ids]}
    if after != step.before - removed:
        data["verification_errors"] = [{"problem": "the page's objects differ from the expected set",
                                        "unexpected": sorted(after - step.before)[:20],
                                        "missing": sorted((step.before - removed) - after)[:20]}]
    return data


def powerclip(
    document_path: str,
    page_index: int,
    container_id: str,
    action: Literal["place", "extract"] = "place",
    shape_ids: list[str] | None = None,
    center: bool = False,
) -> ToolResult:
    """Place exact shapes into a PowerClip container, or extract its contents; one verified undo step.

    place: shape_ids (not inside groups) keep their position unless center=true. extract: contents return above
    the container. Refuses locked/master layers and effect objects. Returns content_ids or extracted_ids.
    """
    if action not in ("place", "extract"):
        return ToolResult.fail("action must be place or extract")
    if action == "place" and not (isinstance(shape_ids, list) and shape_ids):
        return ToolResult.fail("place needs shape_ids")
    if action == "extract" and shape_ids:
        return ToolResult.fail("extract takes only container_id")
    if action == "place" and container_id in shape_ids:
        return ToolResult.fail("The container cannot also be content")
    ids = [*(shape_ids or []), container_id]

    def body(app, doc, page, targets):
        def extra(shape_id, view, entry):
            if shape_id == container_id:
                return None if entry.type in VECTOR_TYPES | {BITMAP} else "a container must be a single shape"
            return "inside a group; ungroup it first" if entry.parent is not None else None

        _in_place(targets, extra)
        view, entry = targets[container_id]
        container = _com_shape(view, entry, container_id)
        contents = [_com_shape(v, e, i) for i, (v, e) in targets.items() if i != container_id]
        out_ids = []
        with undoable(doc, page, f"MCP powerclip {action}") as step:
            if action == "place":
                selection = app.CreateShapeRange()
                for shape in contents:
                    selection.Add(shape)
                selection.AddToPowerClip(container, 1 if center else 0)
            else:
                extracted = container.PowerClip.ExtractShapes()
                out_ids = [str(extracted.Item(i).StaticID) for i in range(1, extracted.Count + 1)]
        structure = walk_layer(view.layer)
        inside = [e.shape_id for e in structure.entries if e.parent_id == container_id and e.path[-1] < 0]
        problems = []
        if page_ids(doc, page) != step.before:
            problems.append({"problem": "objects were added or removed"})
        if action == "place":
            if sorted(inside) != sorted(shape_ids):
                problems.append({"problem": "the container does not hold exactly the shapes", "content_ids": inside})
            data = {"action": "place", "container_id": container_id, "content_ids": inside}
        else:
            if inside:
                problems.append({"problem": "the container still holds contents", "content_ids": inside})
            data = {"action": "extract", "container_id": container_id, "extracted_ids": out_ids}
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, ids, body)


# --- delete_shapes ------------------------------------------------------------------------------------------------

def delete_shapes(document_path: str, page_index: int, shape_ids: list[str]) -> ToolResult:
    """Delete exact shapes (groups with their members) from one page; one verified undo step.

    Refuses, deleting nothing, locked/master layers, effect groups and effect-generated objects (delete the control
    shape), PowerClip contents (extract first), shapes inside a targeted group. Returns deleted_ids.
    """
    def body(app, doc, page, targets):
        _in_place(targets)
        members = {e.shape_id for view, entry in targets.values() for e in descendants(view, entry)}
        # One ShapeRange per layer (built with Shapes.Range per parent): one Delete instead of one per shape,
        # which costs ~0.13 s each inside a large group.
        by_layer = {}
        for view, entry in targets.values():
            by_layer.setdefault(id(view), (view, []))[1].append(entry)
        ranges = [shape_range(view, entries) for view, entries in by_layer.values()]
        with undoable(doc, page, "MCP delete_shapes") as step:
            for selection in ranges:
                selection.Delete()
        gone = set(shape_ids) | members
        after = page_ids(doc, page)
        data = {"deleted_ids": list(shape_ids), "members_deleted": len(members), "objects_left_on_page": len(after)}
        if after != step.before - gone:
            data["verification_errors"] = [{"problem": "the page's objects differ from the expected set",
                                            "still_there": sorted(after & gone)[:20],
                                            "also_missing": sorted((step.before - gone) - after)[:20]}]
        return data

    return _edit(document_path, page_index, shape_ids, body)
