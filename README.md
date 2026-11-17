# CorelDRAW Signage MCP

> Let AI drive CorelDRAW directly via the Model Context Protocol — automated design file generation at scale

[中文文档](README-CN.md)

## Overview

This project exposes CorelDRAW as an MCP (Model Context Protocol) tool server. An AI Agent connects via COM API to create documents, replace text, manipulate shapes, run preflight checks, and batch-export production files — all from natural language instructions.

**Key capabilities**

- Natural language → CorelDRAW operations, no manual repetition
- Template filling and batch export to PDF / DXF / PNG
- Streamlit Chat UI for local debugging, or connect any MCP-compatible client (Claude Desktop, OpenCode, etc.)

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Designer's machine (Windows)        │
│                                                 │
│  ┌──────────────┐      ┌────────────────────┐  │
│  │  AI Agent    │─MCP─▶│   MCP Server       │  │
│  │ (runner.py)  │◀─────│ (FastMCP / HTTP)   │  │
│  │              │      │                    │  │
│  │  Streamlit   │      │  CorelDRAW COM API │  │
│  │  Chat UI     │      │  (pywin32)         │  │
│  └──────────────┘      └────────┬───────────┘  │
│                                 │               │
│                         ┌───────▼──────┐        │
│                         │  CorelDRAW   │        │
│                         │ (local proc) │        │
│                         └──────────────┘        │
└─────────────────────────────────────────────────┘
         │ LLM API calls
         ▼
  Company LiteLLM Proxy (planned)
  or Anthropic / DeepSeek / Qwen direct
```

**Three-layer structure**

| Layer | Component | Description |
|-------|-----------|-------------|
| Agent | `server/agent/runner.py` | LLM tool-call loop; supports Claude / DeepSeek / Qwen |
| MCP tools | `server/server.py` + `server/tools/` | 30+ CorelDRAW tools, HTTP or stdio transport |
| CorelDRAW | `server/core/connection.py` | Drives local CorelDRAW via pywin32 COM API |

**Tool modules**

| Module | Tools | Functionality |
|--------|-------|---------------|
| `document` | 7 | `list_documents`, `open_document`, `create_document`, `save_document` (save-as with overwrite guard), `close_document` (by path), `edit_pages` (add/delete/resize/activate), guidelines. Every tool takes an exact `document_path`; units are never changed |
| `shapes` | 16 | Rectangle / ellipse / line drawing, SVG / image import, arrange, group (legacy, active document) |
| `geometry` + `silhouette` + `effects` | 8 | `offset_silhouette` (combined underbase / spread in one call: welds copies of fills, stroked outlines and text of exact objects across layers with `Shape.Weld`, skips `exclude_colors`, contour effect inside or outside, fill, overprint, target layer; verified area and inset; stops and undoes itself after 35 s), `combine_shapes` (weld / trim / intersect / combine / break apart, keep sources, target layer, order-preserving weld), `duplicate_shapes`, `convert_to_curves` (exact IDs), `apply_contour`, `separate_effect` (contour, drop shadow, extrude, blend), `set_shape_position_exact`, `delete_shape_by_id`; one undo step each, failures rolled back |
| `find` | 1 | `find_shapes`: combinable filters (type, name glob, color spec on fill/outline/text runs, sizes, bounds, on/off page, layer flags, text, effects, PowerClips, depth, overprint, read-only CQL) and cleanup presets (zero size, invisible, stacked duplicates, off page, hairline, empty text, tiny, hidden-but-printable, registration on art); paged records, counts, optional UI selection |
| `text` | 7 | Text replacement, style, fill runs, overflow detection, text frames |
| `colors` + `recolor` | 6 | `set_fill` (spot by name + tint, CMYK, RGB, registration, none, fountain, donor, text ranges), `set_outline` (spot outlines, explicit width units), `set_overprint`, `set_transparency`, `replace_color` (scope-wide, dry run, one undo step, verified), `undo` |
| `proof` + `plates` | 4 | `export_page_proof_pdf` (page or one layer, optional hidden-but-printable layers, state restored), `render_plates` (Ghostscript films per plate: coverage, bboxes, registration-only, object attribution, predicted vs rendered, containment/choke check), `simulate_overprints`, `get_print_separation_settings` |
| `layers` | 5 | Create, query, assign, show/hide, lock layers |
| `export` | 7 | PDF / DXF / AI / SVG / PNG export, visual preview, batch export |
| `preflight` | 4 | Size check, text overflow, missing fonts, color report |
| `data_merge` | 3 | Excel data read, barcode / QR code generation |

---

## Requirements

- **OS**: Windows 10 / 11 (CorelDRAW COM API is Windows-only)
- **Python**: 3.11+, **64-bit** (must match CorelDRAW's bitness)
- **CorelDRAW**: X6 or later (must be installed and activated; X6 verified compatible)
- **LLM API Key**: one of Anthropic Claude, DeepSeek, or Alibaba Qwen

---

## Installation

```bash
# 1. Clone the repo
git clone <repo-url>
cd coreldraw-signage-mcp

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
copy .env.example .env
# then fill in a real LLM API key
```

Minimal `.env`:

```dotenv
# Pick one LLM provider
ANTHROPIC_API_KEY=sk-ant-xxxxx

# MCP Server transport (stdio or streamable-http)
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=8765
```

---

## Usage

### Option 1 — Streamlit Chat UI (recommended for quick start)

```bash
# Make sure CorelDRAW is running first, then:
streamlit run server/app.py
```

Open `http://localhost:8501`, enter your API key in the sidebar, and control CorelDRAW with natural language.

> **Note**: The Streamlit UI connects to CorelDRAW directly via COM. Do **not** run `server.py` at the same time — two simultaneous COM connections can cause conflicts.

### Option 2 — Codex launches the local MCP bridge

Use a stdio MCP entry in Codex. Replace the paths with your checkout's full paths:

```toml
[mcp_servers.coreldraw]
command = 'C:/path/to/coreldraw-mcp/.venv/Scripts/python.exe'
args = ['C:/path/to/coreldraw-mcp/server/server.py', '--stdio']
cwd = 'C:/path/to/coreldraw-mcp'
tool_timeout_sec = 90
```

Codex launches the bridge when it initializes the MCP connection and owns
the child process. Open CorelDRAW yourself in the same Windows session.
The bridge can start while CorelDRAW is closed; use `get_coreldraw_status`
to check whether COM is ready. CorelDRAW 2022 can omit the application from
the Running Object Table. The bridge uses COM dispatch as a fallback only
after verifying the registered executable is already running for the same
user in the same Windows session. Avoid closing CorelDRAW during a tool call.

The 90-second client timeout leaves room for the bridge's 60-second COM
deadline and two seconds of queue time. Expired queued requests are cancelled.
The bridge never replays a tool call. If a running call stalls, look for a
CorelDRAW dialog and verify the document or export before retrying. A call
may finish after timing out. Separate stdio clients serialize CorelDRAW
operations within the Windows session. If a client is busy, wait for its call
to finish before submitting another.

The stdio bridge writes diagnostics to the client's stderr capture. If the
connection stays wedged, reconcile the last operation, then start a fresh
client session. Apply MCP configuration changes by reopening Codex if its
active connection is stale.

### Option 3 — start an HTTP bridge manually for an HTTP client

```powershell
.\.venv\Scripts\python.exe server\server.py --local-http
```

Connect to `http://127.0.0.1:8765/mcp` (the URL in `.mcp.json`).
`/healthz` checks the listener; `/readyz` independently checks CorelDRAW.
The manual HTTP bridge rotates its log at `logs/server.log`. MCP itself needs
no LLM API key; the Streamlit and agent integrations do.

---

## Example prompts

**Batch room-number signs**

1. Place a CDR template (with text placeholders) in `server/templates/`
2. Prepare an Excel sheet (one sign per row)
3. Type: `Batch generate room signs, template: room_template.cdr, data: rooms.xlsx`
4. The agent reads the data, fills the template row by row, runs preflight, and exports PDF + DXF

**Single sign, quick output**

```
Create a 300×150 mm room sign, room number 301, department "R&D Center",
background CMYK(0,0,0,80), export print-ready PDF and laser-cut DXF
```

---

## Directory structure

```
coreldraw-signage-mcp/
├── server/
│   ├── server.py          # MCP Server entry point
│   ├── app.py             # Streamlit Web UI
│   ├── agent/
│   │   ├── runner.py      # Agent main loop (LLM + tool calls)
│   │   └── prompts.py     # System prompt
│   ├── tools/             # MCP tool implementations (30+ tools)
│   ├── core/              # CorelDRAW COM connection & shared models
│   ├── config/            # Settings & template registry
│   └── templates/         # CDR template files
├── docs/                  # Architecture docs & diagrams
├── CONTRIBUTING.md
├── LICENSE
├── NOTICE
├── requirements.txt
└── pyproject.toml
```

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Lint
ruff check server/

# End-to-end test (requires CorelDRAW running locally)
cd server
python test_e2e.py
```

---

## Roadmap

| Phase | Status | Scope |
|-------|--------|-------|
| Phase 1 MVP | ✅ Done | MCP Server + 30+ tools + Agent loop + HTTP transport |
| Phase 2 | In progress | LiteLLM Proxy for unified LLM management; migrate agent orchestration to LangGraph |
| Planned | Architecture drafted | Company dispatch mode: task queue, multi-workstation workers, concurrency lock |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Note: the COM API is Windows-only, so end-to-end testing requires a Windows machine with CorelDRAW installed.

---

## License

Apache 2.0 © 2026 深圳市玄熵智能科技有限责任公司 (Xuanshang Intelligent Technology Co., Ltd., Shenzhen)
