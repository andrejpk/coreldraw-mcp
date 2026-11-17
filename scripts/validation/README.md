# Disposable-fixture validation scripts (2026-11-17)

Run from `server/` with the repo venv against a running CorelDRAW 2022. They build their own fixtures in
`<dir>\fx` and never open production files. `<dir>` is any scratch folder containing these scripts.

| Script | Purpose |
| --- | --- |
| `build_accept.py <dir>\fx` | Build `accept.cdr` (text runs, weld stack, overprint/hidden layers, same-named layer on page 2); prints shape IDs as JSON |
| `accept_mcp.py <dir> '<ids json>'` | Acceptance checks through the MCP HTTP server (`mc.py` client, port 8765): E08 document management (open/list/save-as/close/pages, unit preservation, document mismatch) on copies of `colors.cdr`, then text runs, weld and layer proofs on `accept.cdr` while another document is active, then (E17a) structure-cache invalidation after out-of-band COM edits and MCP edits, and `get_coreldraw_status` while a tool runs |
| `accept_failure.py <dir> '<ids json>'` | In-process failure paths: layer-proof restore, partial-weld rollback |
| `build_big.py <dir>\fx` | Build `big_flat.cdr`, a flat 4,452-object layer with mixed native colors |
| `parity.py <dir>\fx build\|compare` | Mixed fixture (groups, zero-width, registration, contour) batched vs direct reader parity |
| `parity_big.py <dir>\fx` | Batched vs direct inventory parity and timings on `big_flat.cdr` (direct reads take ~20 min) |
| `pdfscan.py <pdf>` | Dependency-free PDF check: spot separations and overprint states in inflated streams |
| `build_templates.py <out_dir> [--profiles json] [--only a,b]` | Build the disposable template library (`C:\CorelMcpLab\templates`) and write `MANIFEST.json`/`MANIFEST.md` |
| `profile_cdr.py <out.json> <run dir> <file.cdr>...` | Read-only profile of document copies under a run folder (layers, counts, depth, colors, text, effects, PowerClips) |
| `verify_templates.py <manifest> <copies dir> <out.json> [file...]` | Compare template copies against the manifest through the MCP server |
| `close_run_docs.py <run dir>` | Close only documents saved under a run folder (`Dirty=False` first) |
| `catalog_size.py [out.json]` | Tool count and `list_tools` JSON size (schemas, descriptions, output schemas), overall and per module |
| `audit_lib.py` | Logged MCP client for agent-eye audits: one session, any argument names, JSONL log of tool/args/seconds/bytes/errors; `python audit_lib.py <log>` summarizes per task |
| `audit_manifest.py` | Client-side film manifest (spots by fill/outline/text run, process/registration sources, hidden-printable) and artifact heuristics over `inspect_shapes` inventories |
| `e01/*.py` | E01 task scripts (T1 inventory, T2 manifest, T4 underbase/registration, T5 proofs, T6 mutations, ground-truth check); paths are under `AUDIT_RUN` (default `C:\CorelMcpLab\runs\e01`) |
| `e17a/*.py` | E17a: `bench.py <label> [wedge]` MCP timings and saved outputs (inventories, text runs, the old wedge) in `runs\e17a`; `compare.py <before> <after>` parity; `textlist.py`, `prof_after.py`, `interrupt.py` run in-process from `server/` with no server running |

Results are recorded in `docs/requirements/2026-11-16-screen-separation-tools.md`.
