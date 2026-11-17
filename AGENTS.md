# CorelDRAW Signage Agent — Agent 工作规范

> 非自推信息。harness 流程由 Hook 强制执行，本文不重复。

## 平台核心约束

- **所有 COM 调用仅 Windows 有效**。macOS 只能做语法检查（AST parse + import 不含 COM 的模块）
- **Python 必须是 64-bit**（`pywin32` 与 CorelDRAW 位数必须一致，32-bit Python 会导致 COM 调用静默失败）
- **CorelDRAW 必须预先启动**，MCP Server 连接失败不会自动拉起

## 入口与运行方式

| 入口 | 命令 | 用途 |
|------|------|------|
| MCP Server | `cd server && python server.py` | 工具提供方，供 Claude/OpenCode 连接 |
| Streamlit UI | `cd server && streamlit run app.py` | 设计师调试用的 Chat 界面 |
| Agent 脚本 | `from agent.runner import SignageAgent` | 编程方式调用 |

MCP Server 默认 `stdio` 传输，设 `MCP_TRANSPORT=streamable-http` 启动 HTTP 模式（端口 8765），`.mcp.json` 已配好连接地址。

## 工具层架构约定（添加/修改工具时必须遵守）

1. **工具是裸函数**，不用 `@mcp.tool()` 装饰器。由 `server/server.py` 的 `register_tools()` 统一 `mcp.add_tool()`。
2. **所有工具返回 `ToolResult`**（`from core.models import ToolResult`），用 `ToolResult.ok()` / `ToolResult.fail()` 构建。
3. **统一模式**：
   ```python
   def tool_name(param: type) -> ToolResult:
       conn = get_connection()
       result = conn.safe_call(lambda: actual_com_work())
       if result["success"]: return ToolResult.ok(...)
       return ToolResult.fail(result.get("error", "失败"))
   ```
   连接检查、重新连接与超时处理由 `safe_call` 统一管理。不要用缓存的 `conn.status` 拦截工具，也不要重试可能已写入的操作。
4. **所有工具注册在 `server.py`**，新增工具必须同时在该文件加 `mcp.add_tool()`。
5. **函数 docstring 即 MCP 工具描述**，会被 Agent 看到，写清楚参数含义。
6. **COM 常量是硬编码整数**（`cdrDXF=86`, `cdrPNG=776`, `cdrMillimeter=3`, `cdrTextShape=3` 等）。这些常量来自 CorelDRAW 类型库，只有 Windows 上运行 `makepy` 才能生成类型存根，不要试图从 `win32com.client.constants` 导入。

## 已知的代码重复与特殊处理

- 按名称/活动页查找形状的 `_find_shape()` 已在 E07 删除：形状、图层、变换工具一律按 StaticID 和页码经 `core/structure.py` 定位（导入用 `Layer.Import` + `StructImportOptions`）。颜色工具（`colors.py`、`recolor.py`）按 StaticID 经 `core/structure.py` 定位，颜色规格统一由 `core/colorspec.py` 解析
- 颜色、油墨与印版检查统一用 `get_ink_inventory`（`tools/inks.py`）。旧的 `get_color_report` 和两份 `check_rgb_colors` 已在 E03 删除，Agent runner 的工具去重也随之移除。
- 印版（胶片）视觉与数值反馈用 `render_plates`（`tools/plates.py`，Ghostscript 在 COM 桥之外运行，`core/separations.py` 不含 COM）。`export_layer_proof_pdf` 已并入 `export_page_proof_pdf(layer_name=…)`，`get/set_simulate_overprints` 合并为 `simulate_overprints`（E10）。
- PNG 导出有两套 API：`ExportBitmap`（主方案）和 `Export`（fallback），因不同 CorelDRAW 版本行为不一
- 专色按名称查找：先查文档调色板，再查已安装专色调色板（读取 `Color\Palettes\Spot` 的 XML），用 `app.CreateSpotColorByName(guid, name, tint)` 创建（旧的 `FindPantone` 方案从未生效，E02 已删除）。`ShapeRange.SetOutlineProperties` 经 pywin32 需用 `InvokeTypes` 传全部 14 个类型化参数（见 `tools/colors.py`）

## Agent (runner.py) 关键约定

- `SignageAgent(provider="anthropic"|"openai", model=..., api_key=..., base_url=...)`
- API Key 环境变量：`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY`
- `run_single()` — 同步阻塞，返回 dict
- `run_single_stream()` — 生成器，逐事件 yield（供 Streamlit 消费）。事件类型：`thinking`, `text`, `tool_call`, `tool_result`, `preview`, `final`, `error`
- Provider 为 `openai` 时，DeepSeek 用 `base_url="https://api.deepseek.com"`，千问用 `base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"`
- 工具定义通过 `inspect.signature` 自动从函数签名生成

## 校验命令

```
ruff check server/      # lint（line-length=120, py311, E/F/I/N/W）
python -m unittest discover -s server -p test_connection.py
python server/test_e2e.py      # 端到端测试（需 Windows + CorelDRAW 运行中）
```

## 环境配置

```bash
cp .env.example .env   # 填一个 LLM API Key 即可
```

`.env` 最小内容：任意一个 `*_API_KEY` + `MCP_TRANSPORT`（默认 stdio）。其他均有默认值。

## 当前阶段

Phase 1 MVP 全部完成（feat-001～004 passes=true）。Phase 2 批量生产待开始。详见 `.harness/product/backlog.md`。
