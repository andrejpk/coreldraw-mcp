# Disposable-fixture validation scripts (2026-11-17)

Run from `server/` with the repo venv against a running CorelDRAW 2022. They build their own fixtures in
`<dir>\fx` and never open production files. `<dir>` is any scratch folder containing these scripts.

| Script | Purpose |
| --- | --- |
| `build_accept.py <dir>\fx` | Build `accept.cdr` (text runs, weld stack, overprint/hidden layers, same-named layer on page 2); prints shape IDs as JSON |
| `accept_mcp.py <dir> '<ids json>'` | Acceptance checks through the MCP HTTP server (`mc.py` client, port 8765): E08 document management (open/list/save-as/close/pages, unit preservation, document mismatch) on copies of `colors.cdr`, then text runs, weld and layer proofs on `accept.cdr` while another document is active, then (E17a) structure-cache invalidation after out-of-band COM edits and MCP edits, and `get_coreldraw_status` while a tool runs, (E03) the ink inventory, (E02+E05) `set_fill`/`set_outline`/`set_overprint`/`set_transparency`/`replace_color`/`undo` on `e02/build_colors.py` and sample copies, (E10) `render_plates` (screen_job plates, attribution, bboxes vs `inspect_shapes`, containment with insets and a seeded protrusion, hidden-printable and layer renders, Knights bitmap-only process plates, `inks.cdr`), merged `export_page_proof_pdf` and `simulate_overprints`, and (E04+E11) `find_shapes` filters, presets, paging, selection and the named-shape cache fingerprint |
| `accept_failure.py <dir> '<ids json>'` | In-process failure paths: layer-proof and hidden-printable proof restore, `render_plates` without Ghostscript, rollback of `combine_shapes` and `offset_silhouette` failures part way |
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
| `e02/build_colors.py <dir>` | Build `e02_colors.cdr` for the color-editing checks (spot fill/outline, tint, CMYK near-match, donor, RGB, fountain, mixed-run text, contour, group, locked layer, master layer); prints shape IDs |
| `e10/validate.py <out> <doc>...` | E10: `render_plates` on every page vs `get_ink_inventory` and an independent `render_separation_proof.py` render |
| `e10/t5.py <out> <doc>...` | E10: E01 task T5 redone (one `render_plates` per page) |
| `e10/concurrency.py <doc> <page> <out>` | E10: polls `get_coreldraw_status` during a render (bridge free while Ghostscript runs) |
| `e10/baseline.py <run> <doc>...` | E10 baseline: `export_page_proof_pdf` + standalone render timings |
| `e02/measure.py <run dir>` | E02+E05 MCP measurements: E01 T6b redone with `replace_color` (inventory + Ghostscript plates + save-as) and a 32,822-change Good News `replace_color` (dry run, real run, undo) |
| `e04/*.py` | E04+E11: `build_finds.py <dir>` (finds.cdr: clean objects + seeded artifacts with expected preset reasons), `measure.py <run>` (every preset on the sample copies through MCP), `groundtruth.py` / `gn_truth.py` (direct-COM duplicate and off-page truth), `probe_*.py` (FindShapes name semantics, CQL `toarray`, CQL safety, fingerprint old vs new with `structure_e17a.py`), `lab.py` (in-process helpers; run-folder documents only) |
| `e13/*.py` | E13: `t6a_before.py` (E01 T6a with the pre-E13 tools, MCP), `dev*.py` (in-process development checks of `offset_silhouette`, `combine_shapes`, effects on run-folder copies), `probe*.py` (COM probes; `probe3.py` and `probe12.py` crashed CorelDRAW, do not rerun on a shared instance), `lab.py` (run-folder helpers) |
| `e17a/*.py` | E17a: `bench.py <label> [wedge]` MCP timings and saved outputs (inventories, text runs, the old wedge) in `runs\e17a`; `compare.py <before> <after>` parity; `textlist.py`, `prof_after.py`, `interrupt.py` run in-process from `server/` with no server running |

Results are recorded in `docs/requirements/2026-11-16-screen-separation-tools.md`.
