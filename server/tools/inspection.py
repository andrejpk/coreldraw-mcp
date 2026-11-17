"""Inspect drawing objects and bridge exact IDs to CorelDRAW UI selection."""

from itertools import groupby
from typing import Literal

from core.connection import get_connection
from core.drawing import (
    CDR_COLOR_CMYK,
    CDR_COLOR_PANTONE,
    CDR_COLOR_REGISTRATION,
    CDR_COLOR_RGB,
)
from core.models import ToolResult
from core.structure import (
    CONTAINER_TYPES,
    EFFECT_GROUP_TYPES,
    StaleStructureError,
    evaluate,
    page_layers,
    query_path,
    read_entries,
    resolve_path,
    resolve_shapes,
)
from core.targets import resolve_document, resolve_page
from core.units import unit_name

_CDR_NO_FILL = 0
_CDR_UNIFORM_FILL = 1
_CDR_NO_OUTLINE = 0
InspectionField = Literal["name", "type", "z_order", "bounds", "effects", "fill", "outline",
                          "overprint_fill", "overprint_outline"]


def _static_id(shape) -> str:
    return str(shape.StaticID)


def _exact_shapes(doc, page, shape_ids: list[str]):
    """COM shapes for exact StaticIDs on the page's layers, through the page structure cache (core.structure)."""
    return resolve_shapes(doc, page, shape_ids)


def _decode_color(color_type, is_spot, name, tint, components):
    """Native color identity shared by direct COM reads and batched query reads."""
    if color_type == CDR_COLOR_REGISTRATION:
        return {"kind": "registration", "type": color_type}
    if color_type == CDR_COLOR_PANTONE or is_spot:
        return {"kind": "spot", "type": color_type, "name": name(), "tint": tint()}
    if color_type == CDR_COLOR_CMYK:
        return {"kind": "process", "model": "CMYK", "type": color_type, "components": list(components())}
    if color_type == CDR_COLOR_RGB:
        return {"kind": "process", "model": "RGB", "type": color_type, "components": list(components())}
    return {"kind": "unknown", "type": color_type}


def _color_details(color):
    color_type = color.Type

    def components():
        if color_type == CDR_COLOR_CMYK:
            return color.CMYKCyan, color.CMYKMagenta, color.CMYKYellow, color.CMYKBlack
        return color.RGBRed, color.RGBGreen, color.RGBBlue

    is_spot = color_type not in (CDR_COLOR_REGISTRATION, CDR_COLOR_PANTONE) and color.IsSpot
    return _decode_color(color_type, is_spot, lambda: color.Name, lambda: color.Tint, components)


def _fill_details(shape):
    fill = shape.Fill
    fill_type = fill.Type
    if fill_type == _CDR_NO_FILL:
        return {"type": fill_type}
    if fill_type == _CDR_UNIFORM_FILL:
        return {"type": fill_type, "color": _color_details(fill.UniformColor)}
    return {"type": fill_type, "color": {"kind": "unknown"}}


def _outline_details(shape):
    outline = shape.Outline
    outline_type = outline.Type
    if outline_type == _CDR_NO_OUTLINE:
        return {"type": outline_type}
    return {"type": outline_type, "width": outline.Width, "color": _color_details(outline.Color)}


def _layer_details(layer, name):
    return {
        "name": name, "master": bool(layer.Master),
        "visible": bool(layer.Visible), "printable": bool(layer.Printable),
    }


# Each COM property read crosses into CorelDRAW twice and costs ~1–25 ms on a large
# drawing. CorelDRAW's query language (Shape.Evaluate) runs many reads in-process and
# returns them as one array. Paths are relative to an anchor shape on the same layer.
_STRUCTURE_BATCH = 250
_CHUNK = 250  # shapes read per batch; inspect_shapes checks its time budget between batches
_RESERVE_SECONDS = 20.0  # stop a long inventory this long before the bridge time limit and return next_offset
_query_path = query_path
_query = evaluate


def _color_query(color):
    tint = f"iif({color}.Type = {CDR_COLOR_PANTONE} or {color}.IsSpot, {color}.Tint, 0)"
    cmyk = f"array({color}.CMYKCyan, {color}.CMYKMagenta, {color}.CMYKYellow, {color}.CMYKBlack)"
    rgb = f"array({color}.RGBRed, {color}.RGBGreen, {color}.RGBBlue)"
    components = f"iif({color}.Type = {CDR_COLOR_CMYK}, {cmyk}, iif({color}.Type = {CDR_COLOR_RGB}, {rgb}, 0))"
    return f"array({color}.Type, {color}.IsSpot, {color}.Name, {tint}, {components})"


def _decode_queried_color(values):
    color_type, is_spot, name, tint, components = values
    return _decode_color(color_type, is_spot, lambda: name, lambda: tint, lambda: components)


def _is_container(path):
    return " or ".join(f"{path}.Type = {kind}" for kind in sorted(CONTAINER_TYPES))


def _record_query(path, fields):
    """Return the query expressions for one shape and the field names they answer, in order."""
    def leaf(expression):
        # Groups and effect groups have no fill, outline, or overprint flags of their own.
        return f"iif({_is_container(path)}, 0, {expression})"

    queries = {
        "name": f"{path}.Name",
        "z_order": f"{path}.ZOrder",
        "bounds": f"array({path}.LeftX, {path}.BottomY, {path}.SizeWidth, {path}.SizeHeight)",
        "effects": f"{path}.Effects.Count",
        "fill": leaf(f"array({path}.Fill.Type, iif({path}.Fill.Type = {_CDR_UNIFORM_FILL}, "
                     f"{_color_query(path + '.Fill.UniformColor')}, 0))"),
        "outline": leaf(f"array({path}.Outline.Type, iif({path}.Outline.Type = {_CDR_NO_OUTLINE}, 0, "
                        f"array({path}.Outline.Width, {_color_query(path + '.Outline.Color')})))"),
        "overprint_fill": leaf(f"{path}.OverprintFill"),
        "overprint_outline": leaf(f"{path}.OverprintOutline"),
    }
    names = [name for name in queries if fields is None or name in fields]
    return [f"{path}.StaticID", f"{path}.Type", *(queries[name] for name in names)], names


def _decode_record(values, names, fields):
    static_id, shape_type, *rest = values
    record = {"shape_id": str(static_id), "unreadable": {}}
    if fields is None or "type" in fields:
        record["type"] = shape_type
    for name, value in zip(names, rest, strict=True):
        if shape_type in CONTAINER_TYPES and name in ("fill", "outline", "overprint_fill", "overprint_outline"):
            continue
        if name == "bounds":
            left, bottom, width, height = value
            value = dict(left=left, bottom=bottom, width=width, height=height)
        elif name == "fill":
            fill_type, color = value
            value = {"type": fill_type}
            if fill_type == _CDR_UNIFORM_FILL:
                value["color"] = _decode_queried_color(color)
            elif fill_type != _CDR_NO_FILL:
                value["color"] = {"kind": "unknown"}
        elif name == "outline":
            outline_type, details = value
            value = {"type": outline_type}
            if outline_type != _CDR_NO_OUTLINE:
                width, color = details
                value.update(width=width, color=_decode_queried_color(color))
        elif name in ("overprint_fill", "overprint_outline"):
            value = bool(value)
        record[name] = value
    return record


def _effect_details(shape, unit):
    from tools.effects import effect_details

    return effect_details(shape, unit)


def _query_records(view, info, entries, fields, unit=None):
    """Inspection records for structure entries of one layer, in one batched query (IDs verified)."""
    queries, names = _record_query("$item", fields)
    rows = read_entries(view, entries, queries[1:])
    records = []
    for entry, row in zip(entries, rows, strict=True):
        record = _decode_record((entry.shape_id, *row), names, fields)
        if record.get("effects"):
            shape = resolve_path(view.layer, entry.path)
            record["effects"] = [shape.Effects.Item(index).Type for index in range(1, record["effects"] + 1)]
            if unit is not None:
                record["effect_details"] = _effect_details(shape, unit)
        elif "effects" in record:
            record["effects"] = []
        records.append({"shape_id": record.pop("shape_id"), "parent_id": entry.parent_id, "layer": info,
                        **_placement(entry), **record})
    return records


def _placement(entry):
    """PowerClip and effect membership of a structure entry (keys present only when they apply)."""
    placement = {}
    if entry.clip is not None:
        placement.update(in_powerclip=True, powerclip_container_id=entry.clip)
    if entry.effect is not None:
        placement["effect_group_id"] = entry.effect
    return placement


_EFFECT_GROUP_IDS = {
    5: "$item.DropShadow.ShadowGroup.StaticID",  # cdrDropShadow
    6: "$item.Contour.ContourGroup.StaticID",  # cdrContour
}
_MAX_EFFECTS = 8


def effect_controls(view) -> dict[str, str]:
    """Map each contour and drop-shadow group on one layer to the StaticID of the shape that controls it.

    Reads the effect count of every ordinary shape on the layer, so call it only when the layer has effect groups.
    Other effect groups (blend, extrude, bevel) are not linked.
    """
    entries = [entry for entry in view.structure.entries if entry.type not in CONTAINER_TYPES]
    counts = read_entries(view, entries, ["$item.Effects.Count"]) if entries else []
    controls = {}
    for entry, (count,) in zip(entries, counts, strict=True):
        if not count:
            continue
        group = " ".join(f"iif($item.Type = {kind}, {expression}, " for kind, expression in _EFFECT_GROUP_IDS.items())
        group += "0" + ")" * len(_EFFECT_GROUP_IDS)
        items = ", ".join(f"$item.Effects.Item({index})" for index in range(1, min(count, _MAX_EFFECTS) + 1))
        for value in read_entries(view, [entry], [f"array({items}).convert({group})"])[0][0]:
            if value:
                controls[str(value)] = entry.shape_id
    return controls


def _read_records(view, info, entries, fields, unit=None):
    """Read inspection records for structure entries of one layer in batched queries."""
    try:
        return _query_records(view, info, entries, fields, unit)
    except StaleStructureError:
        raise
    except Exception:
        pass
    # One property failed somewhere in the batch. Retry each shape, then use direct
    # reads, which report each unreadable property instead of failing the inventory.
    records = []
    for entry in entries:
        try:
            records.extend(_query_records(view, info, [entry], fields, unit))
        except StaleStructureError:
            raise
        except Exception:
            try:
                shape = resolve_path(view.layer, entry.path)
                same = str(shape.StaticID) == entry.shape_id
            except Exception:
                same = False
            if not same:
                raise StaleStructureError(f"Shape {entry.shape_id} is no longer at its cached position") from None
            records.append(_inspect_shape(shape, entry.parent_id, info, fields, unit))
    return records


def _inspect_shape(shape, parent_id, layer, fields=None, unit=None):
    info = {"shape_id": _static_id(shape), "parent_id": parent_id, "layer": layer, "unreadable": {}}
    shape_type = shape.Type
    readers = {
        "name": lambda: shape.Name,
        "type": lambda: shape_type,
        "z_order": lambda: shape.ZOrder,
        "bounds": lambda: dict(left=shape.LeftX, bottom=shape.BottomY, width=shape.SizeWidth, height=shape.SizeHeight),
        "effects": lambda: [effect.Type for effect in shape.Effects],
    }
    if shape_type not in CONTAINER_TYPES:
        readers.update(
            fill=lambda: _fill_details(shape),
            outline=lambda: _outline_details(shape),
            overprint_fill=lambda: bool(shape.OverprintFill),
            overprint_outline=lambda: bool(shape.OverprintOutline),
        )
    for name, read in readers.items():
        if fields is not None and name not in fields:
            continue
        try:
            info[name] = read()
        except Exception as exc:
            info["unreadable"][name] = str(exc)
    if info.get("effects") and unit is not None:
        try:
            info["effect_details"] = _effect_details(shape, unit)
        except Exception as exc:
            info["unreadable"]["effect_details"] = str(exc)
    return info


def inspect_shapes(
    document_path: str,
    page_index: int,
    layer_name: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    fields: list[InspectionField] | None = None,
) -> ToolResult:
    """Inspect one-based page of an exact open document, and its shared layers, without changing anything.

    document_path is any open document (it need not be active). Returns exact IDs, nested group
    ancestry, order, bounds in the document unit (document_unit, e.g. "in"), effects (and effect_details:
    type, group_id; contours also direction, offset, steps, corner, fill_color), uniform fill and outline
    colors, overprint flags, and layer visibility/printability/locked.
    PowerClip contents follow their container (in_powerclip, powerclip_container_id, parent_id =
    container); objects generated by an effect follow its group (effect_group_id), and contour and
    drop-shadow groups name their control shape (effect_control_id). For per-ink totals, plates and
    text-run colors use get_ink_inventory.
    Unknown color types and unreadable fields require further inspection. This is an
    artwork inventory. CorelDRAW's print preview determines actual separation output.

    Use an exact layer_name to limit the scan, or pass limit (1–250) and follow
    next_offset until null. Offset is zero-based within the selected scope. Keep the
    document unchanged across calls. Omit limit for the full inventory; on a very large
    page it may stop early to stay within the time limit and return next_offset, so
    always continue until next_offset is null.
    Pass fields to request only the named details (e.g. type, fill, outline); IDs,
    ancestry and layer information are always returned.
    """
    if offset < 0 or (limit is not None and not 1 <= limit <= 250):
        return ToolResult.fail("Offset must be nonnegative and limit must be between 1 and 250")
    conn = get_connection()
    wanted_fields = None if fields is None else set(fields)

    def inventory(doc, page, fresh):
        # Structures are cached between calls (core.structure) and validated on every call.
        views = page_layers(doc, page, layer_name, fresh=fresh)
        if layer_name is not None and len(views) != 1:
            raise ValueError(f"Expected one layer named {layer_name!r}; found {len(views)}")
        scoped = [(number, entry) for number, view in enumerate(views) for entry in view.structure.entries]
        end = len(scoped) if limit is None else min(len(scoped), offset + limit)
        shapes, position, infos, controls = [], offset, {}, {}
        while position < end:
            if shapes and conn.time_left() < _RESERVE_SECONDS:
                break  # return what is read; the caller continues from next_offset
            stop = min(end, position + _CHUNK)
            for number, group in groupby(scoped[position:stop], key=lambda item: item[0]):
                view = views[number]
                if number not in infos:
                    infos[number] = view.details()
                records = _read_records(view, infos[number], [entry for _, entry in group], wanted_fields, doc.Unit)
                if any(record.get("type") in EFFECT_GROUP_TYPES for record in records):
                    if number not in controls:
                        controls[number] = effect_controls(view)
                    for record in records:
                        if record["shape_id"] in controls[number]:
                            record["effect_control_id"] = controls[number][record["shape_id"]]
                shapes.extend(records)
            position = stop
        return shapes, position if position < len(scoped) else None

    def inspect():
        doc, actual_path = resolve_document(conn.app, document_path)
        page = resolve_page(doc, page_index)
        for fresh in (False, True):
            try:
                shapes, next_offset = inventory(doc, page, fresh)
                break
            except StaleStructureError:
                continue
        else:
            raise RuntimeError("The page changed while it was being read; retry when it is not being edited")
        return {
            "document_path": actual_path, "page_index": page_index, "page_name": page.Name,
            "document_unit": unit_name(doc.Unit), "shapes": shapes, "next_offset": next_offset,
        }

    result = conn.read_call(inspect)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def get_ui_selection(document_path: str, page_index: int) -> ToolResult:
    """Read exact shape IDs in the UI selection of an exact open document; page_index must be its active page.

    To change the selection use find_shapes(..., select=True), for example with filters {"ids": [...]}.
    """
    conn = get_connection()

    def read():
        doc, actual_path = resolve_document(conn.app, document_path)
        if doc.ActivePage.Index != page_index:
            raise ValueError(f"Page {page_index} must be the document's active page (edit_pages action=activate)")
        selection = doc.SelectionRange
        return {
            "document_path": actual_path, "page_index": page_index,
            "shape_ids": [_static_id(selection.Item(i)) for i in range(1, selection.Count + 1)],
        }

    result = conn.read_call(read)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    return ToolResult.ok(**result["result"])


def _on_shape(document_path, page_index, shape_id, action, *, write=False) -> ToolResult:
    def one(app, doc, shapes):
        return {"shape_id": shape_id, **action(app, doc, shapes[0])}

    return _on_shapes(document_path, page_index, [shape_id], one, write=write)


def _require_editable(shape, page_index):
    layer = shape.Layer
    if layer.Master or layer.IsSpecialLayer or layer.Page.Index != page_index or not layer.Editable:
        raise ValueError("The shape must be on an editable, ordinary layer local to the requested page")


def _on_shapes(document_path, page_index, shape_ids, action, *, write=False) -> ToolResult:
    conn = get_connection()

    def run():
        # Edits activate the document: Undo and command groups follow the active document.
        doc, actual_path = resolve_document(conn.app, document_path, activate=write)
        page = resolve_page(doc, page_index)
        shapes = _exact_shapes(doc, page, shape_ids)
        if write:
            for shape in shapes:
                _require_editable(shape, page_index)
        return {
            "document_path": actual_path, "page_index": page_index,
            **action(conn.app, doc, shapes),
        }

    # A failed readback after a COM mutation leaves its outcome uncertain.
    result = conn.safe_call(run) if write else conn.read_call(run)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if error := data.pop("verification_error", None):
        return ToolResult(success=False, error=error, data=data)
    return ToolResult.ok(**data)
