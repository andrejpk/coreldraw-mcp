"""offset_silhouette: the welded outline of exact objects, offset inward or outward, as one new shape.

How it is built (one CorelDRAW command group, so one undo step; a failure part way is undone). Every step is a
shape-level operation on temporary copies (the path of the old weld_copies_by_id + apply_contour +
break_contour_apart tools); no in-memory curve geometry (``Curve.WeldWith`` crashed CorelDRAW in E13):

1. Inputs expand to their visible leaves (groups). Leaves whose fill or outline matches ``exclude_colors`` (the
   shared color spec, matched as ``find_shapes`` does: fills incl. fountain stops, outlines and every text run)
   are skipped and reported, as are hidden leaves and leaves without fill or outline. Effect groups and
   effect-generated objects are refused (copying them and editing the copies is untested and was part of a
   crash sequence in E13); pass the effect's control shape, or separate_effect first.
2. Leaves are copied to the result layer. Fill-only leaves go in sets whose bounding boxes neither overlap nor
   touch: one ``ShapeRange.CopyToLayer`` and one ``Combine`` per set (shapes that cannot overlap keep exactly
   their area when combined). Text and outlined leaves are copied one by one; text copies are converted to
   curves, outlines to objects (Convert Outline To Object: width, caps, joins and pen shape included).
3. The pieces are welded with ``Shape.Weld`` in pairs (a balanced tree, so intermediate shapes stay small).
   Counters such as the hole in an "O" stay open, unlike Create Boundary which fills them. The edit stops and
   is undone once it has run ``_BUDGET_SECONDS``, well inside the bridge's 60 s limit.
4. The offset is a one-step contour effect on the welded shape, separated (Break Contour Apart); the welded
   shape and every other temporary are deleted.
5. The result is filled, outline removed, fill overprint set, named, and placed behind the backmost
   participating object on the target layer (or on top of the layer when none is there).

Verification reads the result back: StaticIDs on the page (only the result is new; sources kept or removed as
asked), layer, paint, area with holes subtracted, bounds, and the inset (or outset) actually achieved: the mean
from the area change divided by the silhouette's perimeter, and the minimum per side of the bounds.
"""

from __future__ import annotations

import math
import time
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
    optimized,
    page_ids,
    refusal,
    rounded,
    to_unit,
    undoable,
)
from core.shapeops import (
    target_layer as resolve_layer,
)
from core.structure import StaleStructureError, locate_entries, resolve_path
from core.units import MEASUREMENT_UNITS, convert, unit_name
from tools import find
from tools.colors import _edit, _read, _RefusedError, _resolve_color, _states

_DIRECTIONS = {"inside": 0, "outside": 1}
_CORNERS = {"miter": 4, "round": 2, "bevel": 0}  # cdrContourCornerType: mitered offset bevel (default), round, bevel
_SQUARE_CAP, _MITER_LIMIT = 2, 15.0  # CorelDRAW's contour defaults
_MAX_LISTED = 200
# Every copy, combine and weld is a CorelDRAW command (about 0.1-0.2 s each on real art) inside one bridge call,
# which the bridge cancels at 60 s. The edit stops and is undone once it has run this long.
_BUDGET_SECONDS = 35.0
_MAX_PARTICIPANTS = 1000  # resolving and reading them happens before the budget starts
_BOUNDS = ["$item.LeftX", "$item.BottomY", "$item.RightX", "$item.TopY"]
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
    color specs as in set_fill; objects whose fill, outline or any text run matches are skipped (listed;
    specs that matched nothing are in exclude_unmatched). color: result fill (default: the first object's
    fill); overprint_fill; name. target_layer: editable layer of this page (default: the first object's
    layer); the result goes behind the backmost object there. keep_sources=false deletes the participants.
    Refuses bitmaps, PowerClips, master-layer objects, effect groups and effect-generated objects (pass the
    control shape, or separate_effect first). One undo step. Fills are fast (100 in ~6 s); outlined objects
    and text cost ~0.3 s each, and a call that runs past 35 s is undone (split shape_ids).
    Returns result_id, layer, area (holes subtracted), bounds, silhouette (before the offset), offset
    achieved (mean = area change / perimeter; min per side; in unit), participants, skipped, fill, seconds.
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
        clock = [time.perf_counter()]
        seconds = {}

        def lap(name):
            now = time.perf_counter()
            seconds[name] = round(now - clock[0], 3)
            clock[0] = now

        document_unit = doc.Unit
        layer, leaves = _inputs(page, targets, keep_sources, target_layer)
        participants, skipped, unmatched = _participants(conn, document_path, page_index, leaves, excludes)
        if not participants:
            raise _RefusedError([{"id": shape_id, "layer": view.name, "reason": f"nothing takes part: {_summary(skipped)}"}
                                 for shape_id, (view, _) in targets.items()])
        if len(participants) > _MAX_PARTICIPANTS:
            raise ValueError(f"{len(participants)} objects take part; at most {_MAX_PARTICIPANTS} in one call. "
                             "Split shape_ids")
        spec = fill_spec or _donor_spec(participants)
        resolved = _resolve_color(app, doc, page, spec)
        behind = _backmost(participants, layer)
        sources = _resolve_all(participants)
        plain = [leaf for leaf, (_, _, _, parts) in participants.items() if parts == ["fill"]]
        bounds = _read({leaf: participants[leaf][:2] for leaf in plain}, _BOUNDS) if plain else {}
        document_offset = convert(offset, unit, document_unit)
        lap("prepare")
        deadline = clock[0] + _BUDGET_SECONDS - sum(seconds.values())
        with optimized(app), undoable(doc, page, "MCP offset_silhouette") as step:
            union_shape, temps = _union(app, layer, participants, sources, bounds, lap, deadline)
            union_geometry = geometry(union_shape, document_unit)
            if document_offset > 0:
                result = _contour(union_shape, document_offset, direction, corner)
            else:
                result = union_shape
            lap("offset")
            keep = str(result.StaticID)
            for temp in temps:
                if _alive(temp) and str(temp.StaticID) != keep:
                    temp.Delete()
            if result.Layer.Name != layer.Name:
                result.MoveToLayer(layer)
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
            lap("finish")
        data = _verify(doc, page, step.before, result, result_id, union_geometry, participants, skipped, layer,
                       resolved, document_unit, document_offset)
        lap("verify")
        # A misspelled exclusion would silently put that ink into the result: name the specs that matched nothing.
        data["exclude_unmatched"] = unmatched
        data["seconds"] = seconds
        return data

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
        reason = refusal(view, entry, consume=not keep_sources, groups=True)
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
            reason = refusal(view, item, consume=not keep_sources)
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
    """Participants {id: (view, entry, input id, parts)}, skipped records, and exclusion specs that matched nothing."""
    skipped, excluded, unmatched = [], {}, []
    ids = list(leaves)
    layers = sorted({view.name for view, _, _ in leaves.values()})
    for spec, _ in excludes:
        scan = find._scan(conn, document_path, [page_index], layers, (None, {"ids": ids, "color": spec}, None, None,
                                                                       None))
        if not scan["matches"]:
            unmatched.append(spec)
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
    return participants, skipped, unmatched


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


def _painted_shapes(shape, parts, layer, temps):
    """Temporary shapes on ``layer`` covering a shape's painted area: its fill, and its stroke as an object."""
    copy = shape.CopyToLayer(layer)
    temps.append(copy)
    if "text" in parts:
        copy.ConvertToCurves()
    copies = [copy]
    if copy.Type == 7:  # mixed text can convert to a group: every piece becomes its own top-level temporary
        ungrouped = copy.UngroupAllEx()
        copies = [ungrouped.Item(index) for index in range(1, ungrouped.Count + 1)]
        temps.extend(copies)
    out = []
    for part in copies:
        filled = bool(part.Fill.Type)
        if part.Outline.Type and part.Outline.Width > 0:
            # CorelDRAW removes a part that has no fill once its outline became an object.
            stroke = part.Outline.ConvertToObject()
            if stroke is not None:
                temps.append(stroke)
                out.append(stroke)
        if filled:
            out.append(part)
    return out


def _alive(shape) -> bool:
    """False for a COM shape that CorelDRAW has removed (welded, or outline-only after Convert Outline To Object)."""
    try:
        shape.StaticID
    except pywintypes.com_error:
        return False
    return True


def _resolve_all(participants):
    """COM shapes of every participant, each StaticID verified; shared group prefixes are resolved once."""
    cache, out = {}, {}
    for leaf, (view, entry, _, _) in participants.items():
        shape = None
        for depth in range(len(entry.path), 0, -1):
            shape = cache.get((id(view), entry.path[:depth]))
            if shape is not None:
                break
        else:
            depth = 0
        for size in range(depth + 1, len(entry.path) + 1):
            index = entry.path[size - 1]
            if size == 1:
                shape = view.layer.Shapes.Item(index)
            else:
                shape = shape.PowerClip.Shapes.Item(-index) if index < 0 else shape.Shapes.Item(index)
            cache[(id(view), entry.path[:size])] = shape
        if str(shape.StaticID) != leaf:
            raise StaleStructureError(f"Shape {leaf} is no longer at its cached position")
        out[leaf] = shape
    return out


def _disjoint_sets(bounds):
    """Group IDs so that the bounding boxes within a group neither overlap nor touch (greedy, left to right)."""
    sets = []
    for leaf in sorted(bounds, key=lambda key: bounds[key][0]):
        left, bottom, right, top = bounds[leaf]
        for members in sets:
            if not any(left <= o[2] and o[0] <= right and bottom <= o[3] and o[1] <= top for o in members[1]):
                members[0].append(leaf)
                members[1].append(bounds[leaf])
                break
        else:
            sets.append(([leaf], [bounds[leaf]]))
    return [members[0] for members in sets]


def _union(app, layer, participants, sources, bounds, lap=lambda name: None, deadline=None):
    """Weld temporary copies of every painted part; returns (welded shape, temporaries).

    Fill-only objects are copied in sets whose bounding boxes neither overlap nor touch, one CopyToLayer and one
    Combine per set (combining shapes that cannot overlap keeps exactly their area, holes included). Text and
    outlines are copied one by one and converted. The pieces are then welded pairwise with Shape.Weld.
    """
    def budget(done, total, stage):
        if deadline is not None and time.perf_counter() > deadline:
            raise TimeoutError(f"Stopped after {_BUDGET_SECONDS:.0f} s at {stage} ({done} of {total}) to stay under "
                               "the bridge's 60 s limit; split shape_ids into smaller calls")

    temps, pieces = [], []
    others = [leaf for leaf in participants if leaf not in bounds]
    for done, leaf in enumerate(others):
        budget(done, len(others), "copying text and outlines")
        pieces.extend(_painted_shapes(sources[leaf], participants[leaf][3], layer, temps))
    groups = _disjoint_sets(bounds)
    for done, group in enumerate(groups):
        budget(done, len(groups), "copying fills")
        selection = app.CreateShapeRange()
        for leaf in group:
            selection.Add(sources[leaf])
        copies = selection.CopyToLayer(layer)
        items = [copies.Item(index) for index in range(1, copies.Count + 1)]
        if len(items) != len(group):
            raise RuntimeError(f"CorelDRAW copied {len(items)} of {len(group)} shapes")
        temps.extend(items)
        if len(items) > 1:
            combined = copies.Combine()
            if combined is None:
                raise RuntimeError("CorelDRAW did not combine the copies")
            temps.append(combined)
            pieces.append(combined)
        else:
            pieces.extend(items)
    lap("copies")
    if not pieces:
        raise ValueError("The participating objects have no painted area")
    welds, total = 0, len(pieces) - 1
    while len(pieces) > 1:
        welded = []
        for index in range(0, len(pieces) - 1, 2):
            budget(welds, total, "welding")
            result = pieces[index + 1].Weld(pieces[index], False, False)
            if result is None:
                raise RuntimeError("CorelDRAW did not return a weld result")
            welds += 1
            temps.append(result)
            welded.append(result)
        if len(pieces) % 2:
            welded.append(pieces[-1])
        pieces = welded
    union = pieces[0]
    union.Outline.SetNoOutline()
    lap("weld")
    return union, temps


def _contour(shape, offset, direction, corner):
    """A one-step contour of ``shape``, separated; returns the contour as one curve (``shape`` stays a temporary)."""
    control_id = str(shape.StaticID)
    effect = shape.CreateContour(_DIRECTIONS[direction], offset, 1, 0, None, None, None, 0, 0, _SQUARE_CAP,
                                 _CORNERS[corner], _MITER_LIMIT)
    if effect is None:
        raise ValueError("The offset removes the whole silhouette")
    separated = effect.Separate()
    if separated is None:  # CorelDRAW made an empty contour: nothing is left at this inset
        raise ValueError("The offset removes the whole silhouette")
    generated = [item for item in (separated.Item(i) for i in range(1, separated.Count + 1))
                 if str(item.StaticID) != control_id]
    leaves = [leaf for item in generated for leaf in _ungrouped(item)]
    if not leaves:
        raise ValueError("The offset removes the whole silhouette")
    if len(leaves) == 1:
        return leaves[0]
    pieces = get_connection().app.CreateShapeRange()
    for leaf in leaves:
        pieces.Add(leaf)
    combined = pieces.Combine()
    if combined is None:
        raise RuntimeError("CorelDRAW did not combine the contour pieces")
    return combined


def _ungrouped(shape):
    """The curves of a separated contour group (ungrouped), or the shape itself."""
    if shape.Type != 7:
        return [shape]
    children = shape.UngroupEx()
    return [leaf for i in range(1, children.Count + 1) for leaf in _ungrouped(children.Item(i))]


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
