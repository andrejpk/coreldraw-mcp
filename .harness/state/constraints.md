# 已知约束

> 每次 Session 发现新约束时，追加到本文件。不要删除历史条目。

## 架构约束


- COM 所有调用必须在同一 STA 线程（`_COMThread`）执行；HTTP 模式下 FastMCP 用线程池分发，不能直接在工具函数里做 COM 调用
- `disconnect()` 只释放 COM 引用，不调用 `Quit()`；CorelDRAW 生命周期由用户自己管理
- 工具逻辑失败不应触发重连或重试。只在连接失效时重新附加；已经开始的工具绝不自动重放。COM 超时不等于取消。
- 多个 stdio 客户端可能各自启动桥接进程。Windows 会话内的命名互斥锁防止这些进程同时操作 CorelDRAW。


## 已知坑

- **cdrTextShape = 3，不是 6**（2026-04-29 CorelDRAW 2020 验证）
  多处旧代码误写为 6，导致 check_text_overflow_all / check_missing_fonts / populate_title_block 漏检所有文字形状。已修复。
  **Correction (2026-11-16):** The CorelDRAW SDK and a CorelDRAW 2022 fixture confirm `cdrTextShape = 6` and `cdrCurveShape = 3`. Use the definitions in `server/core/drawing.py`.
- **layer.Color 返回 COM 对象**，不能直接放入 dict；需 `int(c)` 或 `str(c)` 转换，否则 FastMCP 序列化报 outputSchema 错误
- **register_tools() 单条 import 语句**：任一子模块 import 失败，全部工具注册失败，MCP 客户端看到空工具列表（Agent 会误报"工具未注册"）
- CorelDRAW X6：`ExportEx` 接受 None 参数但不接受 `app.CreateRect()` 返回的 COM rect；`ExportBitmap` 同样；fallback 用 `doc.Export(path, filter, scope, None, None)`

## 发现时间

- [2026-04-23] 初始化时记录
- [2026-04-29] cdrTextShape 常量、layer.Color 序列化、register_tools 单点失败
- [2026-11-17] The bridge cancels any single COM operation after 60 s; chunk long work. — `core/connection.py` `_call` timeout
- [2026-11-17] Dynamic COM reads cost two round trips each (1–25 ms on large docs); batch reads through `Shape.Evaluate("array(@com...)")`. — profiled 4,452-object layer
- [2026-11-17] `Shapes.FindShape` treats Name as a filter ("" matches only unnamed shapes); it cannot look up by StaticID alone. — fixture test
- [2026-11-17] Publish to PDF follows layer Printable, not Visible; changing Printable sets Document.Dirty. — fixture test
