"""印前质检工具 — 尺寸校验、文字溢出、缺失字体、色彩报告"""

from core.connection import get_connection
from core.drawing import CDR_TEXT_SHAPE
from core.models import ToolResult
from core.targets import call_on_active
from core.units import MILLIMETER, convert

_CDR_PARAGRAPH_TEXT = 1


def check_dimensions(expected_width: float, expected_height: float, tolerance: float = 0.5, document_path: str = "") -> ToolResult:
    """校验当前页面尺寸是否在容差范围内。

    Reads the active document. Sizes and tolerance in mm.
    """
    conn = get_connection()

    def _check():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        page = doc.ActivePage
        actual_w = convert(page.SizeWidth, doc.Unit, MILLIMETER, conn.app)
        actual_h = convert(page.SizeHeight, doc.Unit, MILLIMETER, conn.app)
        w_diff = abs(actual_w - expected_width)
        h_diff = abs(actual_h - expected_height)
        w_ok = w_diff <= tolerance
        h_ok = h_diff <= tolerance
        return {
            "passed": w_ok and h_ok,
            "expected": {"width": expected_width, "height": expected_height},
            "actual": {"width": round(actual_w, 2), "height": round(actual_h, 2)},
            "diff": {"width": round(w_diff, 2), "height": round(h_diff, 2)},
            "tolerance": tolerance,
            "unit": "mm",
        }

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        if result["result"]["passed"]:
            return ToolResult.ok("尺寸校验通过", **result["result"])
        return ToolResult.ok("尺寸校验未通过", **result["result"])
    return ToolResult.fail(result.get("error", "尺寸校验失败"))


def check_text_overflow_all(document_path: str = "") -> ToolResult:
    """检查全文档所有文字形状的溢出状态。返回所有溢出的文字列表。

    Reads the active document.
    """
    conn = get_connection()

    def _check():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")
        page = doc.ActivePage
        overflow_items = []
        total_text = 0
        try:
            for s in page.Shapes:
                if s.Type == CDR_TEXT_SHAPE:
                    total_text += 1
                    try:
                        if s.Text.Type == _CDR_PARAGRAPH_TEXT:
                            overflowing = False
                            try:
                                overflowing = s.Text.Overflows
                            except Exception:
                                try:
                                    overflowing = s.Text.IsOverflowing
                                except Exception:
                                    pass
                            if overflowing:
                                content = ""
                                try:
                                    content = s.Text.Story
                                except Exception:
                                    pass
                                overflow_items.append({
                                    "name": s.Name,
                                    "shape_id": str(s.StaticID),
                                    "content_preview": content[:50] if content else "",
                                })
                    except Exception:
                        continue
        except Exception:
            pass
        return {
            "passed": len(overflow_items) == 0,
            "total_text_shapes": total_text,
            "overflow_count": len(overflow_items),
            "overflow_items": overflow_items,
        }

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        r = result["result"]
        if r["passed"]:
            return ToolResult.ok(f"所有 {r['total_text_shapes']} 处文字正常，无溢出", **r)
        return ToolResult.ok(f"发现 {r['overflow_count']} 处文字溢出", **r)
    return ToolResult.fail(result.get("error", "文字溢出检查失败"))


def check_missing_fonts(document_path: str = "") -> ToolResult:
    """检查文档是否使用了系统中缺失的字体。

    Reads the active document.
    """
    conn = get_connection()

    def _check():
        doc = conn.app.ActiveDocument
        if not doc:
            raise RuntimeError("没有打开的文档")

        # 优先从 CorelDRAW Fonts 集合获取可用字体列表（最可靠）
        available_fonts: set[str] = set()
        use_font_list = False
        try:
            font_col = conn.app.Fonts
            for i in range(1, font_col.Count + 1):
                try:
                    available_fonts.add(font_col.Item(i).Name.lower())
                except Exception:
                    pass
            use_font_list = bool(available_fonts)
        except Exception:
            pass

        missing_fonts: set[str] = set()
        page = doc.ActivePage
        try:
            for s in page.Shapes:
                if s.Type != CDR_TEXT_SHAPE:
                    continue
                # 收集形状使用的字体（字符级优先，形状级 fallback）
                shape_fonts: set[str] = set()
                try:
                    chars = s.Text.Characters
                    for ci in range(1, chars.Count + 1):
                        try:
                            shape_fonts.add(chars.Item(ci).Properties.Font)
                        except Exception:
                            pass
                except Exception:
                    pass
                if not shape_fonts:
                    try:
                        shape_fonts.add(s.Text.FontProperties.Name)
                    except Exception:
                        pass

                for font_name in shape_fonts:
                    if not font_name:
                        continue
                    if use_font_list:
                        # 与 Fonts 集合比对，无需创建测试形状
                        if font_name.lower() not in available_fonts:
                            missing_fonts.add(font_name)
                    else:
                        # fallback：创建测试形状后读回实际字体名，检测是否被静默替换
                        try:
                            test = page.ActiveLayer.CreateArtisticText(0, 0, "T", Font=font_name)
                            actual = test.Text.FontProperties.Name
                            test.Delete()
                            if actual.lower() != font_name.lower():
                                missing_fonts.add(font_name)
                        except Exception:
                            missing_fonts.add(font_name)
        except Exception:
            pass

        missing_list = sorted(list(missing_fonts))
        return {"passed": len(missing_list) == 0, "missing_fonts": missing_list, "count": len(missing_list)}

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        r = result["result"]
        if r["passed"]:
            return ToolResult.ok("所有字体均已安装", **r)
        return ToolResult.ok(f"发现 {r['count']} 个缺失字体", **r)
    return ToolResult.fail(result.get("error", "字体检查失败"))



