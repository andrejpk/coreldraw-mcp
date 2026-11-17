# Disposable-fixture validation scripts (2026-11-17)

Run from `server/` with the repo venv against a running CorelDRAW 2022. They build their own fixtures in
`<dir>\fx` and never open production files. `<dir>` is any scratch folder containing these scripts.

| Script | Purpose |
| --- | --- |
| `build_accept.py <dir>\fx` | Build `accept.cdr` (text runs, weld stack, overprint/hidden layers, same-named layer on page 2); prints shape IDs as JSON |
| `accept_mcp.py <dir> '<ids json>'` | 33 acceptance checks through the MCP HTTP server (`mc.py` client, port 8765) |
| `accept_failure.py <dir> '<ids json>'` | In-process failure paths: layer-proof restore, partial-weld rollback |
| `build_big.py <dir>\fx` | Build `big_flat.cdr`, a flat 4,452-object layer with mixed native colors |
| `parity.py <dir>\fx build\|compare` | Mixed fixture (groups, zero-width, registration, contour) batched vs direct reader parity |
| `parity_big.py <dir>\fx` | Batched vs direct inventory parity and timings on `big_flat.cdr` (direct reads take ~20 min) |
| `pdfscan.py <pdf>` | Dependency-free PDF check: spot separations and overprint states in inflated streams |
| `close_mine.py` | Close only fixture documents saved under a `scratchpad` path |

Results are recorded in `docs/requirements/2026-11-16-screen-separation-tools.md`.
