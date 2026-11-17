"""文档管理工具 — 打开模板、创建/保存/关闭文档、页面管理"""

import ntpath

from core.connection import get_connection
from core.drawing import CDR_TEXT_SHAPE, walk_shapes
from core.models import ToolResult

_CDR_MILLIMETER = 3
_UNIT_MAP = {"mm": _CDR_MILLIMETER, "cm": 4, "inch": 1, "pt": 14, "px": 5}


def _get_page_size(doc):
    """读取当前页面尺寸（mm）"""
    try:
        doc.Unit = _CDR_MILLIMETER
        page = doc.ActivePage
        return page.SizeWidth, page.SizeHeight
    except Exception:
        return 0.0, 0.0


def _get_page(doc, page_index: int):
    """Return a CorelDRAW page by 1-based index."""
    return doc.Pages(page_index)


def _scoped_document(app, document_path: str):
    """Match the active document without opening or changing it."""
    if not ntpath.isabs(document_path):
        raise ValueError("document_path must be an absolute Windows path")
    doc = app.ActiveDocument
    if not doc:
        raise RuntimeError("CorelDRAW has no active document")
    actual_path = ntpath.join(doc.FilePath, doc.FileName)
    if ntpath.normcase(ntpath.normpath(actual_path)) != ntpath.normcase(ntpath.normpath(document_path)):
        raise ValueError(f"Document mismatch: active document is {actual_path!r}")
    return doc, actual_path


def _scoped_page(doc, page_index: int):
    if not 1 <= page_index <= doc.Pages.Count:
        raise ValueError(f"page_index must be between 1 and {doc.Pages.Count}")
    return _get_page(doc, page_index)


def open_template(path: str) -> ToolResult:
    """打开 CDR 模板文件。path 为模板文件的绝对路径。"""
    conn = get_connection()

    def _open():
        import os
        if not os.path.isfile(path):
            raise FileNotFoundError(f"模板文件不存在: {path}")
        doc = conn.app.OpenDocument(path)
        doc.Unit = _CDR_MILLIMETER
        width, height = _get_page_size(doc)
        return {"path": path, "pages": doc.Pages.Count, "width": width, "height": height}

    result = conn.safe_call(_open)
    if result["success"]:
        return ToolResult.ok(f"模板已打开: {path}", **result["result"])
    return ToolResult.fail(result.get("error", "打开模板失败"))


def create_document(width: float, height: float, unit: str = "mm") -> ToolResult:
    """新建指定尺寸的 CorelDRAW 文档。width/height 为文档尺寸，unit 支持 mm/cm/inch/pt/px。"""
    conn = get_connection()

    def _create():
        doc = conn.app.CreateDocument()
        cdr_unit = _UNIT_MAP.get(unit.lower(), _CDR_MILLIMETER)
        doc.Unit = cdr_unit
        page = doc.ActivePage
        page.SetSize(width, height)
        return {"width": width, "height": height, "unit": unit, "pages": 1}

    result = conn.safe_call(_create)
    if result["success"]:
        return ToolResult.ok(f"新建文档: {width}×{height} {unit}", **result["result"])
    return ToolResult.fail(result.get("error", "创建文档失败"))


def save_document(path: str = "") -> ToolResult:
    """保存当前文档。path 为空时保存到原路径，否则另存为指定路径。"""
    conn = get_connection()

    def _save():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        if path:
            doc.SaveAs(path)
            saved_path = path
        else:
            doc.Save()
            saved_path = doc.FilePath + doc.FileName if doc.FilePath else "未命名.cdr"
        return {"path": saved_path}

    result = conn.safe_call(_save)
    if result["success"]:
        return ToolResult.ok(f"文档已保存: {result['result']['path']}")
    return ToolResult.fail(result.get("error", "保存文档失败"))


def close_document() -> ToolResult:
    """关闭当前文档。如有未保存修改会提示。"""
    conn = get_connection()

    def _close():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        name = doc.FileName or "未命名"
        modified = doc.Modified if hasattr(doc, "Modified") else False
        doc.Close()
        return {"name": name, "had_unsaved_changes": modified}

    result = conn.safe_call(_close)
    if result["success"]:
        info = result["result"]
        msg = f"已关闭: {info['name']}"
        if info.get("had_unsaved_changes"):
            msg += "（如有未保存修改）"
        return ToolResult.ok(msg, **info)
    return ToolResult.fail(result.get("error", "关闭文档失败"))


def add_page() -> ToolResult:
    """在当前文档末尾添加一个新页面，并激活它。"""
    conn = get_connection()

    def _add():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        doc.AddPages(1)
        page = doc.Pages.Last
        page.Activate()
        width, height = _get_page_size(doc)
        return {"page_index": page.Index, "total_pages": doc.Pages.Count, "width": width, "height": height}

    result = conn.safe_call(_add)
    if result["success"]:
        return ToolResult.ok("已添加新页面", **result["result"])
    return ToolResult.fail(result.get("error", "添加页面失败"))


def set_page_size(width: float, height: float) -> ToolResult:
    """修改当前页面尺寸（mm）。"""
    conn = get_connection()

    def _set():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        doc.Unit = _CDR_MILLIMETER
        page = doc.ActivePage
        page.SetSize(width, height)
        return {"width": width, "height": height, "unit": "mm"}

    result = conn.safe_call(_set)
    if result["success"]:
        return ToolResult.ok(f"页面尺寸已设为: {width}×{height} mm", **result["result"])
    return ToolResult.fail(result.get("error", "设置页面尺寸失败"))


def get_document_info() -> ToolResult:
    """Read the active document's path, pages, dimensions and shape counts without changing it."""
    conn = get_connection()

    def _info():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        page = doc.ActivePage
        unit = doc.Unit
        width = conn.app.ConvertUnits(page.SizeWidth, unit, _CDR_MILLIMETER)
        height = conn.app.ConvertUnits(page.SizeHeight, unit, _CDR_MILLIMETER)
        shapes = page.Shapes
        # Count inside CorelDRAW rather than making one COM round trip per shape.
        text_shapes = shapes.FindShapes("", CDR_TEXT_SHAPE, False).Count

        layers_info = []
        try:
            for layer in page.Layers:
                layers_info.append({
                    "name": layer.Name,
                    "visible": layer.Visible if hasattr(layer, "Visible") else True,
                    "editable": layer.Editable if hasattr(layer, "Editable") else True,
                })
        except Exception:
            pass

        return {
            "name": doc.FileName or "未命名",
            "path": doc.FilePath or "",
            "document_path": ntpath.join(doc.FilePath, doc.FileName) if doc.FilePath else None,
            "pages": doc.Pages.Count,
            "current_page": page.Index,
            "width": round(width, 2),
            "height": round(height, 2),
            "unit": "mm",
            "shapes_count": shapes.Count,
            "text_shapes_count": text_shapes,
            "layers": layers_info,
        }

    result = conn.safe_call(_info)
    if result["success"]:
        return ToolResult.ok("文档信息获取成功", **result["result"])
    return ToolResult.fail(result.get("error", "获取文档信息失败"))


def list_all_text_shapes(
    page_index: int = 0, content_preview_len: int = 40, document_path: str = "",
) -> ToolResult:
    """List editable CorelDRAW text shapes on a page, including nested groups.

    Use a one-based page index or 0 for the active page. When supplied, document_path
    must match the saved active file. Coordinates and sizes are in document units.
    Converted curves have no editable story and are excluded.
    """
    conn = get_connection()
    if content_preview_len < 0:
        return ToolResult.fail("content_preview_len must be nonnegative")

    def _list():
        doc = _scoped_document(conn.app, document_path)[0] if document_path else conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        page = doc.ActivePage if page_index == 0 else _scoped_page(doc, page_index)

        items = []
        for shape, parent_id in walk_shapes(page.Shapes):
            if shape.Type != CDR_TEXT_SHAPE:
                continue
            item = {
                "shape_id": str(shape.StaticID), "parent_id": parent_id, "name": shape.Name or "",
                "x": shape.PositionX, "y": shape.PositionY, "width": shape.SizeWidth, "height": shape.SizeHeight,
                "writable": False, "text_type": "unknown", "content_preview": "",
            }
            try:
                frame = shape.Text
                story = frame.Story
                content = story.Text
                item["content_preview"] = content[:content_preview_len]
                if len(content) > content_preview_len:
                    item["content_preview"] += "…"
                text_type = frame.Type
                if text_type in (0, 1, 2, 3):
                    item["text_type"] = "paragraph" if text_type in (1, 3) else "artistic"
                item["writable"] = True
                for prop in ("Font", "Size", "Bold", "Italic"):
                    try:
                        item[prop.lower()] = getattr(story, prop)
                    except Exception:
                        item[prop.lower()] = None
            except Exception as exc:
                item["read_error"] = str(exc)
            items.append(item)

        return {
            "page": page.Index, "document_unit": doc.Unit, "total": len(items),
            "writable": sum(item["writable"] for item in items), "shapes": items,
        }

    result = conn.safe_call(_list)
    if result["success"]:
        r = result["result"]
        return ToolResult.ok(
            f"第{r['page']}页共 {r['total']} 个文字形状，{r['writable']} 个可写入",
            **r,
        )
    return ToolResult.fail(result.get("error", "列举文字形状失败"))


def add_guideline(position: float, orientation: str = "horizontal") -> ToolResult:
    """在当前页面添加辅助线。position 为辅助线位置（mm），orientation 为 horizontal 或 vertical。"""
    conn = get_connection()

    def _add():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        doc.Unit = _CDR_MILLIMETER
        page = doc.ActivePage
        orientation_lower = orientation.lower()
        if orientation_lower not in ("horizontal", "vertical"):
            raise ValueError("orientation 必须是 horizontal 或 vertical")

        try:
            if orientation_lower == "horizontal":
                guide = page.ActiveLayer.CreateGuide(0, position, page.SizeWidth, position)
            else:
                guide = page.ActiveLayer.CreateGuide(position, 0, position, page.SizeHeight)
        except Exception:
            try:
                guides = page.Guides
                if orientation_lower == "horizontal":
                    guide = guides.Add(position, 0)
                else:
                    guide = guides.Add(position, 90)
            except Exception:
                raise RuntimeError(f"无法添加 {orientation} 辅助线")

        data = {"position": position, "orientation": orientation_lower}
        try:
            data["shape_id"] = str(guide.StaticID)
        except Exception:
            pass
        return data

    result = conn.safe_call(_add)
    if result["success"]:
        return ToolResult.ok(f"已添加 {orientation} 辅助线: {position} mm", **result["result"])
    return ToolResult.fail(result.get("error", "添加辅助线失败"))


def switch_page(page_index: int) -> ToolResult:
    """切换到指定页面。page_index 为页码（从 1 起）。"""
    conn = get_connection()

    def _switch():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        if page_index < 1 or page_index > doc.Pages.Count:
            raise ValueError(f"页码 {page_index} 超出范围（1-{doc.Pages.Count}）")
        page = _get_page(doc, page_index)
        page.Activate()
        doc.Unit = _CDR_MILLIMETER
        width, height = _get_page_size(doc)
        return {"page_index": page_index, "width": width, "height": height}

    result = conn.safe_call(_switch)
    if result["success"]:
        return ToolResult.ok(f"已切换到第 {page_index} 页", **result["result"])
    return ToolResult.fail(result.get("error", "切换页面失败"))


def delete_page(page_index: int) -> ToolResult:
    """删除指定页面。page_index 为页码（从 1 起）。"""
    conn = get_connection()

    def _delete():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        if doc.Pages.Count <= 1:
            raise RuntimeError("无法删除最后一页")
        if page_index < 1 or page_index > doc.Pages.Count:
            raise ValueError(f"页码 {page_index} 超出范围（1-{doc.Pages.Count}）")
        page = _get_page(doc, page_index)
        page.Delete()
        return {"remaining_pages": doc.Pages.Count}

    result = conn.safe_call(_delete)
    if result["success"]:
        return ToolResult.ok(f"已删除第 {page_index} 页，剩余 {result['result']['remaining_pages']} 页", **result["result"])
    return ToolResult.fail(result.get("error", "删除页面失败"))
