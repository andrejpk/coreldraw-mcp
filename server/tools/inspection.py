"""Inspect drawing objects and bridge exact IDs to CorelDRAW UI selection."""

from core.connection import get_connection
from core.models import ToolResult
from tools.colors import CDR_COLOR_CMYK, CDR_COLOR_PANTONE, CDR_COLOR_REGISTRATION, CDR_COLOR_RGB
from tools.document import _scoped_document, _scoped_page
from tools.shapes import _create_shape_range, _static_id

_CDR_GROUP_SHAPE = 7
_CDR_NO_FILL = 0
_CDR_UNIFORM_FILL = 1
_CDR_NO_OUTLINE = 0


def _walk_shapes(shapes, parent_id=None):
    for shape in shapes:
        yield shape, parent_id
        if shape.Type == _CDR_GROUP_SHAPE:
            yield from _walk_shapes(shape.Shapes, _static_id(shape))


def _exact_shapes(page, shape_ids: list[str]):
    requested = set(shape_ids)
    if not requested or len(requested) != len(shape_ids):
        raise ValueError("Provide one or more unique shape IDs")
    found = {}
    for layer in page.AllLayers:
        for shape, _ in _walk_shapes(layer.Shapes):
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


def _inspect_shape(shape, parent_id, layer):
    info = {"shape_id": _static_id(shape), "parent_id": parent_id, "layer": layer, "unreadable": {}}
    readers = {
        "name": lambda: shape.Name,
        "type": lambda: shape.Type,
        "z_order": lambda: shape.ZOrder,
        "bounds": lambda: dict(left=shape.LeftX, bottom=shape.BottomY, width=shape.SizeWidth, height=shape.SizeHeight),
        "effects": lambda: [effect.Type for effect in shape.Effects],
    }
    if shape.Type != _CDR_GROUP_SHAPE:
        readers.update(
            fill=lambda: _fill_details(shape),
            outline=lambda: _outline_details(shape),
            overprint_fill=lambda: bool(shape.OverprintFill),
            overprint_outline=lambda: bool(shape.OverprintOutline),
        )
    for name, read in readers.items():
        try:
            info[name] = read()
        except Exception as exc:
            info["unreadable"][name] = str(exc)
    return info


def inspect_shapes(document_path: str, page_index: int) -> ToolResult:
    """Inspect a saved active document's one-based page and shared layers without changing the UI.

    Returns exact IDs, nested group ancestry, order, bounds in document units, effects,
    uniform fill and outline colors, overprint flags, and layer visibility/printability.
    Unknown color types and unreadable fields require further inspection. This is an
    artwork inventory. CorelDRAW's print preview determines actual separation output.
    """
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW is not connected")

    def inspect():
        doc, actual_path = _scoped_document(conn.app, document_path)
        page = _scoped_page(doc, page_index)
        shapes = []
        for layer in page.AllLayers:
            layer_info = {
                "name": layer.Name, "master": bool(layer.Master),
                "visible": bool(layer.Visible), "printable": bool(layer.Printable),
            }
            shapes.extend(_inspect_shape(shape, parent_id, layer_info) for shape, parent_id in _walk_shapes(layer.Shapes))
        return {
            "document_path": actual_path, "page_index": page_index, "page_name": page.Name,
            "document_unit": doc.Unit, "shapes": shapes,
        }

    result = conn.safe_call(inspect)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def _selection(document_path: str, page_index: int, shape_ids: list[str] | None = None) -> ToolResult:
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW is not connected")

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
