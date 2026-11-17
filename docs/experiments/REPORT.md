# CorelDRAW MCP experiment program: final report

Date: 2026-11-17. Branch: `claude/experiments-integration`. PR: andrejpk/coreldraw-mcp#2 (stacked on #1).
Log: [backlog.md](backlog.md). Per-experiment write-ups: [results/](results/). Protocol: [PROTOCOL.md](PROTOCOL.md).

## Goal

Make this CorelDRAW MCP server something agents can use reliably and quickly, with useful feedback, for complex vector cleanup, separations, inspection, analysis, repairs and color changes. The Friday Threads film skills were the reference workload. The second goal was simplification: fewer, more powerful tools, while staying a general-purpose CorelDRAW server.

## Outcome in numbers

| | Start | End |
| --- | --- | --- |
| Registered tools | 99 | **60** |
| Tool catalog (`list_tools` JSON) | 80,487 chars (~20.1k tokens) | **54,070 chars (~13.5k tokens), −33 %** |
| Descriptions in Chinese | 71 | 0 |
| Tools that act only on the active document | 73 | 0 (every tool targets an exact `document_path`; edits refuse a mismatch) |
| MCP acceptance checks | 37 | **504** |
| In-process failure-path checks | 3 | **19** |
| `ruff check server/` errors | 31 | **11** |
| Child-agent cycles | budget 20 | 13 used |

### Production tasks, before and after

The before numbers come from the E01 audit, which used only MCP calls on copies of real customer files.

| Task | Before | After |
| --- | --- | --- |
| Full inventory of an 18.8k-object page (Good News p1) | 68 calls, 746–766 s, 9.3 MB | 68 paged calls, 39 s (identical output); the ink inventory for the whole document is 1 call, 8.4 s, 18 KB |
| Film/plate manifest for every page of 4 sample files (T2) | 165 calls (35 failed), 553 s, plus 9.3 MB of inventory | 12 calls, 15.7 s, 77 KB; matched the Ghostscript renders on 21/21 pages |
| Text-run read for one text shape (Good News) | ~15 s | 0.6 s |
| Recolor one spot ink to another across fills, outlines and text (T6b) | Impossible: spot outlines could not be set and a new spot could not be applied | 5 calls, 5.0 s, verified by plates |
| Recolor 32,822 objects (Good News) | n/a | dry run 6.3 s, real run 22.9 s in one call, undo 5.2 s exactly |
| Combined underbase with choke (T6a) | 17 calls, 21 s | 6 calls, 7.7 s; the build itself is 1 call (~1.9 s), verified with `render_plates(contain=…)` |
| Move the bottom registration mark (T6c) | 4 calls, 1.65 s | 3 calls, 0.65 s |
| Plate proofs per page (T5) | Manual export plus render; the process plates were misleading | 17 calls, 42.5 s, 11/11 pages correct, registration separated, objects attributed |
| Save-as to a new path | Always failed | 1 call, 0.6 s |
| Artifact finding (seeded test file) | Client heuristics found 7/9 | `find_shapes` presets found 9/9; under 7 s per preset on Good News |
| Delete 300 nested specks | 41.4 s | 3.6 s |
| Text audit of a page (Good News p1) | 22 calls, 9.5 s | 1 call, 1.26 s |

## What we did

Each experiment ran on its own branch, one at a time on the shared CorelDRAW 2022 instance, using disposable copies in `C:\CorelMcpLab`. The orchestrator reviewed each one and merged it into `claude/experiments-integration`.

| ID | Experiment | Outcome |
| --- | --- | --- |
| E00 | Fixture library (8 templates with a manifest) and byte copies of 4 real files | Success |
| E01 | Agent-eye usability audit with MCP only: 381 calls, 16 ranked problems | Success (it set the order of the remaining experiments) |
| E08 | Exact-document targeting (`core/targets.py`), `list_documents`/`open_document`/`edit_pages`, working save-as and close; removed 11 forced unit changes | Success |
| E17a | Validated page-structure cache (`core/structure.py`), batched ID resolution, interruptible reads, `status` shows the running tool | Success |
| E03 | `get_ink_inventory` (inks, sources, plate prediction); `inspect_shapes` reads PowerClip contents; removed 3 wrong color reports | Success |
| E02+E05 | Shared color spec (`core/colorspec.py`); `set_fill`, `set_outline`, `set_overprint`, `replace_color`, `undo`; removed 9 tools | Success |
| E10 | `render_plates` (per-plate Ghostscript metrics, attribution, containment check); merged the proof and overprint tools | Success |
| E04+E11 | `find_shapes` (filters, 9 artifact presets, read-only CQL); fixed a blind spot in the cache fingerprint; removed 4 tools | Success (also covered the planned CQL escape hatch, E15) |
| E13 | `offset_silhouette`, `combine_shapes`, `duplicate_shapes`, `separate_effect` | Partial: CorelDRAW crashed twice during in-process probes, so the work was not merged |
| E13b | Revalidated E13 on an idle CorelDRAW; replaced the crashing in-memory weld with `Shape.Weld` on temporary copies; added time limits | Success (E13 and E13b merged together) |
| E07+E06 | Replaced 25 legacy name-based layer and shape tools with 11 exact-ID tools; working `import_file` | Success |
| E19+E12 | Catalog diet (−35 %), English descriptions, shared instructions, consistent errors (`core/results.py`), silent failures removed, catalog budget check | Success |
| E14 | `inspect_text` and `edit_text` (formatting-preserving edits); failures return MCP `isError: true` | Success |

The final tool set has 60 tools. The skills' entry points (`get_coreldraw_status`, `inspect_shapes`, `export_page_proof_pdf`) keep their names.

- **Documents:** `list_documents`, `open_document`, `create_document`, `save_document`, `close_document`, `edit_pages`, `add_guideline`
- **Inspection:** `inspect_shapes`, `get_ink_inventory`, `find_shapes`, `inspect_text`, `list_layers`, `get_ui_selection`, `get_print_separation_settings`
- **Proofs:** `export_page_proof_pdf`, `render_plates`, `simulate_overprints`
- **Color:** `set_fill`, `set_outline`, `set_overprint`, `set_transparency`, `replace_color`, `undo`
- **Geometry:**
  - shapes: `create_shape`, `import_file`, `transform_shapes`, `arrange_shapes`, `group_shapes`, `powerclip`, `delete_shapes`, `duplicate_shapes`, `combine_shapes`, `convert_to_curves`
  - effects: `apply_contour`, `separate_effect`, `offset_silhouette`
- **Layers:** `create_layer`, `set_layer_properties`, `move_shapes_to_layer`
- **Text:** `edit_text`
- **Not yet consolidated:**
  - export: 8 tools
  - signage and domain tools: 11 (`check_dimensions`, templates ×3, engineering ×2, data merge ×5)

## What we learned

### CorelDRAW automation facts (2022, 24.4)

- **Batched reads are the main speed lever.** Each dynamic-dispatch property read costs two COM round trips (1–25 ms). `Shape.Evaluate("array(@com.…)")` evaluates many reads in-process. `toarray()` and `Next(…)` sibling chains walk 4,096 shapes in about 0.15 s. CQL can read properties but refuses method calls, so a CQL escape hatch is read-only by construction.
- **Spot colors report `Color.Type` 25**, not the documented 1, so check `IsSpot`. Spot colors are created with `CreateSpotColorByName(palette, name, tint)`; installed spot palettes can be read from their XML files.
- **`Document.Undo` acts on the active document**, whatever document it is called on. Every edit therefore activates its target first, and activation completes asynchronously, so pump messages until it lands. An inactive document has no `ActiveWindow`.
- **`Document.Unit` is session-wide and is not saved in the file.** The old tools that forced millimetres changed units for every later call.
- **`SaveAs` through pywin32 needs `CreateStructSaveAsOptions()`.** `ShapeRange.SetOutlineProperties` needs `InvokeTypes` with all 14 typed arguments. pywin32 silently ignores keyword arguments to `FindShape(s)`.
- **Native `FindShapes` treats Name as an exact filter.** With an empty or missing name it finds only unnamed shapes, and it skips PowerClip contents.
- **`TextRange.Evaluate` has no `@com`**, so query characters through the shape. Text automation cannot write characters outside the Basic Multilingual Plane. Replacing a range gives the new text the formatting of the range's last character.
- **Publish to PDF follows the layer Printable flag, not Visible**, and it exports a hidden printable layer as an optional-content layer that is off by default. Spot plates persist for hidden, off-page or clipped objects, and registration prints on every plate.
- **Ghostscript `tiffsep` needs `-dOverrideICC`.** Without it, K-only art also inks the C, M and Y plates.
- **CorelDRAW quirks:**
  - Moving a shape off a hidden layer silently does nothing.
  - Importing onto a hidden layer throws a server fault.
  - A new group lands at the back of the active layer.
  - An empty group cannot exist.
  - `CreateBlend` fails through pywin32.
  - Stacking positions shift as you edit, so resolve every target before the first change.
- **Crash risk:** `Curve.WeldWith` on display curves crashed CorelDRAW (`VGCore.dll`). Shape-level `Weld` on temporary copies is safe. A single COM call cannot be interrupted from the client; only loops of calls can.

### Design lessons for agent-facing tools

- **Exact targeting beats convenience.** Name lookups and active-document defaults caused most of the wrong-object and wrong-document risks.
- **Server-side aggregation is what makes complex work feasible.** One inventory call replaced hundreds of client calls and megabytes of JSON. The same was true for recolor, artifact search and plate checks.
- **Verify by measurement, not by trusting COM.** The ink inventory and the Ghostscript plate render agree, and the containment check measures chokes in pixels. Every mutating tool reads back its own result.
- **One undo step per edit, with self-undo time limits under the 60 s bridge limit**, makes partial failures recoverable.
- **The catalog size is mostly overhead.** A repeated output schema was 43 % of the catalog. Shared conventions belong in the server instructions.
- **Silent `except: pass` hid real bugs.** Removing it exposed `set_text_style` reporting changes it never made, an overflow check that skipped overflowing shapes, and right alignment applied as left.
- **Process lessons:**
  - An audit first (E01) gave a ranked, measurable target list, and later experiments could quote its numbers.
  - Combining related experiments (color editing, layers with transforms, search with artifacts) saved cycles without hurting quality.
  - Ad-hoc in-process probes on a shared CorelDRAW instance are dangerous. The protocol now has a shared-instance gate: no probes or edits while any non-lab document is open, and no self-relaunch after a crash.

## Incidents

- **CorelDRAW crashed twice during E13** (06:54 in `BaseToolCore.dll`, 07:14 in `VGCore.dll`). The second crash happened while a customer file, Hopewell #53977 on `Z:`, was open. It had not been edited and was not dirty a minute earlier. CorelDRAW was relaunched at 08:13 on the user's go-ahead; there were no further crashes.
- **Leftover documents and processes:** an unsaved fixture `Untitled-1` was found and closed after checking that its structure matched the fixture, and leftover agent background tasks were stopped. `close_mine.py` was deleted because it could close other sessions' documents.

## What's left to explore

Planned but not run, because the program ended after E14:

1. **E09 export consolidation.** Merge 8 export tools into one `export_document` with pages, layers and IDs. Reuse the state restore from `export_page_proof_pdf`.
2. **E16 domain tools.** The signage templates, engineering title block, data merge and barcode/QR tools (11 tools): decide whether each is kept, moved to an opt-in module, or removed. For example, register them only when an environment flag is set.
3. **E21 agent-eye re-audit.** Rerun the E01 task scripts with the new tools (port `e01/` and `e17a/bench.py`, which still call removed tools) and publish a before/after table from one consistent run.
4. **E18 final pass.** README and tool docs, run `test_e2e.py` (updated but never run), and regression on every sample.

Follow-ups that came out of the experiments:

- **Friday Threads skill updates** (outside this repo):
  - Use `render_plates` per page in `separation-check.md`.
  - Build the combined underbase with one `offset_silhouette(exclude_colors=[black])` call, verified with `render_plates(contain=…)`.
  - Use `find_shapes` presets for shop review instead of reading the Objects docker.
  - Move registration marks with `set_layer_properties` plus `transform_shapes`.
- **Scale limits:**
  - `offset_silhouette` handles about 100 outlined objects per call. Batching same-outline, non-overlapping copies would reach the 1,863-speck Good News group.
  - A `replace_color` covering several Good News-sized pages should be split by `pages`.
  - Per-shape transform loops cost about 0.1 s per shape in large groups; batch identical moves through one `ShapeRange`.
- **Accuracy:**
  - `render_plates` object attribution is by bounding box; per-object masks for disputed plates would make it exact.
  - `include_hidden_printable` assumes the print path follows the Printable flag, which has not been verified with a real print job.
  - Confirm print-preview parity with the AccuRIP destination.
- **Capabilities:**
  - cross-page `move_shapes_to_layer`;
  - grouping and layer moves for nested shapes;
  - effect groups as `offset_silhouette` input;
  - blends (pywin32 limitation);
  - `import_file` with formats beyond SVG and PNG;
  - an optional `activate=False` for edits proven Undo-free, so the user's view stays still.
- **Catalog:**
  - Drop FastMCP's `_meta.fastmcp.tags` and its auto-generated `title` (about 4k characters that the model does not see).
  - Report shape types as names everywhere.
  - Lower the catalog budget after E09 and E16.
- **Performance:**
  - `toarray()` iteration is about 1.5× faster than `Next`-chain reads for full records.
  - `resolve_document` costs 0.08–0.19 s with many documents open.
  - Responses of about 150 KB per 250 shapes argue for field-selective paging.
- **Safety:** make the shared-instance gate a server-side guard, for example a refusal mode when non-lab documents are open during test runs, instead of a protocol rule only.

## Validation of the merged branch

The final run on `claude/experiments-integration` used CorelDRAW 2022 with only lab documents open.

- `ruff check server/`: 11 errors (baseline 31).
- `python server.py --check`: passes.
- `catalog_size.py --budget`: within budget (60 tools, 54,070 chars).
- `accept_mcp.py`: **504/504** on a fresh `build_accept.py` fixture through the MCP HTTP server. The first attempt passed 494 checks with no failures, then crashed printing a non-ASCII character to a cp1252-redirected log. Run it with `PYTHONIOENCODING=utf-8` when you redirect output.
- `accept_failure.py`: **19/19**, in-process, with no server.
- All run documents were closed afterwards, the server was stopped, and CorelDRAW stayed up throughout.
