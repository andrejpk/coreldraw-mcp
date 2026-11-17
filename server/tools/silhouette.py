"""offset_silhouette: the welded outline of exact objects, offset inward or outward, as one new shape.

How it is built (one CorelDRAW command group, so one undo step; a failure part way is undone):

1. Inputs expand to their visible leaves (groups and, when the sources are kept, effect groups). Leaves whose fill
   or outline matches ``exclude_colors`` (the shared color spec, matched as ``find_shapes`` does: fills incl.
   fountain stops, outlines and every text run) are skipped and reported, as are hidden leaves and leaves without
   fill or outline.
2. Each leaf contributes its painted area as a curve: the fill area is its display curve; an outline contributes
   its stroked area, taken from a temporary copy whose outline is converted to an object (CorelDRAW's
   Convert Outline To Object: width, caps, joins and pen shape included). Text is copied and converted to curves
   first. Copies are made on the result layer and deleted again.
3. The curves are welded in memory (``Curve.WeldWith``, the same union as Shape.Weld; counters such as the hole
   in an "O" stay open, unlike Create Boundary which fills them) and offset with ``Curve.Contour`` (CorelDRAW's
   contour geometry: same result as a contour effect broken apart, without the temporary effect objects).
4. The result is created on the target layer, filled, outline removed, fill overprint set, named, and placed
   behind the backmost participating object on that layer (or on top of the layer when none is there).

Verification reads the result back: StaticIDs on the page (only the result is new; sources kept or removed as
asked), layer, paint, area with holes subtracted, bounds, and the inset (or outset) actually achieved: the mean
from the area change divided by the silhouette's perimeter, and the minimum per side of the bounds.
"""

from __future__ import annotations

import math
from typing import Literal

import pywintypes
from core import colorspec
from core.connection import get_connection
from core.models import ToolResult
from core.shapeops import (
    TEXT,
    descendants,
    geometry,
    holds_powerclip,
    is_container,
    page_ids,
    refusal,
    rounded,
    to_unit,
    undoable,
)
from core.shapeops import (
    target_layer as resolve_layer,
)
from core.structure import locate_entries, resolve_path
from core.units import MEASUREMENT_UNITS, convert, unit_name
from tools import find
from tools.colors import _com_shape, _edit, _read, _RefusedError, _resolve_color, _states

_DIRECTIONS = {"inside": 0, "outside": 1}
_CORNERS = {"miter": 4, "round": 2, "bevel": 0}  # cdrContourCornerType: mitered offset bevel (default), round, bevel
_SQUARE_CAP, _MITER_LIMIT = 2, 15.0  # CorelDRAW's contour defaults
_MAX_LISTED = 200
# Measured: 4 objects 1.4 s, 126 (Good News) 4.2 s; about 1,870 overran the 60 s bridge limit (the in-memory
# union grows with every weld). Larger selections must be split until a faster union is validated.
_MAX_PARTICIPANTS = 400
_PAINT = ["$item.Visible", f"iif($item.Type = {TEXT}, -1, $item.Fill.Type)",
          f"iif($item.Type = {TEXT}, -1, $item.Outline.Type)", f"iif($item.Type = {TEXT}, 0, $item.Outline.Width)"]


def offset_silhouette(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    offset: float,
    unit: Literal["pt", "mm", "in", "cm"] = "pt",
    direction: Literal["inside", "outside"] = "inside",
    exclude_colors: list[str | dict] | None = None,
    color: str | dict | None = None,
    target_layer: str | None = None,
    overprint_fill: bool | None = None,
    keep_sources: bool = True,
    name: str | None = None,
    corner: Literal["miter", "round", "bevel"] = "miter",
) -> ToolResult:
    """One new shape from the welded painted area of exact objects, offset inside (choke) or outside (spread).

    Fills, outline strokes and text (converted on copies) are welded across layers and groups; holes stay
    open. offset in unit (0 = none), CorelDRAW contour geometry, corner miter|round|bevel. exclude_colors:
    color specs as in set_fill; objects whose fill, outline or any text run matches are skipped (listed).
    color: result fill (default: the first object's fill); overprint_fill; name. target_layer: editable
    layer of this page (default: the first object's layer); the result goes behind the backmost object
    there. keep_sources=false deletes the participants. Refuses bitmaps, PowerClips, master-layer objects.
    Returns result_id, layer, area (holes subtracted), bounds, silhouette (before the offset), offset
    achieved (mean = area change / perimeter; min per side; in unit), participants, skipped, fill.
    """
    if unit not in MEASUREMENT_UNITS:
        return ToolResult.fail(f"unit must be one of {', '.join(MEASUREMENT_UNITS)}")
    if direction not in _DIRECTIONS or corner not in _CORNERS:
        return ToolResult.fail("direction must be inside or outside; corner miter, round or bevel")
    if not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0:
        return ToolResult.fail("offset must be a finite number >= 0")
    try:
        excludes = [(spec, colorspec.parse(spec)) for spec in (exclude_colors or [])]
        for _, parsed in excludes:
            colorspec.Matcher(parsed)
        fill_spec = colorspec.parse(color) if color is not None else None
    except colorspec.ColorSpecError as exc:
        return ToolResult.fail(str(exc))
    conn = get_connection()

    def body(app, doc, page, targets):
        document_unit = doc.Unit
        layer, leaves = _inputs(page, targets, keep_sources, target_layer)
        participants, skipped = _participants(conn, document_path, page_index, leaves, excludes)
        if not participants:
            raise _RefusedError([{"id": shape_id, "layer": view.name, "reason": f"nothing takes part: {_summary(skipped)}"}
                                 for shape_id, (view, _) in targets.items()])
        if len(participants) > _MAX_PARTICIPANTS:
            raise ValueError(f"{len(participants)} objects take part; at most {_MAX_PARTICIPANTS} fit in one call. "
                             "Split shape_ids, or weld parts first with combine_shapes")
        spec = fill_spec or _donor_spec(participants)
        resolved = _resolve_color(app, doc, page, spec)
        behind = _backmost(participants, layer)
        sources = {leaf: _com_shape(view, entry, leaf) for leaf, (view, entry, _, _) in participants.items()}
        document_offset = convert(offset, unit, document_unit)
        with undoable(doc, page, "MCP offset_silhouette") as step:
            union, union_shape, temps = _union(app, doc, layer, participants, sources)
            union_geometry = geometry(union_shape, document_unit)
            if document_offset > 0:
                curve = union.Contour(document_offset, _DIRECTIONS[direction], _SQUARE_CAP, _CORNERS[corner],
                                      _MITER_LIMIT)
                if curve is None or not curve.SubPaths.Count:
                    raise ValueError("The offset removes the whole silhouette")
            else:
                curve = union
            result = layer.CreateCurve(curve)
            for temp in temps:
                if _alive(temp):
                    temp.Delete()
            if resolved.none:
                result.Fill.ApplyNoFill()
            else:
                result.Fill.ApplyUniformFill(resolved.color)
            result.Outline.SetNoOutline()
            result.OverprintFill = bool(overprint_fill)
            if name:
                result.Name = name
            if behind is not None:
                result.OrderBackOf(behind)
            if not keep_sources:
                for source in sources.values():
                    source.Delete()
            result_id = str(result.StaticID)
        return _verify(doc, page, step.before, result, result_id, union_geometry, participants, skipped, layer,
                       resolved, document_unit, document_offset)

    def _verify(doc, page, before, result, result_id, union_geometry, participants, skipped, layer, resolved,
                document_unit, document_offset):
        after = page_ids(doc, page)
        expected = (before - (set() if keep_sources else set(participants))) | {result_id}
        problems = []
        if after != expected:
            problems.append({"problem": "the page's objects differ from the expected set",
                             "unexpected": sorted(after - expected)[:20], "missing": sorted(expected - after)[:20]})
        located = locate_entries(doc, page, [result_id])
        view, entry = located[result_id]
        measured = geometry(result, document_unit)
        state = _states(located, document_unit, ("fill", "outline", "flags"))[result_id]
        if view.name != layer.Name:
            problems.append({"problem": f"the result is on layer {view.name!r}"})
        if not colorspec.same(state.get("fill") or {}, resolved.identity):
            problems.append({"problem": "the result's fill differs", "fill": state.get("fill")})
        if state["overprint"]["fill"] != bool(overprint_fill):
            problems.append({"problem": "the fill overprint flag differs"})
        inset, warnings = _inset(union_geometry, measured, document_offset, document_unit, problems)
        inset["achieved_mean"] = round(to_unit(inset["achieved_mean"], document_unit, unit), 4)
        inset["achieved_min_side"] = round(to_unit(inset["achieved_min_side"], document_unit, unit), 4)
        inset["sides"] = {side: round(to_unit(value, document_unit, unit), 4) for side, value in inset["sides"].items()}
        data = {
            "result_id": result_id, "layer": view.name, "name": name, "document_unit": unit_name(document_unit),
            **rounded({key: measured[key] for key in ("area", "perimeter", "subpaths", "holes", "bounds")}),
            "silhouette": rounded({key: union_geometry[key] for key in ("area", "perimeter", "subpaths", "holes",
                                                                         "bounds")}),
            "offset": {"direction": direction, "requested": offset, "unit": unit, "corner": corner, **inset},
            "fill": state.get("fill"), "palette": resolved.info.get("palette"), "overprint": state["overprint"],
            "sources_kept": keep_sources, "participant_count": len(participants),
            "participants": [{"id": leaf, "source": source, "parts": parts}
                             for leaf, (_, _, source, parts) in list(participants.items())[:_MAX_LISTED]],
            "skipped_count": len(skipped), "skipped": skipped[:_MAX_LISTED],
        }
        if warnings:
            data["warnings"] = warnings
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _inputs(page, targets, keep_sources, layer_name):
    """The result layer and every leaf {id: (view, entry, input id)}; refuses what cannot take part."""
    refused = []

    def refuse(shape_id, view, reason):
        refused.append({"id": shape_id, "layer": view.name, "reason": reason})

    leaves = {}
    for shape_id, (view, entry) in targets.items():
        reason = refusal(view, entry, consume=not keep_sources, groups=True, effects=keep_sources)
        if reason is None and holds_powerclip(view, entry):
            reason = "holds PowerClip contents; extract them first"
        if reason:
            refuse(shape_id, view, reason)
            continue
        if not is_container(entry):
            leaves.setdefault(entry.shape_id, (view, entry, shape_id))
            continue
        for item in descendants(view, entry):
            if is_container(item):
                if holds_powerclip(view, item):
                    refuse(shape_id, view, f"member {item.shape_id} holds PowerClip contents; extract them first")
                    break
                continue
            reason = refusal(view, item, consume=not keep_sources, effects=keep_sources)
            if reason:
                refuse(shape_id, view, f"member {item.shape_id}: {reason}")
                break
            leaves.setdefault(item.shape_id, (view, item, shape_id))
    if refused:
        raise _RefusedError(refused)
    if layer_name is not None:
        layer = resolve_layer(page, layer_name)
    else:
        view = next(iter(targets.values()))[0]
        if not view.editable:
            raise ValueError(f"The first shape's layer {view.name!r} is locked; pass target_layer")
        layer = view.layer
    return layer, leaves


def _participants(conn, document_path, page_index, leaves, excludes):
    """Split leaves into participants {id: (view, entry, input id, parts)} and skipped records."""
    skipped, excluded = [], {}
    ids = list(leaves)
    layers = sorted({view.name for view, _, _ in leaves.values()})
    for spec, _ in excludes:
        scan = find._scan(conn, document_path, [page_index], layers, (None, {"ids": ids, "color": spec}, None, None,
                                                                       None))
        for match in scan["matches"]:
            excluded.setdefault(match["id"], (spec, match.get("color_parts")))
    hidden = _hidden(leaves)
    rows = _read({leaf: (view, entry) for leaf, (view, entry, _) in leaves.items()}, _PAINT)
    participants = {}
    for leaf, (view, entry, source) in leaves.items():
        visible, fill_type, outline_type, width = rows[leaf]
        if leaf in excluded:
            spec, parts = excluded[leaf]
            skipped.append({"id": leaf, "source": source, "reason": "excluded_color", "color": spec, "parts": parts})
        elif not visible or leaf in hidden:
            skipped.append({"id": leaf, "source": source, "reason": "hidden"})
        elif entry.type == TEXT:
            participants[leaf] = (view, entry, source, ["text"])
        else:
            parts = (["fill"] if fill_type else []) + (["outline"] if outline_type and width > 0 else [])
            if parts:
                participants[leaf] = (view, entry, source, parts)
            else:
                skipped.append({"id": leaf, "source": source, "reason": "no_fill_no_outline"})
    return participants, skipped


def _hidden(leaves):
    """Leaves inside a hidden group (a hidden container hides everything below it)."""
    containers = {}
    for view, entry, _ in leaves.values():
        structure = view.structure
        parent = entry.parent
        while parent:
            index = next((i for i, item in enumerate(structure.entries) if item.path == parent), None)
            if index is None:
                break
            item = structure.entries[index]
            containers.setdefault(item.shape_id, (view, item))
            parent = item.parent
    if not containers:
        return set()
    visible = _read(containers, ["$item.Visible"])
    hidden_paths = {(id(view), item.path) for shape_id, (view, item) in containers.items() if not visible[shape_id][0]}
    return {leaf for leaf, (view, entry, _) in leaves.items()
            if any((id(view), entry.path[:size]) in hidden_paths for size in range(1, len(entry.path)))}


def _summary(skipped):
    counts = {}
    for record in skipped:
        counts[record["reason"]] = counts.get(record["reason"], 0) + 1
    return ", ".join(f"{count} {reason}" for reason, count in counts.items()) or "no shapes"


def _donor_spec(participants):
    for leaf, (_, entry, _, parts) in participants.items():
        if entry.type != TEXT:
            return colorspec.parse({"from_shape_id": leaf, "part": "fill" if "fill" in parts else "outline"})
    raise ValueError("Every participating object is text; pass color")


def _backmost(participants, layer):
    """The top-level shape of the backmost participant on the result layer (resolved before any edit), or None."""
    best = None
    for view, entry, _, _ in participants.values():
        if view.master or view.name != layer.Name or view.layer.Page.Index != layer.Page.Index:
            continue
        if best is None or entry.path[0] > best[1]:
            best = (view, entry.path[0])
    return None if best is None else resolve_path(best[0].layer, (best[1],))


def _painted_curves(shape, parts, layer, temps):
    """Curves of a shape's painted area: its display curve (fill) and its stroke as an object (outline)."""
    if parts == ["fill"]:
        return [shape.DisplayCurve]
    copy = shape.CopyToLayer(layer)
    temps.append(copy)
    if "text" in parts:
        copy.ConvertToCurves()
    curves = []
    for part in list(_leaf_shapes(copy)):
        filled = bool(part.Fill.Type)
        if filled:
            curves.append(part.DisplayCurve)
        if part.Outline.Type and part.Outline.Width > 0:
            # CorelDRAW removes a part that has no fill once its outline became an object.
            stroke = part.Outline.ConvertToObject()
            if stroke is not None:
                temps.append(stroke)
                curves.append(stroke.DisplayCurve)
    return curves


def _alive(shape) -> bool:
    """False for a COM shape that CorelDRAW has removed (an outline-only part after Convert Outline To Object)."""
    try:
        shape.StaticID
    except pywintypes.com_error:
        return False
    return True


def _leaf_shapes(shape):
    if shape.Type == 7:
        for index in range(1, shape.Shapes.Count + 1):
            yield from _leaf_shapes(shape.Shapes.Item(index))
    else:
        yield shape


def _union(app, doc, layer, participants, sources):
    """Weld every painted curve in memory; returns (curve, a temporary shape of it, temporary shapes)."""
    temps, curves = [], []
    for leaf, (_, _, _, parts) in participants.items():
        curves.extend(_painted_curves(sources[leaf], parts, layer, temps))
    if not curves:
        raise ValueError("The participating objects have no painted area")
    union = app.CreateCurve(doc)
    union.AppendCurve(curves[0])
    for curve in curves[1:]:
        welded = union.WeldWith(curve)
        if welded is not None:
            union = welded
    union_shape = layer.CreateCurve(union)
    temps.append(union_shape)
    return union, union_shape, temps


def _inset(before, after, offset, document_unit, problems):
    """Inset (inside) or outset (outside) achieved, in document units, from areas and bounds."""
    tolerance = max(convert(0.01, "pt", document_unit), 0.02 * offset)
    inside = after["area"] <= before["area"]
    b, a = before["bounds"], after["bounds"]
    if inside:
        sides = {"left": a[0] - b[0], "bottom": a[1] - b[1], "right": b[2] - a[2], "top": b[3] - a[3]}
        mean = (before["area"] - after["area"]) / before["perimeter"] if before["perimeter"] else 0.0
    else:
        sides = {"left": b[0] - a[0], "bottom": b[1] - a[1], "right": a[2] - b[2], "top": a[3] - b[3]}
        mean = (after["area"] - before["area"]) / before["perimeter"] if before["perimeter"] else 0.0
    warnings = []
    if offset > 0 and min(sides.values()) < offset - tolerance:
        problems.append({"problem": "a side moved less than the offset", "sides": sides})
    if offset == 0 and abs(after["area"] - before["area"]) > tolerance * max(before["perimeter"], 1.0):
        problems.append({"problem": "the area changed without an offset"})
    if offset > 0 and not 0.7 * offset <= mean <= 1.3 * offset:
        warnings.append("mean_offset_differs: thin parts disappeared, or sharp corners dominate (see sides)")
    if after["subpaths"] < before["subpaths"]:
        warnings.append(f"parts_removed: {before['subpaths'] - after['subpaths']} subpaths fewer than the silhouette")
    return {"achieved_mean": mean, "achieved_min_side": min(sides.values()), "sides": sides,
            "direction_achieved": "inside" if inside else "outside"}, warnings
