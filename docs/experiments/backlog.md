# CorelDRAW MCP experiment log

Integration branch: `claude/experiments-integration` (cut from `claude/production-followup-tools` @ d41c729).
Protocol: [PROTOCOL.md](PROTOCOL.md). Lab folder: `C:\CorelMcpLab` (templates, sample copies, per-run copies, worktrees).
Budget: 20 child-agent cycles. Used: 1.

Baseline: 99 registered tools, 31 ruff errors, 37/37 MCP acceptance checks (incl. Ghostscript plate renders), 3/3 failure-path checks.

Status values: **backlog**, **in progress**, **completed: success | partial | failure**, **dropped**.

| ID | Experiment | Status | Result | Merge |
| --- | --- | --- | --- | --- |
| E00 | Fixture/template library + fresh copies of 4 real sample files | in progress | | |
| E01 | Agent-eye usability audit: run realistic inspection/separation/recolor tasks on real sample copies using only MCP; log friction, failures, timings | backlog | | |
| E02 | Color-setting consolidation: `set_fill` / `set_outline` (all color models, spot+tint, registration, none, fountain, copy-from-donor) on exact IDs with verified read-back; remove 8 narrow tools | backlog | | |
| E03 | Document-wide color & plate inventory (replaces `get_color_report`, both `check_rgb_colors`): all pages/layers, nested groups, PowerClips, text runs, outlines, effects; spot type 25; printable flags; object IDs per ink; batched | backlog | | |
| E04 | `find_shapes` query tool (color, type, size/zero-area, invisible, hairline, layer, text, name); replaces `find_shape_by_name`, `select_shapes`, `list_all_text_shapes` | backlog | | |
| E05 | Batch `replace_color` across fills/outlines/text runs/contours with dry-run, one undo step, verification; `undo` tool | backlog | | |
| E06 | Transform/arrange consolidation on exact IDs (`transform_shapes`, `arrange_shapes`, merged delete/duplicate) | backlog | | |
| E07 | Layer tool consolidation (`list_layers` all pages+master, `set_layer_properties`, `move_shapes_to_layer`) | backlog | | |
| E08 | Document management reliability: `list_documents`, `open_document` (merge `open_template`), fixed `save_document` (SaveAs options), exact close; page tools on exact doc | backlog | | |
| E09 | Export consolidation into one `export_document` (formats, page/layer/ids scope) | backlog | | |
| E10 | Dependency-light separation plate rendering + per-plate coverage/bbox feedback (no Ghostscript) | backlog | | |
| E11 | Cleanup/artifact finder presets: stacked duplicates, zero-area, invisible, empty groups, off-page, stray registration/Trans. White | backlog | | |
| E12 | Error/feedback quality + boilerplate reduction: remove silent `except: pass`, structured errors with hints and document context, shared tool wrapper | backlog | | |
| E13 | Generic offset silhouette (copy+weld+contour+break-apart, one undo step, exclusions by color) for chokes/spreads/underbases | backlog | | |
| E14 | Text tool consolidation (`inspect_text` incl. runs/fonts/overflow, `edit_text`); fold `check_missing_fonts` | backlog | | |
| E15 | Read-only CQL `evaluate` escape hatch | backlog | | |
| E16 | Review domain-specific tools (templates, engineering, data merge, preflight) for keep/merge/remove in a general-purpose server | backlog | | |
| E17 | Performance pass on real sample copies (inspection, exact-ID resolution, text runs) | backlog | | |
| E18 | Final consolidation: README/tool docs, acceptance suite refresh, regression on all samples | backlog | | |

## Notes

Order is adjusted as results come in. E01 findings may reprioritize or replace later items.
