# CorelDRAW MCP experiment log

Integration branch: `claude/experiments-integration` (cut from `claude/production-followup-tools` @ d41c729, later merged 0d4f40a).
Protocol: [PROTOCOL.md](PROTOCOL.md). Lab folder: `C:\CorelMcpLab` (templates, sample copies, per-run copies, worktrees).
Budget: 20 child-agent cycles. Used: 3 (E00, E01, E08).

Baseline: 99 registered tools, catalog ~20.1k tokens (80,487 chars), 31 ruff errors, 37/37 MCP acceptance checks (incl. Ghostscript plate renders), 3/3 failure-path checks.

Status values: **backlog**, **in progress**, **completed: success | partial | failure**, **dropped**.

## Completed and in progress

| ID | Experiment | Status | Result | Merge |
| --- | --- | --- | --- | --- |
| E00 | Fixture/template library + fresh copies of 4 real sample files | completed: success | 8 templates + MANIFEST.json/md, deterministic rebuild; 4 sample copies profiled (55 to 18,813 objects); builder/profiler/verifier scripts. Found 10 tool shortcomings. [results](results/e00.md) | merged (d930bb2) |
| E01 | Agent-eye usability audit on real sample copies, MCP only | completed: success | 381 MCP calls over 6 production tasks. Underbase build and mark move worked; save-as, spot recolor and spot outlines impossible; a page inventory on Good News took 68 calls / 746 s / 9.3 MB. 16 ranked problems, consolidation map 99 → ~35 core tools. [results](results/e01.md) | merged; `-dOverrideICC` render fix applied on integration (6fab173) |

## Queue (in run order, revised after E01)

| Order | ID | Experiment | Status |
| --- | --- | --- | --- |
| 1 | E08 | Document management + exact-document targeting: `list_documents`, `open_document`/activate (no unit change; merge `open_template`), fixed `save_document` (SaveAs options, save-as copy), `close_document(document_path)`, `edit_pages`; remove all forced `Unit = mm` writes; shared document resolver used by every tool (fail when the active doc differs from `document_path`) | in progress |
| 2 | E17a | Structure/ID cache + bridge timeout safety (absorbs E20): reuse page structure across paged `inspect_shapes` calls; resolve many IDs in one walk; batch text listing/runs; no read-only call may exceed the bridge timeout; status reports the running tool | backlog |
| 3 | E03 | `get_ink_inventory`: per page and printable layer, fills/outlines/text runs/effects/PowerClip contents, spot type 25, off-page and hidden-printable splits, locked flags, IDs per ink; replaces `get_color_report`, both `check_rgb_colors`, plate list of `get_print_separation_settings`; `inspect_shapes` descends into PowerClips | backlog |
| 4 | E02 | `set_fill` / `set_outline` / `set_overprint`: every color model, spot by palette name + tint, registration, none, fountain, from donor; spot outlines; widths with explicit units; verified read-back; removes ~9 tools | backlog |
| 5 | E05 | `replace_color` across fills/outlines/text runs/contours; dry run; one undo step; `undo` tool | backlog |
| 6 | E10 | Plate feedback: proof + Ghostscript render with registration-aware masks, per-plate bbox/coverage, object attribution, hidden-printable warning; `export_layer_proof_pdf` folded into `export_page_proof_pdf(layer_name=…)` | backlog |
| 7 | E04+E11 | `find_shapes` with filters and artifact presets (zero-size, invisible, stacked duplicates, off-page, hairline, spot:<name>, Trans. White, text); replaces `find_shape_by_name`, `select_shapes`, `list_all_text_shapes`, `check_text_overflow_all` | backlog |
| 8 | E13 | `offset_silhouette` (copy + weld + contour + break apart + target layer + overprint, exclusions by color) and `combine_shapes` (weld/trim/intersect/combine, keep sources, target layer) | backlog |
| 9 | E07 | Layers: `list_layers`, `set_layer_properties`, `move_shapes_to_layer` (exact IDs) | backlog |
| 10 | E06 | `transform_shapes`, `arrange_shapes`, multi-ID `duplicate_shapes` / `delete_shapes` | backlog |
| 11 | E19+E12 | Catalog diet and feedback quality: drop or share the repeated `ToolResult` output schema (~8.7k tokens), English descriptions, structured units, no silent `except: pass`, token-budget check in the acceptance suite | backlog |
| 12 | E14 | Text: `inspect_text` / `edit_text` (content, style, run fills, overflow, fonts); `convert_to_curves` over IDs | backlog |
| 13 | E09 | `export_document` (formats, pages, layers, IDs) replacing 8 export tools | backlog |
| 14 | E16 | Domain tools (templates, engineering, data merge, barcode, `check_dimensions`): keep / opt-in module / remove | backlog |
| 15 | E15 | Read-only CQL `evaluate` escape hatch (may fold into `find_shapes`) | backlog |
| 16 | E21 | Agent-eye re-audit with the E01 scripts; compare calls, seconds, bytes, correctness | backlog |
| 17 | E18 | Final consolidation: README/tool docs, acceptance refresh, regression on all samples | backlog |
| — | spare | 1 cycle held for a fix-up or re-run | — |

## Findings feeding later experiments

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
