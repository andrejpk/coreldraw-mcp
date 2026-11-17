# CorelDRAW MCP experiment log

Integration branch: `claude/experiments-integration` (cut from `claude/production-followup-tools` @ d41c729, later merged 0d4f40a).
Protocol: [PROTOCOL.md](PROTOCOL.md). Lab folder: `C:\CorelMcpLab` (templates, sample copies, per-run copies, worktrees).
Budget: 20 child-agent cycles. Used: 12 (E00, E01, E08, E17a, E03, E02+E05, E10, E04+E11, E13, E13b, E07+E06, E19+E12).

Current: 67 tools, ~21.5k tokens, 17 ruff errors, acceptance 451/451 + failure 19/19.
Baseline: 99 registered tools, catalog ~20.1k tokens (80,487 chars), 31 ruff errors, 37/37 MCP acceptance checks (incl. Ghostscript plate renders), 3/3 failure-path checks.

Status values: **backlog**, **in progress**, **completed: success | partial | failure**, **dropped**.

## Completed and in progress

| ID | Experiment | Status | Result | Merge |
| --- | --- | --- | --- | --- |
| E00 | Fixture/template library + fresh copies of 4 real sample files | completed: success | 8 templates + MANIFEST.json/md, deterministic rebuild; 4 sample copies profiled (55 to 18,813 objects); builder/profiler/verifier scripts. Found 10 tool shortcomings. [results](results/e00.md) | merged (d930bb2) |
| E01 | Agent-eye usability audit on real sample copies, MCP only | completed: success | 381 MCP calls over 6 production tasks. Underbase build and mark move worked; save-as, spot recolor and spot outlines impossible; a page inventory on Good News took 68 calls / 746 s / 9.3 MB. 16 ranked problems, consolidation map 99 → ~35 core tools. [results](results/e01.md) | merged; `-dOverrideICC` render fix applied on integration (6fab173) |
| E08 | Document management + exact-document targeting | completed: success | 99 → 96 tools. Added `list_documents`, `open_document`, `edit_pages`; removed `open_template`, `get_document_info`, `add_page`, `delete_page`, `set_page_size`, `switch_page`; `save_document` save-as fixed (E01 T6d: 1 call, 0 failures), `close_document` by exact path with dirty refusal. Shared `core/targets.py` + `core/units.py`; 11 forced-mm writes removed; 46 legacy edit tools require `document_path` (refuse on mismatch). Catalog 20.1k → 21.7k tokens (more params). Acceptance 84/84, failure 4/4, ruff 29. Finding: `Document.Undo` acts on the active document, so edits activate their target. [results](results/e08.md) | merged; removed unsafe `close_mine.py` |
| E17a | Structure/ID cache + bridge timeout safety | completed: success | New `core/structure.py` validated page-structure cache (edit counter, layer fingerprints, StaticID check on every path). Good News p1 inventory 765.8 s → 39.4 s (68 calls, identical output); text runs per shape ~15 s → 0.6 s; `get_shape_overprint` 14 s → 0.5 s; `list_all_text_shapes` p1 60 s timeout + 193 s wedge → 0.46 s; big_flat 22.6 → 7.5 s. Fixed text-run batching (`TextRange.Evaluate` has no `@com`). Bridge: interruptible `read_call`, status names running tool. Acceptance 103/103, failure 4/4. [results](results/e17a.md) | merged |
| E03 | `get_ink_inventory` + PowerClip-aware inspection | completed: success | 96 → 95 tools. New read-only `get_ink_inventory` (per page/layer, fill/outline/text runs/fountain/effects/PowerClips, printable/hidden/off-page splits, IDs per ink, plate summary, near-duplicate warnings). Removed `get_color_report`, `check_rgb_colors`; `get_print_separation_settings` reduced to real settings + `disabled_plates`. `inspect_shapes` returns PowerClip contents and effect→control links. Good News whole doc 8.4 s / 18 KB; E01 T2 redone 12 calls / 15.7 s / 77 KB (was 165 calls, 35 failed, 553 s + 9.3 MB inventory). Plates predicted = Ghostscript renders on 21/21 pages. Acceptance 134/134, ruff 28. [results](results/e03.md) | merged |
| E02+E05 | Color editing: `set_fill` / `set_outline` / `set_overprint` / `replace_color` / `undo` | completed: success | 95 → 89 tools. Shared `core/colorspec.py` (spot by name from document + installed palettes, with/without PANTONE prefix, near-match suggestions; accepts inventory entries). Removed 9 narrow tools (`set_fill_cmyk/rgb/pantone`, `set_no_fill`, `set_fountain_fill`, `copy_shape_fill`, `set_no_outline`, `get_shape_overprint`, `copy_fill_to_text_range`); renamed `set_shape_overprint` → `set_overprint`. E01 T6b recolor: 5 calls / 5.0 s / 0 failures (was 20 calls, 2 failures, impossible); Good News 32,822 changes: dry run 6.3 s, real 22.9 s one call verified, undo 5.2 s exact. Acceptance 189/189, ruff 26. [results](results/e02.md) | merged |
| E10 | Plate-level proof feedback | completed: success | 89 → 88 tools. New `render_plates` (proof + Ghostscript tiffsep per plate: coverage, inked bbox in doc units, registration-aware art, `registration_only`, approximate StaticID attribution, diffs vs inventory prediction, hidden-printable warning, generic `contain` protrusion check). `export_layer_proof_pdf` folded into `export_page_proof_pdf(layer_name, include_hidden_printable)`; `simulate_overprints` replaces get/set pair. 21/21 pages agree; E01 T5: 17 calls / 42.5 s / 0 failures / 11/11 pages correct; screen_job choke check 0 px protrusion, seeded fault caught. COM mutex released while Ghostscript runs. Acceptance 229/229, failure 6/6. [results](results/e10.md) | merged |
| E04+E11 | `find_shapes` with artifact presets (+ E15 raw CQL) | completed: success | 88 → 85 tools, catalog unchanged (22.4k tokens). `find_shapes`: combinable filters, 9 presets (zero_size, invisible, stacked_duplicates, off_page, hairline, empty_text, tiny, hidden_printable_content, registration_on_art), read-only `cql` escape hatch, paged, `select=True`. Removed `find_shape_by_name`, `select_shapes`, `list_all_text_shapes`, `select_shapes_by_id`. Inventory `speck_with_ink` (11,597 on Good News p1) → `tiny_with_ink` summary (0 isolated, 2 clusters). artifacts.cdr 9/9 seeded found (E01 heuristics 7/9); Good News p1 presets 1.5–7.0 s. Fixed E17a fingerprint blind spot with CQL `Shapes.toarray()` per layer/container (0.15 s). Acceptance 296/296, failure 6/6, ruff 25. [results](results/e04.md) | merged; orchestrator closed a leftover unsaved `Untitled-1` acceptance fixture |
| E13 | `offset_silhouette`, `combine_shapes`, `duplicate_shapes`, `separate_effect` | completed: partial | Stopped early: CorelDRAW crashed twice during in-process probes (06:54 `BaseToolCore.dll`; 07:14 `VGCore.dll` in `Curve.WeldWith` while a live customer file was open — not dirty at 07:13, not edited). Code: 85 → 81 tools (added 4, removed 8, `convert_to_curves`/`apply_contour` take exact IDs). In-process: T6a underbase 13 calls / 9.4 s → 1 call / 1.4 s, inset 0.748 pt mean; black exclusion works. Acceptance suites NOT run, no MCP checks for new tools, no `render_plates(contain)` check; 400-object cap after a 1,870-object group passed 60 s. [results](results/e13.md) | **not merged** (needs validation on an idle CorelDRAW; consider `Shape.Weld` on temp copies instead of `Curve.WeldWith`) → merged via E13b |
| E13b | Validate and fix E13 on an idle CorelDRAW | completed: success | No crash (same CorelDRAW PID throughout; open-document gate before every session). `offset_silhouette` now welds temp copies with `Shape.Weld` + contour effect (no in-memory curve APIs), 35 s self-undoing deadline, cap 1,000. Fixed multi-target same-layer bug in `duplicate_shapes`/`convert_to_curves`/`apply_contour`/`separate_effect`. T6a through MCP 9 calls / 9.2 s (underbase itself 1 call / 1.9 s); `render_plates(contain)` 0 px protrusion at 0.5 pt inset, seeded fault caught. E13+E13b net: 85 → 81 tools. Acceptance 365/365, failure 13/13, ruff 24, catalog ~22.6k tokens. Limits: ~100 outlined specks per call; `combine_shapes` has no deadline; effect groups refused. [results](results/e13b.md) | merged together with E13 |
| E07+E06 | Layers + transform/arrange + delete/create/import | completed: success | 81 → 67 tools, catalog ~22.6k → ~21.5k tokens. 25 legacy tools → 11 exact-ID tools: added `list_layers`, `set_layer_properties`, `move_shapes_to_layer`, `transform_shapes`, `arrange_shapes`, `delete_shapes`, `create_shape`, `import_file` (working `Layer.Import`); `create_layer`, `group_shapes` (+ungroup), `powerclip` (place/extract) re-signed; `_find_shape` deleted; `combine_shapes` gets 35 s self-undo deadline. T6c 4 calls/1.65 s → 3/0.65 s; T6a 9/9.2 s → 6/7.7 s; Good News delete 300 nested specks 41.4 s → 3.6 s. Works around hidden-layer move/import quirks inside the undo step. Acceptance 451/451, failure 19/19, ruff 17. [results](results/e07.md) | merged |

## Queue (in run order, revised after E01)

| Order | ID | Experiment | Status |
| --- | --- | --- | --- |
| 1 | E08 | Document management + exact-document targeting: `list_documents`, `open_document`/activate (no unit change; merge `open_template`), fixed `save_document` (SaveAs options, save-as copy), `close_document(document_path)`, `edit_pages`; remove all forced `Unit = mm` writes; shared document resolver used by every tool (fail when the active doc differs from `document_path`) | completed: success |
| 2 | E17a | Structure/ID cache + bridge timeout safety (absorbs E20): reuse page structure across paged `inspect_shapes` calls; resolve many IDs in one walk; batch text listing/runs; no read-only call may exceed the bridge timeout; status reports the running tool | completed: success |
| 3 | E03 | `get_ink_inventory`: per page and printable layer, fills/outlines/text runs/effects/PowerClip contents, spot type 25, off-page and hidden-printable splits, locked flags, IDs per ink; replaces `get_color_report`, both `check_rgb_colors`, plate list of `get_print_separation_settings`; `inspect_shapes` descends into PowerClips | completed: success |
| 4 | E02+E05 | Color editing (merged E02 and E05 to save a cycle): `set_fill` / `set_outline` / `set_overprint` (every color model, spot by palette name + tint, registration, none, fountain, from donor; spot outlines; explicit width units; verified read-back) and `replace_color` across fills/outlines/text runs/fountain stops/contours with dry run and one undo step; `undo` tool; removes ~10 tools | completed: success |
| 6 | E10 | Plate feedback: proof + Ghostscript render with registration-aware masks, per-plate bbox/coverage, object attribution, hidden-printable warning; `export_layer_proof_pdf` folded into `export_page_proof_pdf(layer_name=…)` | completed: success |
| 7 | E04+E11 | `find_shapes` with filters and artifact presets (zero-size, invisible, stacked duplicates, off-page, hairline, spot:<name>, Trans. White, text); replaces `find_shape_by_name`, `select_shapes`, `list_all_text_shapes`, `check_text_overflow_all` | completed: success |
| 8 | E13 | `offset_silhouette` (copy + weld + contour + break apart + target layer + overprint, exclusions by color) and `combine_shapes` (weld/trim/intersect/combine, keep sources, target layer) | completed: partial (not merged; follow-up E13b) |
| 9 | E07+E06 | Layers + transform/arrange (merged to save a cycle): `list_layers` / `set_layer_properties` / `move_shapes_to_layer` (exact IDs); `transform_shapes`, `arrange_shapes`, multi-ID `delete_shapes`; `combine_shapes` deadline; fix or remove broken `import_svg`/`import_image` | completed: success |
| 11 | E19+E12 | Catalog diet and feedback quality: drop or share the repeated `ToolResult` output schema (~8.7k tokens), English descriptions, structured units, no silent `except: pass`, token-budget check in the acceptance suite | in progress |
| 12 | E14 | Text: `inspect_text` / `edit_text` (content, style, run fills, overflow, fonts); `convert_to_curves` over IDs | backlog |
| 13 | E09 | `export_document` (formats, pages, layers, IDs) replacing 8 export tools | backlog |
| 14 | E16 | Domain tools (templates, engineering, data merge, barcode, `check_dimensions`): keep / opt-in module / remove | backlog |
| 15 | E15 | Read-only CQL `evaluate` escape hatch (may fold into `find_shapes`) | dropped: covered by `find_shapes(cql=…)` in E04+E11 |
| 16 | E21 | Agent-eye re-audit with the E01 scripts; compare calls, seconds, bytes, correctness | backlog |
| 17 | E18 | Final consolidation: README/tool docs, acceptance refresh, regression on all samples | backlog |
| 8b | E13b | Validate/fix E13 on an idle CorelDRAW: MCP acceptance for the new tools, `render_plates(contain)` check, replace `Curve.WeldWith` with a crash-safe weld, run suites; then merge | completed: success |
| — | spare | cycles held for a fix-up or re-run | — |

## Findings feeding later experiments

### Open follow-ups from completed experiments

- E03: PowerClip contents get new StaticIDs on reopen; effect-generated objects now resolve by ID but CorelDRAW may refuse edits.
- E02: one `replace_color` over several Good News-sized pages could pass the 60 s limit (split by `pages`); `e17a/bench.py` and `e01/` scripts still call removed tools (kept as artifacts; E21 re-audit must port them).
- E10: `include_hidden_printable` assumes the print path follows the printable flag (unverified without a print job). The Friday Threads `separation-check.md` could switch to `render_plates` (skill change, outside this repo; report to user).
- E04: native `FindShapes` treats Name as an exact filter and skips PowerClips (not used). CQL refuses method calls, so `cql` is read-only.
- E13b: ~100 outlined specks per `offset_silhouette` call; effect groups refused.
- E07: moves to another page refused; group/move-to-layer accept top-level shapes only; `import_file` tested with SVG and PNG only.
- E08: edits activate their target document (Undo hazard); `test_e2e.py` updated but not run.

### From E00

- `inspect_shapes` skips PowerClip contents (7 missing on `structure.cdr`); contour/shadow groups are not linked to their control shape (E03).
- `get_color_report` shows Pantone=0 on spot-heavy files (type 25), top-level/current page only (E03).
- `get_print_separation_settings` lists all plates including unused Hexachrome; `plate_type` unreadable (E03/E10).
- `open_template` and 10 other sites set `doc.Unit = mm` silently; `close_document` closes the active doc without a path check (E08).
- Legacy tools return Chinese messages, mm without a unit field; `find_shape_by_name` returns only the first match (E04/E12).
- Publish-to-PDF omits hidden-but-printable layers while print may include them (E10).
- Blends cannot be created through pywin32 (`CreateBlend`); empty groups cannot exist.
- Docs fixed on integration: `cdrMillimeter` is 3 (CLAUDE.md/AGENTS.md said 2).

### From E01 (top problems; full table in [results/e01.md](results/e01.md))

- P1 no ink inventory; paging rescans the page per call (Good News p1: 68 calls, 746 s).
- P2 `save_document(path)` always fails. P3 `set_fill_pantone` always fails; spot outlines cannot be set; `set_outline` converts spot outlines to process.
- P4 73/99 tools bound to the active document; P5 render put K on all CMYK plates (fixed with `-dOverrideICC`).
- P7 one page walk per exact-ID call (up to 17.6 s per text-run call). P8 a 60 s timeout wedges the bridge for minutes.
- P9 catalog ~20k tokens, 43 % a duplicated output schema, 71 Chinese descriptions. P10 no artifact finders; no text/off-page fields.
- P12 weld results land on the first source's layer; P13 `get_document_info`/`get_layers` wrong data.
