"""Inspect drawing objects and bridge exact IDs to CorelDRAW UI selection."""

from itertools import groupby
from typing import Literal

from core.connection import get_connection
from core.drawing import (
    CDR_COLOR_CMYK,
    CDR_COLOR_PANTONE,
    CDR_COLOR_REGISTRATION,
    CDR_COLOR_RGB,
    CDR_GROUP_SHAPE,
)
from core.models import ToolResult
from core.targets import resolve_document, resolve_page
from core.units import unit_name
from tools.shapes import _create_shape_range, _static_id

_CDR_NO_FILL = 0
_CDR_UNIFORM_FILL = 1
_CDR_NO_OUTLINE = 0
InspectionField = Literal["name", "type", "z_order", "bounds", "effects", "fill", "outline",
                          "overprint_fill", "overprint_outline"]


def _exact_shapes(page, shape_ids: list[str]):
    requested = set(shape_ids)
    if not requested or len(requested) != len(shape_ids):
        raise ValueError("Provide one or more unique shape IDs")
    found = {}
    for layer in page.AllLayers:
        # Read IDs in batched queries instead of one COM round trip per object, which
        # takes minutes on large layers. (Shapes.FindShape also filters on Name, so an
        # empty name matches only unnamed shapes and it cannot search by ID alone.)
        anchor, entries = _layer_structure(layer)
        paths = [path for path, _ in entries]
        for start in range(0, len(paths), _STRUCTURE_BATCH):
            chunk = paths[start:start + _STRUCTURE_BATCH]
            ids = _query(anchor, [f"{_query_path(path)}.StaticID" for path in chunk])
            for path, value in zip(chunk, ids, strict=True):
                shape_id = str(value)
                if shape_id in requested:
                    if shape_id in found:
                        raise ValueError(f"Ambiguous shape ID {shape_id}")
                    found[shape_id] = _resolve_path(layer, path)
    if missing := requested - found.keys():
        raise ValueError(f"Shape IDs not found on requested page: {', '.join(sorted(missing))}")
    return [found[shape_id] for shape_id in shape_ids]


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
_QUERY_BATCH = 50
_STRUCTURE_BATCH = 250


def _query_path(indices):
    return "@com.Layer.Shapes." + ".Shapes.".join(f"Item({index})" for index in indices)


def _query(anchor, expressions):
    values = anchor.Evaluate("array(" + ", ".join(expressions) + ")")
    if len(values) != len(expressions):
        raise RuntimeError("CorelDRAW returned an incomplete query result")
    return values


def _color_query(color):
    tint = f"iif({color}.Type = {CDR_COLOR_PANTONE} or {color}.IsSpot, {color}.Tint, 0)"
    cmyk = f"array({color}.CMYKCyan, {color}.CMYKMagenta, {color}.CMYKYellow, {color}.CMYKBlack)"
    rgb = f"array({color}.RGBRed, {color}.RGBGreen, {color}.RGBBlue)"
    components = f"iif({color}.Type = {CDR_COLOR_CMYK}, {cmyk}, iif({color}.Type = {CDR_COLOR_RGB}, {rgb}, 0))"
    return f"array({color}.Type, {color}.IsSpot, {color}.Name, {tint}, {components})"


def _decode_queried_color(values):
    color_type, is_spot, name, tint, components = values
    return _decode_color(color_type, is_spot, lambda: name, lambda: tint, lambda: components)


def _record_query(path, fields):
    """Return the query expressions for one shape and the field names they answer, in order."""
    def leaf(expression):
        # Groups have no fill, outline, or overprint flags of their own.
        return f"iif({path}.Type = {CDR_GROUP_SHAPE}, 0, {expression})"

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
        if shape_type == CDR_GROUP_SHAPE and name in ("fill", "outline", "overprint_fill", "overprint_outline"):
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


def _layer_structure(layer):
    """List each shape's index path in walk_shapes order with its parent's path."""
    if not layer.Shapes.Count:
        return None, []
    anchor = layer.Shapes.Item(1)

    def children(prefix):
        if prefix:
            count = _query(anchor, [f"{_query_path(prefix)}.Shapes.Count"])[0]
        else:
            count = layer.Shapes.Count
        paths = [(*prefix, index) for index in range(1, count + 1)]
        kinds = []
        for start in range(0, len(paths), _STRUCTURE_BATCH):
            chunk = paths[start:start + _STRUCTURE_BATCH]
            kinds.extend(_query(anchor, [f"{_query_path(path)}.Type" for path in chunk]))
        for path, shape_type in zip(paths, kinds, strict=True):
            yield path, prefix or None
            if shape_type == CDR_GROUP_SHAPE:
                yield from children(path)

    return anchor, list(children(()))


def _resolve_path(layer, path):
    shape = layer.Shapes.Item(path[0])
    for index in path[1:]:
        shape = shape.Shapes.Item(index)
    return shape


def _query_records(layer, info, anchor, chunk, fields):
    expressions, sizes, names = [], [], None
    parents = sorted({parent for _, parent in chunk if parent}, key=len)
    for path, _ in chunk:
        queries, names = _record_query(_query_path(path), fields)
        expressions.extend(queries)
        sizes.append(len(queries))
    expressions.extend(f"{_query_path(parent)}.StaticID" for parent in parents)
    values = _query(anchor, expressions)
    parent_ids = dict(zip(parents, (str(value) for value in values[sum(sizes):]), strict=True))
    records, position = [], 0
    for (path, parent), size in zip(chunk, sizes, strict=True):
        record = _decode_record(values[position:position + size], names, fields)
        position += size
        if record.get("effects"):
            shape = _resolve_path(layer, path)
            record["effects"] = [shape.Effects.Item(index).Type for index in range(1, record["effects"] + 1)]
        elif "effects" in record:
            record["effects"] = []
        records.append({"shape_id": record.pop("shape_id"), "parent_id": parent_ids.get(parent), "layer": info, **record})
    return records


def _read_records(layer, info, anchor, entries, fields):
    """Read inspection records for (path, parent_path) entries in batched queries."""
    records = []
    for start in range(0, len(entries), _QUERY_BATCH):
        chunk = entries[start:start + _QUERY_BATCH]
        try:
            records.extend(_query_records(layer, info, anchor, chunk, fields))
            continue
        except Exception:
            pass
        # One property failed somewhere in the batch. Retry each shape, then use direct
        # reads, which report each unreadable property instead of failing the inventory.
        for path, parent in chunk:
            try:
                records.extend(_query_records(layer, info, anchor, [(path, parent)], fields))
            except Exception:
                parent_id = str(_resolve_path(layer, parent).StaticID) if parent else None
                records.append(_inspect_shape(_resolve_path(layer, path), parent_id, info, fields))
    return records


def _inspect_shape(shape, parent_id, layer, fields=None):
    info = {"shape_id": _static_id(shape), "parent_id": parent_id, "layer": layer, "unreadable": {}}
    shape_type = shape.Type
    readers = {
        "name": lambda: shape.Name,
        "type": lambda: shape_type,
        "z_order": lambda: shape.ZOrder,
        "bounds": lambda: dict(left=shape.LeftX, bottom=shape.BottomY, width=shape.SizeWidth, height=shape.SizeHeight),
        "effects": lambda: [effect.Type for effect in shape.Effects],
    }
    if shape_type != CDR_GROUP_SHAPE:
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
    ancestry, order, bounds in the document unit (document_unit, e.g. "in"), effects,
    uniform fill and outline colors, overprint flags, and layer visibility/printability.
    Unknown color types and unreadable fields require further inspection. This is an
    artwork inventory. CorelDRAW's print preview determines actual separation output.

    Use an exact layer_name to limit the scan, or pass limit (1–250) and follow
    next_offset until null. Offset is zero-based within the selected scope. Keep the
    document unchanged across calls. Omit limit for the original full inventory.
    Pass fields to request only the named details (e.g. type, fill, outline); IDs,
    ancestry and layer information are always returned.
    """
    if offset < 0 or (limit is not None and not 1 <= limit <= 250):
        return ToolResult.fail("Offset must be nonnegative and limit must be between 1 and 250")
    conn = get_connection()
    wanted_fields = None if fields is None else set(fields)

    def inspect():
        doc, actual_path = resolve_document(conn.app, document_path)
        page = resolve_page(doc, page_index)
        layers = [(layer, layer.Name) for layer in page.AllLayers]
        if layer_name is not None:
            layers = [(layer, name) for layer, name in layers if name == layer_name]
            if len(layers) != 1:
                raise ValueError(f"Expected one layer named {layer_name!r}; found {len(layers)}")

        scoped, anchors = [], []
        for number, (layer, _) in enumerate(layers):
            anchor, entries = _layer_structure(layer)
            anchors.append(anchor)
            scoped.extend((number, path, parent) for path, parent in entries)
        end = len(scoped) if limit is None else offset + limit
        next_offset = end if end < len(scoped) else None
        shapes = []
        for number, group in groupby(scoped[offset:end], key=lambda entry: entry[0]):
            layer, name = layers[number]
            entries = [(path, parent) for _, path, parent in group]
            shapes.extend(_read_records(layer, _layer_details(layer, name), anchors[number], entries, wanted_fields))
        return {
            "document_path": actual_path, "page_index": page_index, "page_name": page.Name,
            "document_unit": unit_name(doc.Unit), "shapes": shapes, "next_offset": next_offset,
        }

    result = conn.safe_call(inspect)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def _selection(document_path: str, page_index: int, shape_ids: list[str] | None = None) -> ToolResult:
    conn = get_connection()

    def select():
        # Changing the UI selection needs the document to be the active one; reading it does not.
        doc, actual_path = resolve_document(conn.app, document_path, activate=shape_ids is not None)
        if doc.ActivePage.Index != page_index:
            raise ValueError(f"Page {page_index} must be the document's active page (edit_pages action=activate)")
        if shape_ids is not None:
            _create_shape_range(_exact_shapes(doc.ActivePage, shape_ids)).CreateSelection()
        selection = doc.SelectionRange
        return {
            "document_path": actual_path, "page_index": page_index,
            "shape_ids": [_static_id(selection.Item(i)) for i in range(1, selection.Count + 1)],
        }

    result = conn.safe_call(select)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if shape_ids is not None and set(data["shape_ids"]) != set(shape_ids):
        return ToolResult(success=False, error="CorelDRAW selected a different set of shapes", data=data)
    return ToolResult.ok(**data)


def get_ui_selection(document_path: str, page_index: int) -> ToolResult:
    """Read exact shape IDs in the UI selection of an exact open document; page_index must be its active page."""
    return _selection(document_path, page_index)


def select_shapes_by_id(document_path: str, page_index: int, shape_ids: list[str]) -> ToolResult:
    """Replace the UI selection with exact StaticIDs; activates the document. page_index must be its active page."""
    return _selection(document_path, page_index, shape_ids)


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
        shapes = _exact_shapes(page, shape_ids)
        if write:
            for shape in shapes:
                _require_editable(shape, page_index)
        return {
            "document_path": actual_path, "page_index": page_index,
            **action(conn.app, doc, shapes),
        }

    # A failed readback after a COM mutation leaves its outcome uncertain.
    result = conn.safe_call(run)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if error := data.pop("verification_error", None):
        return ToolResult(success=False, error=error, data=data)
    return ToolResult.ok(**data)
