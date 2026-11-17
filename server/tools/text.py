"""文字处理工具 — 替换文字内容、样式设置、溢出检查、创建文本框"""

from itertools import groupby

from core.connection import get_connection
from core.drawing import CDR_TEXT_SHAPE
from core.models import ToolResult
from core.targets import call_on_active
from core.units import MILLIMETER, convert
from tools.inspection import (
    _CDR_NO_FILL,
    _CDR_UNIFORM_FILL,
    _color_query,
    _decode_queried_color,
    _fill_details,
    _on_shape,
    _query,
)

_CDR_PARAGRAPH_TEXT = 1
_ALIGNMENT_MAP = {"left": 0, "center": 3, "right": 1, "none": 0}


def _find_text_shape(shape_id: str):
    """查找文字形状，确保是文字类型"""
    conn = get_connection()
    doc = conn.app.ActiveDocument
    if doc is None:
        return None
    target = str(shape_id)
    try:
        shapes = doc.ActivePage.Shapes
        try:
            shape = shapes(shape_id)
        except Exception:
            for s in shapes:
                try:
                    if str(s.StaticID) == target or s.Name == target:
                        shape = s
                        break
                except Exception:
                    continue
            else:
                return None
        try:
            if shape.Type == CDR_TEXT_SHAPE:
                return shape
        except Exception:
            pass
        try:
            _ = shape.Text
        except Exception:
            return None
        return shape
    except Exception:
        return None


def _get_text_content(text) -> str:
    for getter in (
        lambda: text.Contents,
        lambda: text.GetContents(),
        lambda: text.Story.Text,
        lambda: text.Story,
    ):
        try:
            value = getter()
            if isinstance(value, str):
                return value
        except Exception:
            continue
    return ""


def _set_text_content(text, content: str) -> None:
    for setter in (
        lambda: setattr(text.Story, "Text", content),
        lambda: setattr(text, "Contents", content),
        lambda: text.SetContents(2, content),
        lambda: setattr(text, "Story", content),
    ):
        try:
            setter()
            return
        except Exception:
            continue
    raise RuntimeError("当前 CorelDRAW 版本不支持已知的文字内容写入 API")


def set_text_content_by_id(document_path: str, page_index: int, shape_id: str, content: str) -> ToolResult:
    """Replace an exact editable text story and return the actual text read back from CorelDRAW."""
    def replace(app, doc, shape):
        if shape.Type != CDR_TEXT_SHAPE:
            raise ValueError("The specified shape is not editable text")
        shape.Text.Story.Text = content
        actual = shape.Text.Story.Text
        data = {"content": actual}
        if actual != content:
            data["verification_error"] = "CorelDRAW text differs from the requested content"
        return data

    return _on_shape(document_path, page_index, shape_id, replace, write=True)


_TEXT_QUERY_BATCH = 100
_MAX_TEXT_CHARACTERS = 5000


def _character_fills(shape, story):
    """Return (text, fill) for every character, using batched queries with a direct-read fallback.

    The queries run on the text shape: TextRange.Evaluate has no @com, so queries on the story always failed.
    """
    count = shape.Evaluate("@com.Text.Story.Characters.Count")
    if count > _MAX_TEXT_CHARACTERS:
        raise ValueError(f"Text has {count} characters; the limit is {_MAX_TEXT_CHARACTERS}")
    # convert binds each character to $item, so its path is resolved once, not once per property.
    leaf = (f"array($item.Text, array($item.Fill.Type, iif($item.Fill.Type = {_CDR_UNIFORM_FILL}, "
            f"{_color_query('$item.Fill.UniformColor')}, 0)))")
    characters = []
    for start in range(1, count + 1, _TEXT_QUERY_BATCH):
        indices = range(start, min(count, start + _TEXT_QUERY_BATCH - 1) + 1)
        try:
            values = _query(shape, [f"array(@com.Text.Story.Characters.Item({index})).convert({leaf})"
                                    for index in indices])
            for text, (fill_type, color) in (value[0] for value in values):
                fill = {"type": fill_type}
                if fill_type == _CDR_UNIFORM_FILL:
                    fill["color"] = _decode_queried_color(color)
                elif fill_type != _CDR_NO_FILL:
                    fill["color"] = {"kind": "unknown"}
                characters.append((text, fill))
        except Exception:
            for index in indices:
                character = story.Characters.Item(index)
                characters.append((character.Text, _fill_details(character)))
    return characters


def _fill_runs(characters):
    # Positions count CorelDRAW characters, which Story.Range uses, not Python code points.
    runs, position = [], 0
    for fill, group in groupby(characters, key=lambda character: character[1]):
        texts = [text for text, _ in group]
        text = "".join(texts)
        runs.append({"start": position, "end": position + len(texts), "text": text,
                     "whitespace_only": text.isspace(), "fill": fill})
        position += len(texts)
    return runs


def _text_story(shape):
    if shape.Type != CDR_TEXT_SHAPE:
        raise ValueError("The specified shape is not editable text")
    return shape.Text.Story


def inspect_text_fill_runs(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """List the character fill runs of one exact text shape, including whitespace.

    Each run is a zero-based, end-exclusive character range [start, end) sharing one native fill
    (spot name and tint, process components, registration, none, or unknown). The shape-level
    fill reports only part of mixed text, so check these runs before copying fills or separating.
    """
    def inspect(app, doc, shape):
        characters = _character_fills(shape, _text_story(shape))
        return {"text": "".join(text for text, _ in characters), "runs": _fill_runs(characters)}

    return _on_shape(document_path, page_index, shape_id, inspect)


def _set_text_font(text, font: str) -> None:
    for setter in (
        lambda: setattr(text.Story, "Font", font),
        lambda: setattr(text.FontProperties, "Name", font),
    ):
        try:
            setter()
            return
        except Exception:
            continue


def _set_text_size(text, size: float) -> None:
    for setter in (
        lambda: setattr(text.Story, "Size", size),
        lambda: setattr(text.FontProperties, "Size", size),
    ):
        try:
            setter()
            return
        except Exception:
            continue


def _set_text_bold(text, bold: bool) -> None:
    for setter in (
        lambda: setattr(text.Story, "Bold", bold),
        lambda: setattr(text.FontProperties, "Bold", bold),
    ):
        try:
            setter()
            return
        except Exception:
            continue


def _apply_default_text_style(shape, frame_height: float) -> None:
    text = shape.Text
    size = max(8.0, min(18.0, frame_height * 0.55))
    _set_text_size(text, size)
    try:
        shape.Fill.UniformColor.CMYKAssign(0, 0, 0, 100)
    except Exception:
        pass


def set_text_content(document_path: str, shape_id: str, content: str) -> ToolResult:
    """替换文字形状的文本内容。shape_id 为形状名称或 ID。

    Active document only (document_path); lengths in document units.
    """
    conn = get_connection()

    def _set():
        shape = _find_text_shape(shape_id)
        if shape is None:
            raise ValueError(f"未找到文字形状: {shape_id}")
        old_content = _get_text_content(shape.Text)
        _set_text_content(shape.Text, content)
        text_type = "段落文字" if shape.Text.Type == _CDR_PARAGRAPH_TEXT else "美术字"
        return {"shape_id": shape_id, "old_content": old_content, "new_content": content, "text_type": text_type}

    result = call_on_active(conn, document_path, _set)
    if result["success"]:
        return ToolResult.ok(f"文字已替换: '{result['result']['old_content']}' → '{content}'", **result["result"])
    return ToolResult.fail(result.get("error", "替换文字失败"))


def set_text_style(
    document_path: str,
    shape_id: str,
    font: str = "",
    size: float = 0,
    bold: bool = False,
    italic: bool = False,
    alignment: str = "",
) -> ToolResult:
    """设置文字形状的样式：字体、字号、粗体、斜体、对齐。

    Active document only (document_path); lengths in document units.
    """
    conn = get_connection()

    def _style():
        shape = _find_text_shape(shape_id)
        if shape is None:
            raise ValueError(f"未找到文字形状: {shape_id}")
        changes = {}
        text = shape.Text
        if font:
            _set_text_font(text, font)
            changes["font"] = font
        if size > 0:
            _set_text_size(text, size)
            changes["size"] = size
        if bold:
            _set_text_bold(text, True)
            changes["bold"] = True
        if italic:
            try:
                text.FontProperties.Italic = True
                changes["italic"] = True
            except Exception:
                pass
        if alignment and alignment in _ALIGNMENT_MAP:
            try:
                text.Alignment = _ALIGNMENT_MAP[alignment]
                changes["alignment"] = alignment
            except Exception:
                pass
        if not changes:
            raise ValueError("未指定任何样式变更")
        return {"shape_id": shape_id, "changes": changes}

    result = call_on_active(conn, document_path, _style)
    if result["success"]:
        changes = result["result"]["changes"]
        return ToolResult.ok(f"文字样式已更新: {changes}", **result["result"])
    return ToolResult.fail(result.get("error", "设置文字样式失败"))


def fit_text_to_frame(document_path: str, shape_id: str) -> ToolResult:
    """将段文字自适应到文本框大小（仅对段落文字有效）。

    Active document only (document_path); lengths in document units.
    """
    conn = get_connection()

    def _fit():
        shape = _find_text_shape(shape_id)
        if shape is None:
            raise ValueError(f"未找到文字形状: {shape_id}")
        if shape.Text.Type != _CDR_PARAGRAPH_TEXT:
            raise ValueError(f"只能对段落文字使用自适应，当前类型: {shape.Text.Type}")
        shape.Text.FitTextToFrame = True
        return {"shape_id": shape_id, "fitted": True}

    result = call_on_active(conn, document_path, _fit)
    if result["success"]:
        return ToolResult.ok(f"文字已自适应到文本框", **result["result"])
    return ToolResult.fail(result.get("error", "文字自适应失败"))


def check_text_overflow(shape_id: str, document_path: str = "") -> ToolResult:
    """检查文字是否超出文本框（溢出）。返回溢出状态。

    Reads the active document.
    """
    conn = get_connection()

    def _check():
        shape = _find_text_shape(shape_id)
        if shape is None:
            raise ValueError(f"未找到文字形状: {shape_id}")
        text_type = shape.Text.Type
        overflowing = False
        if text_type == _CDR_PARAGRAPH_TEXT:
            try:
                overflowing = shape.Text.Overflows
            except Exception:
                try:
                    overflowing = shape.Text.IsOverflowing
                except Exception:
                    pass
        content_length = 0
        content_length = len(_get_text_content(shape.Text))
        return {
            "shape_id": shape_id,
            "overflowing": overflowing,
            "text_type": "paragraph" if text_type == _CDR_PARAGRAPH_TEXT else "artistic",
            "content_length": content_length,
        }

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        status = "溢出" if result["result"]["overflowing"] else "正常"
        return ToolResult.ok(f"文字状态: {status}", **result["result"])
    return ToolResult.fail(result.get("error", "检查文字溢出失败"))


def convert_text_to_curves(document_path: str, shape_id: str) -> ToolResult:
    """将文字转换为曲线（印前必须操作，防止字体依赖）。

    Active document only (document_path); lengths in document units.
    """
    conn = get_connection()

    def _convert():
        shape = _find_text_shape(shape_id)
        if shape is None:
            raise ValueError(f"未找到文字形状: {shape_id}")
        shape.ConvertToCurves()
        return {"shape_id": shape_id, "converted": True}

    result = call_on_active(conn, document_path, _convert)
    if result["success"]:
        return ToolResult.ok(f"文字已转曲: {shape_id}", **result["result"])
    return ToolResult.fail(result.get("error", "文字转曲失败"))


def create_text_frame(document_path: str, x: float, y: float, width: float, height: float, text: str) -> ToolResult:
    """创建段落文本框。返回形状 ID。

    Active document only (document_path); x, y, width, height in mm.
    """
    conn = get_connection()

    def _create():
        doc = conn.app.ActiveDocument
        layer = doc.ActivePage.ActiveLayer
        left, top, right, bottom = (convert(value, MILLIMETER, doc.Unit, conn.app)
                                    for value in (x, y, x + width, y + height))
        shape = layer.CreateParagraphText(left, top, right, bottom, text)
        _apply_default_text_style(shape, height)
        shape.Name = f"text_{shape.StaticID}"
        return {
            "shape_id": str(shape.StaticID),
            "name": shape.Name,
            "width": width,
            "height": height,
            "x": x,
            "y": y,
            "unit": "mm",
            "content": text,
        }

    result = call_on_active(conn, document_path, _create)
    if result["success"]:
        return ToolResult.ok(f"文本框创建成功: {width}×{height}", **result["result"])
    return ToolResult.fail(result.get("error", "创建文本框失败"))
