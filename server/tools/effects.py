"""Effects on exact shapes: apply a contour, and separate effects (contour, drop shadow, extrude, blend...).

Effect details (contour direction, offset, steps, colors, group ID) are reported by inspect_shapes.
Both tools activate the document, refuse unsupported targets before changing anything, are one undo step
(a failure part way is undone) and return the read-back state.
"""

from __future__ import annotations

import math
from typing import Literal

from core import colorspec
from core.models import ToolResult
from core.shapeops import page_ids, refusal, undoable
from core.structure import EFFECT_GROUP_TYPES, invalidate, locate_entries
from core.units import MEASUREMENT_UNITS, convert, unit_name
from tools.colors import _com_shape, _edit, _read, _RefusedError, _resolve_color
from tools.inspection import _color_details, effect_controls

_CDR_CONTOUR = 6
_DIRECTIONS = {"inside": 0, "outside": 1, "to_center": 2}
_DIRECTION_NAMES = {value: name for name, value in _DIRECTIONS.items()}
_CORNERS = {"miter": 4, "round": 2, "bevel": 0}
_CORNER_NAMES = {4: "miter", 2: "round", 0: "bevel", 1: "offset_bevel", 3: "mitered_bevel", 5: "mitered_round"}
EFFECT_NAMES = {0: "blend", 1: "extrude", 2: "envelope", 3: "text_on_path", 4: "control_path", 5: "drop_shadow",
                6: "contour", 7: "distortion", 8: "perspective", 9: "lens", 10: "custom", 11: "inner_shadow"}
_SEPARABLE = {"contour": 6, "drop_shadow": 5, "extrude": 1, "blend": 0}
_GROUP_IDS = {5: "DropShadow.ShadowGroup", 6: "Contour.ContourGroup", 1: "Extrude.ExtrudeGroup",
              0: "Blend.BlendGroup"}


def contour_details(effect, unit) -> dict:
    """A contour effect's parameters (offset in document units) and its group's StaticID."""
    contour = effect.Contour
    details = {
        "type": "contour", "direction": _DIRECTION_NAMES.get(contour.Direction, "unknown"),
        "offset": contour.Offset, "document_unit": unit_name(unit), "steps": contour.Steps,
        "corner": _CORNER_NAMES.get(contour.CornerType, contour.CornerType),
        "fill_color": _color_details(contour.FillColor),
    }
    try:
        details["group_id"] = str(contour.ContourGroup.StaticID)
    except Exception:
        details["group_id"] = None
    return details


def effect_details(shape, unit) -> list[dict]:
    """Every effect on a shape: contour parameters, or the effect's type and group ID."""
    out = []
    for index in range(1, shape.Effects.Count + 1):
        effect = shape.Effects.Item(index)
        kind = effect.Type
        if kind == _CDR_CONTOUR:
            out.append(contour_details(effect, unit))
            continue
        record = {"type": EFFECT_NAMES.get(kind, f"effect_{kind}")}
        if kind in _GROUP_IDS:
            try:
                group = effect
                for name in _GROUP_IDS[kind].split("."):
                    group = getattr(group, name)
                record["group_id"] = str(group.StaticID)
            except Exception:
                record["group_id"] = None
        out.append(record)
    return out


def apply_contour(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    direction: Literal["inside", "outside", "to_center"],
    offset: float,
    unit: Literal["pt", "mm", "in", "cm"] = "pt",
    steps: int = 1,
    fill: str | dict | None = None,
    outline: str | dict | None = None,
    corner: Literal["miter", "round", "bevel"] = "miter",
) -> ToolResult:
    """Apply a contour effect to exact shapes that have none. One undo step, read back.

    offset: distance between steps, in unit. fill, outline: color specs (see set_fill; default: CorelDRAW's).
    Refuses groups, bitmaps and shapes with a contour. Returns each contour (direction, offset, steps, corner,
    fill, group_id). For one combined, measured underbase or spread shape use offset_silhouette.
    """
    if direction not in _DIRECTIONS or corner not in _CORNERS or unit not in MEASUREMENT_UNITS:
        return ToolResult.fail(f"direction inside/outside/to_center, corner miter/round/bevel, unit one of "
                               f"{', '.join(MEASUREMENT_UNITS)}")
    if not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset <= 0:
        return ToolResult.fail("offset must be a finite positive number")
    if not isinstance(steps, int) or not 1 <= steps <= 999:
        return ToolResult.fail("steps must be between 1 and 999")
    try:
        specs = [colorspec.parse(value) if value is not None else None for value in (fill, outline)]
    except colorspec.ColorSpecError as exc:
        return ToolResult.fail(str(exc))

    def body(app, doc, page, targets):
        unit_code = doc.Unit

        def extra(shape_id, view, entry):
            return refusal(view, entry, consume=True)

        _refuse(targets, extra)
        counts = _read(targets, [f"$item.Effects.toarray().filter($item.Type = {_CDR_CONTOUR}).count()"])
        _refuse(targets, lambda shape_id, view, entry: "already has a contour (inspect_shapes effect_details)"
                if counts[shape_id][0] else None)
        colors = [_resolve_color(app, doc, page, spec, targets) if spec is not None else None for spec in specs]
        document_offset = convert(offset, unit, unit_code)
        records, problems = [], []
        # Resolve every shape first: each new contour group shifts the stacking indices on its layer.
        shapes = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in targets.items()}
        with undoable(doc, page, "MCP apply_contour"):
            for shape_id, shape in shapes.items():
                # Dynamic pywin32 needs explicit nulls for the optional color arguments.
                effect = shape.CreateContour(_DIRECTIONS[direction], document_offset, steps, 0,
                                             colors[1].color if colors[1] and not colors[1].none else None,
                                             colors[0].color if colors[0] and not colors[0].none else None, None,
                                             0, 0, 2, _CORNERS[corner], 15.0)
                if effect is None:
                    raise RuntimeError(f"CorelDRAW created no contour on {shape_id} (the offset may remove the shape)")
                fill_identity = colorspec.identity(colorspec.value_of(effect.Contour.FillColor))
                records.append({"id": shape_id, "contour": contour_details(effect, unit_code),
                                "_fill": fill_identity})
        tolerance = convert(0.001, "pt", unit_code) + 1e-9
        for record in records:
            contour = record["contour"]
            if (contour["direction"] != direction or contour["steps"] != steps or contour["corner"] != corner
                    or not math.isclose(contour["offset"], document_offset, rel_tol=1e-6, abs_tol=tolerance)):
                problems.append({"id": record["id"], "contour": contour})
            fill_identity = record.pop("_fill")
            if colors[0] is not None and not colors[0].none and not colorspec.same(fill_identity, colors[0].identity):
                problems.append({"id": record["id"], "problem": "contour fill color differs", "fill": fill_identity})
        invalidate()
        data = {"shapes": records}
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _refuse(targets, reason_of):
    refused = [{"id": shape_id, "layer": view.name, "reason": reason}
               for shape_id, (view, entry) in targets.items() if (reason := reason_of(shape_id, view, entry))]
    if refused:
        raise _RefusedError(refused)


def separate_effect(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    effect: Literal["contour", "drop_shadow", "extrude", "blend"] = "contour",
) -> ToolResult:
    """Break an effect apart into plain objects (Break Contour/Shadow Apart), given control shapes or their
    effect groups. One undo step. Refuses shapes without exactly one such effect. Returns per shape control_id,
    the new top-level result_ids and every leaf_id.
    """
    if effect not in _SEPARABLE:
        return ToolResult.fail(f"effect must be one of {', '.join(_SEPARABLE)}")
    kind = _SEPARABLE[effect]

    def body(app, doc, page, targets):
        controls = {}
        for shape_id, (view, entry) in targets.items():
            if entry.type in EFFECT_GROUP_TYPES:
                links = effect_controls(view)
                controls[shape_id] = links.get(shape_id)
            else:
                controls[shape_id] = shape_id
        unresolved = [{"id": i, "layer": targets[i][0].name, "reason": "no control shape found for this effect group"}
                      for i, control in controls.items() if control is None]
        if unresolved:
            raise _RefusedError(unresolved)
        located = locate_entries(doc, page, list(dict.fromkeys(controls.values())))
        counts = _read(located, [f"$item.Effects.toarray().filter($item.Type = {kind}).count()"])
        refused = []
        for shape_id, control in controls.items():
            view, entry = located[control]
            reason = refusal(view, entry, consume=True)
            if reason is None and counts[control][0] != 1:
                reason = f"has {counts[control][0]} {effect} effects (needs exactly one)"
            if reason:
                refused.append({"id": shape_id, "layer": view.name, "reason": reason})
        if refused:
            raise _RefusedError(refused)
        records = []
        # Resolve every control first: separating one effect adds shapes and shifts the stacking indices.
        shapes = {control: _com_shape(*located[control], control) for control in dict.fromkeys(controls.values())}
        with undoable(doc, page, "MCP separate_effect") as step:
            for shape_id, control in controls.items():
                shape = shapes[control]
                found = [shape.Effects.Item(i) for i in range(1, shape.Effects.Count + 1)
                         if shape.Effects.Item(i).Type == kind]
                pieces = found[0].Separate()
                items = [pieces.Item(i) for i in range(1, pieces.Count + 1)]
                generated = [item for item in items if str(item.StaticID) != control]
                records.append({"id": shape_id, "control_id": control,
                                "result_ids": [str(item.StaticID) for item in generated],
                                "leaf_ids": [leaf for item in generated for leaf in _leaf_ids(item)]})
        invalidate()
        after = locate_entries(doc, page, list(dict.fromkeys(controls.values())))
        remaining = _read(after, [f"$item.Effects.toarray().filter($item.Type = {kind}).count()"])
        new_ids = {leaf for record in records for leaf in record["leaf_ids"] + record["result_ids"]}
        missing = new_ids - page_ids(doc, page)
        problems = [{"id": record["id"], "problem": "the effect is still there"}
                    for record in records if remaining[record["control_id"]][0]]
        problems += [{"problem": "no objects were generated", "id": r["id"]} for r in records if not r["result_ids"]]
        if missing:
            problems.append({"problem": f"generated IDs not found: {sorted(missing)[:20]}"})
        data = {"effect": effect, "shapes": records, "new_object_count": len(page_ids(doc, page) - step.before)}
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _leaf_ids(shape):
    if shape.Type == 7 or shape.Type in EFFECT_GROUP_TYPES:
        for index in range(1, shape.Shapes.Count + 1):
            yield from _leaf_ids(shape.Shapes.Item(index))
    else:
        yield str(shape.StaticID)

