"""Inspect drawing objects and bridge exact IDs to CorelDRAW UI selection."""

from itertools import islice
from typing import Literal

from core.connection import get_connection
from core.drawing import (
    CDR_COLOR_CMYK,
    CDR_COLOR_PANTONE,
    CDR_COLOR_REGISTRATION,
    CDR_COLOR_RGB,
    CDR_GROUP_SHAPE,
    walk_shapes,
)
from core.models import ToolResult
from tools.document import _scoped_document, _scoped_page
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
        for shape, _ in walk_shapes(layer.Shapes):
            shape_id = _static_id(shape)
            if shape_id in requested:
                if shape_id in found:
                    raise ValueError(f"Ambiguous shape ID {shape_id}")
                found[shape_id] = shape
    if missing := requested - found.keys():
        raise ValueError(f"Shape IDs not found on requested page: {', '.join(sorted(missing))}")
    return [found[shape_id] for shape_id in shape_ids]


def _color_details(color):
    color_type = color.Type
    if color_type == CDR_COLOR_REGISTRATION:
        return {"kind": "registration", "type": color_type}
    if color_type == CDR_COLOR_PANTONE or color.IsSpot:
        return {"kind": "spot", "type": color_type, "name": color.Name}
    if color_type == CDR_COLOR_CMYK:
        return {
            "kind": "process", "model": "CMYK", "type": color_type,
            "components": [color.CMYKCyan, color.CMYKMagenta, color.CMYKYellow, color.CMYKBlack],
        }
    if color_type == CDR_COLOR_RGB:
        return {
            "kind": "process", "model": "RGB", "type": color_type,
            "components": [color.RGBRed, color.RGBGreen, color.RGBBlue],
        }
    return {"kind": "unknown", "type": color_type}


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


def _inspection_entries(layers, offset, end):
    if offset and len(layers) == 1:
        layer, name = layers[0]
        collection = layer.Shapes
        if collection.FindShapes("", CDR_GROUP_SHAPE, False).Count == 0:
            # Skip directly to the requested position on a flat layer. A COM
            # walk of all preceding objects costs more on every successive chunk.
            size = collection.Count
            stop = size if end is None else min(end, size)
            info = _layer_details(layer, name)
            for index in range(offset + 1, stop + 1):
                yield collection.Item(index), None, info
            return

    def entries():
        for layer, name in layers:
            info = _layer_details(layer, name)
            for shape, parent_id in walk_shapes(layer.Shapes):
                yield shape, parent_id, info

    yield from islice(entries(), offset, end)


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
    """Inspect a saved active document's one-based page and shared layers without changing the UI.

    Returns exact IDs, nested group ancestry, order, bounds in document units, effects,
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
        doc, actual_path = _scoped_document(conn.app, document_path)
        page = _scoped_page(doc, page_index)
        layers = [(layer, layer.Name) for layer in page.AllLayers]
        if layer_name is not None:
            layers = [(layer, name) for layer, name in layers if name == layer_name]
            if len(layers) != 1:
                raise ValueError(f"Expected one layer named {layer_name!r}; found {len(layers)}")

        end = None if limit is None else offset + limit + 1
        shapes = []
        next_offset = None
        for entry in _inspection_entries(layers, offset, end):
            if limit is not None and len(shapes) == limit:
                next_offset = offset + limit
                break
            shapes.append(_inspect_shape(*entry, wanted_fields))
        return {
            "document_path": actual_path, "page_index": page_index, "page_name": page.Name,
            "document_unit": doc.Unit, "shapes": shapes, "next_offset": next_offset,
        }

    result = conn.safe_call(inspect)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def _selection(document_path: str, page_index: int, shape_ids: list[str] | None = None) -> ToolResult:
    conn = get_connection()

    def select():
        doc, actual_path = _scoped_document(conn.app, document_path)
        if doc.ActivePage.Index != page_index:
            raise ValueError(f"Page {page_index} must be active to use its UI selection")
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
    """Read exact shape IDs in the UI selection of the specified active document and one-based page."""
    return _selection(document_path, page_index)


def select_shapes_by_id(document_path: str, page_index: int, shape_ids: list[str]) -> ToolResult:
    """Replace the UI selection on the specified active document and page with one or more exact StaticIDs."""
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
        doc, actual_path = _scoped_document(conn.app, document_path)
        page = _scoped_page(doc, page_index)
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
