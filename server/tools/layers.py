"""图层管理工具 — 创建/锁/显隐图层，形状图层分配"""

from core.connection import get_connection
from core.models import ToolResult
from tools.document import _scoped_document, _scoped_page


def _find_shape(shape_id: str):
    """按名称或 StaticID 查找形状"""
    conn = get_connection()
    doc = conn.app.ActiveDocument
    if not doc:
        return None
    target = str(shape_id)
    try:
        shapes = doc.ActivePage.Shapes
        try:
            return shapes(shape_id)
        except Exception:
            for s in shapes:
                try:
                    if str(s.StaticID) == target or s.Name == target:
                        return s
                except Exception:
                    continue
        return None
    except Exception:
        return None


def create_layer(name: str, color: str = "") -> ToolResult:
    """创建新图层。color 用于图层标识（激光机识别），如 'red' / 'blue'。"""
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW 未连接")

    def _create():
        page = conn.app.ActiveDocument.ActivePage
        layer = page.CreateLayer(name)
        # Ensure layer is visible and printable (X6 defaults can vary)
        for attr in ("Visible", "Printable", "Editable"):
            try:
                setattr(layer, attr, True)
            except Exception:
                pass
        if color:
            try:
                layer.Color = color
            except Exception:
                pass
        return {"name": name, "color": color, "visible": True, "editable": True}

    result = conn.safe_call(_create)
    if result["success"]:
        return ToolResult.ok(f"图层已创建: {name}", **result["result"])
    return ToolResult.fail(result.get("error", "创建图层失败"))


def get_layers() -> ToolResult:
    """获取当前页面的所有图层信息。"""
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW 未连接")

    def _list():
        page = conn.app.ActiveDocument.ActivePage
        layers_list = []
        try:
            for layer in page.Layers:
                layer_info = {"name": layer.Name}
                try:
                    layer_info["visible"] = layer.Visible
                except Exception:
                    layer_info["visible"] = True
                try:
                    layer_info["editable"] = layer.Editable
                except Exception:
                    layer_info["editable"] = True
                try:
                    layer_info["locked"] = not layer.Editable
                except Exception:
                    layer_info["locked"] = False
                try:
                    c = layer.Color
                    layer_info["color"] = int(c) if isinstance(c, int) else str(c)
                except Exception:
                    layer_info["color"] = ""
                try:
                    layer_info["shapes_count"] = layer.Shapes.Count
                except Exception:
                    layer_info["shapes_count"] = 0
                layers_list.append(layer_info)
        except Exception:
            pass
        return {"layers": layers_list, "count": len(layers_list)}

    result = conn.safe_call(_list)
    if result["success"]:
        return ToolResult.ok(f"共 {result['result']['count']} 个图层", **result["result"])
    return ToolResult.fail(result.get("error", "获取图层列表失败"))


def assign_to_layer(shape_id: str, layer_name: str) -> ToolResult:
    """将形状移动到指定图层。"""
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW 未连接")

    def _assign():
        shape = _find_shape(shape_id)
        if shape is None:
            raise ValueError(f"未找到形状: {shape_id}")
        page = conn.app.ActiveDocument.ActivePage
        target_layer = None
        try:
            target_layer = page.Layers(layer_name)
        except Exception:
            raise ValueError(f"未找到图层: {layer_name}")
        target_layer.Activate()
        # 将形状移动到目标图层
        try:
            shape.MoveToLayer(target_layer)
        except Exception:
            try:
                shape.Layer = target_layer
            except Exception:
                raise RuntimeError(f"无法移动形状到图层: {layer_name}")
        return {"shape_id": shape_id, "layer": layer_name}

    result = conn.safe_call(_assign)
    if result["success"]:
        return ToolResult.ok(f"形状已移至图层: {layer_name}", **result["result"])
    return ToolResult.fail(result.get("error", "分配图层失败"))


def set_layer_visible(layer_name: str, visible: bool) -> ToolResult:
    """设置图层的可见性。"""
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW 未连接")

    def _set():
        page = conn.app.ActiveDocument.ActivePage
        try:
            layer = page.Layers(layer_name)
        except Exception:
            raise ValueError(f"未找到图层: {layer_name}")
        layer.Visible = visible
        return {"layer": layer_name, "visible": visible}

    result = conn.safe_call(_set)
    if result["success"]:
        status = "可见" if visible else "隐藏"
        return ToolResult.ok(f"图层 {layer_name} 已设为{status}", **result["result"])
    return ToolResult.fail(result.get("error", "设置图层可见性失败"))


def lock_layer(layer_name: str) -> ToolResult:
    """锁定图层，防止意外编辑。"""
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW 未连接")

    def _lock():
        page = conn.app.ActiveDocument.ActivePage
        try:
            layer = page.Layers(layer_name)
        except Exception:
            raise ValueError(f"未找到图层: {layer_name}")
        layer.Editable = False
        return {"layer": layer_name, "locked": True}

    result = conn.safe_call(_lock)
    if result["success"]:
        return ToolResult.ok(f"图层已锁定: {layer_name}", **result["result"])
    return ToolResult.fail(result.get("error", "锁定图层失败"))


_LAYER_PROPERTIES = {"visible": "Visible", "editable": "Editable", "printable": "Printable"}


def _layer_state(layer):
    return {
        "name": layer.Name,
        "master": bool(layer.Master),
        "special": bool(layer.IsSpecialLayer),
        **{name: bool(getattr(layer, attr)) for name, attr in _LAYER_PROPERTIES.items()},
    }


def get_page_layers(document_path: str, page_index: int) -> ToolResult:
    """Read layer visibility, editability, and printability on a specific page of the active saved document.

    page_index is one-based. Shared master layers are identified in the result.
    """
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW is not connected")

    def inspect():
        doc, actual_path = _scoped_document(conn.app, document_path)
        page = _scoped_page(doc, page_index)
        return {
            "document_path": actual_path, "page_index": page_index, "page_name": page.Name,
            "layers": [_layer_state(layer) for layer in page.AllLayers],
        }

    result = conn.safe_call(inspect)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def set_page_layer_state(
    document_path: str, page_index: int, layer_name: str, property_name: str, value: bool,
) -> ToolResult:
    """Set one flag (visible, editable, or printable) on an exact page-local layer and read back all flags.

    document_path must match the active saved file, page_index is one-based, and layer_name
    must match exactly. Shared and special layers are excluded. editable=True unlocks the layer.
    """
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW is not connected")
    if property_name not in _LAYER_PROPERTIES:
        return ToolResult.fail("property_name must be visible, editable, or printable")

    def set_state():
        doc, actual_path = _scoped_document(conn.app, document_path)
        page = _scoped_page(doc, page_index)
        matches = [layer for layer in page.Layers if layer.Name == layer_name]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one layer named {layer_name!r} on page {page_index}")
        layer = matches[0]
        if layer.Master or layer.IsSpecialLayer or layer.Page.Index != page_index:
            raise ValueError("Only ordinary page-local layers can be changed by this tool")
        setattr(layer, _LAYER_PROPERTIES[property_name], value)
        return {"document_path": actual_path, "page_index": page_index, "layer": _layer_state(layer)}

    result = conn.safe_call(set_state)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if data["layer"][property_name] != value:
        return ToolResult(success=False, error=f"CorelDRAW did not apply {property_name}={value}", data=data)
    return ToolResult.ok(**data)
