# CorelDRAW tools for screen separation workflows

Production handoff, 2026-11-16. The priorities below are proposed for review. Keep printer procedures, press checks, and shop choices in the production workflow skill. The MCP tools should accept explicit inputs and report observed CorelDRAW state.

## Confirmed gaps and proposed order

| Priority | Capability | Expected behavior |
| --- | --- | --- |
| 1 | Recursive shape inspection | Read the requested document/page, including objects nested inside groups. Return exact shape IDs, page, layer, parent, bounds and units, stacking order, effects, fill and outline color identity (spot, process, registration, or unknown), outline width, and fill/outline overprint flags. Include zero-size shapes and zero-width outlines. Report inaccessible properties so a partial result cannot appear complete. Review `get_color_report` and the existing RGB checks first. The current report scans active-page uniform fills and uses incorrect `cdrColorType` values. Share color decoding with those checks. |
| 1 | Exact UI selection and readback | Read the live selection and select by exact shape IDs in explicit document/page scope. Verify all requested IDs before changing selection and return the actual selection afterward. Preserve the existing `select_shapes` query behavior for its callers. |
| 1 | Page-scoped layer controls | Read and set visibility, editability (lock/unlock), and print/export participation independently. Match the exact document, page, and layer, distinguish page layers from shared layers, and read back the actual flags. Verify which CorelDRAW property backs Print and Export on the installed release. A hidden layer can still be printable. |
| 2 | Independent overprint controls | Read and set fill and outline overprint by exact shape ID. Return both flags after a write. Verify that setting fill overprint on a new contour result preserves the original object's flags and the result's outline flag. |
| 2 | Contour operations | List available presets, apply a named preset or an explicit inward/outward contour with offset, units, and step count, inspect the effect, and break it apart. Return source and result shape IDs so follow-up operations target the new shapes. Report preset discovery or application as unsupported if the installed CorelDRAW release cannot expose it, and verify the explicit parameter path independently. |

The production workflow motivating the contour operations uses the White Underbase preset, an inward 0.75 pt offset, and overprint fill on the resulting white shapes after breaking apart. Preset, ink, and offset come from the workflow. Treating beige as white, choosing mesh, and setting registration-mark spacing also belong in the skill.

### CorelDRAW 2022 verification

- A disposable two-page document confirmed recursive inspection of a grouped process-color outline, selection by child ID, and independent printability for same-named layers on different pages.
- A disposable contour test confirmed a 0.75 pt inward contour, copying an existing object's uniform fill, separation with returned leaf IDs, and fill overprint on the result with the source and outline flags preserved.
- Inspection of the installed COM type library found contour effect interfaces and parameters but no native preset list or apply member. Use CorelDRAW's preset UI when the named preset is needed. MCP accepts explicit contour parameters and can use an existing object's uniform fill.

## Candidates to investigate

- **Geometry anchors and units.** `set_shape_position` documents millimeters and calls `SetPosition` directly. Verify anchor, coordinate system, unit conversion, and readback on pages with different document units and reference points.
- **New document from a CDT.** CorelDRAW 2022 exposes `Application.CreateDocumentFromTemplate`. Verify with a disposable CDT that it creates a separate unsaved drawing with graphics and the intended page structure, and that saving a new CDR leaves the source CDT intact. `open_template` currently calls `OpenDocument`. On a new CDT-based drawing, dynamic COM rejected `Doc.SaveAs(path)` with a conversion error. Passing `App.CreateStructSaveAsOptions()` as the second argument succeeded. Include that case when validating save behavior.
- **Grouped edits.** Evaluate one undo group with exact target and result IDs and observed state after each operation. Test rollback after a partial failure separately from grouping edits for undo.
- **Per-page plates and previews.** `get_print_separation_settings` reads the configured printer, range, and available plates, which can include unused inks. The installed CorelDRAW 2022 COM type library exposes no rendered print-preview image or per-page nonblank plate list. `export_page_proof_pdf` publishes one page with native spots and overprint instructions for independent verification. `server/render_separation_proof.py` runs Ghostscript `tiffsep` to produce a composite, each plate mask, and a manifest. PDF proof does not validate printer clipping, RIP settings, or physical output. Inspect any registration-only marks reported on other plates before deciding whether an ink is unexpected.
- **Exact transfer between documents.** CorelDRAW rejected cross-document `Shape.CopyToLayer` with “Specified object is from another document.” Native copy and paste preserved curve geometry and spots in the reported case. `Layer.Paste()` did not return a `ShapeRange`, so new IDs were confirmed from the destination collection. Investigate a temporary native CDR import path that avoids clipboard state. Verify fills, outlines, groups, z-order, and overprint flags and return the resulting destination IDs.
- **Native file output from the print engine.** CorelDRAW documents `PrintSettings.PrintToFile`, `FileName`, and `FileMode` with `PrintOut`. Investigate on a disposable drawing in an isolated CorelDRAW session without physical or RIP destinations. Select and verify a file-only PostScript destination, verify the output path, print-to-file flag, exact page range, paper and layout, and separation settings before invoking `PrintOut` once. Confirm the resulting files and compare them with the fixture's expected plates and layout. Whether this path reproduces the shop's actual RIP output remains open. The production print path and physical queues were excluded from this test plan.

## Production follow-up, 2026-11-17

| Capability request | Observation and acceptance criteria | Status, 2026-11-17 fixture validation |
| --- | --- | --- |
| Inspect and edit text fill runs | Shape-level fill copying did not replace mixed character fills. Two spaces retained legacy spot inks in the PDF without producing distinct raster marks. List exact text ranges and native fill identities, including whitespace. Accept an explicit document, page, shape ID, and character range. Copy the donor fill into that range and verify the text, adjacent runs, and resulting ink identities. | `inspect_text_fill_runs` and `copy_fill_to_text_range`. See [Text fill runs](#text-fill-runs). |
| Weld consecutive shapes while preserving stacking order | The tested `ShapeRange` exposed Group and Combine. Grouping two curves and welding the group to a third worked on a disposable drawing. Require an explicit list of consecutive shapes on one layer and validate native fill and tint before editing. Verify coverage, relative stacking order against adjacent objects, and undo on a fixture before production use. | `weld_consecutive_shapes`. See [Consecutive weld](#consecutive-weld). |
| Produce a proof for one named layer | An isolated proof exposed overprint coverage and overlap that were harder to judge from the full drawing. Accept exact document, page, and layer scope. Verify the proof excludes artwork on other layers and preserves native fills and overprint flags. Preserve and verify the original document and PDF settings after success or failure. | `export_layer_proof_pdf`. See [Single-layer proof](#single-layer-proof). |
| Reduce round trips for large inspections | A complete paged inventory took about 107 seconds for a flat 4,452-object layer, even after adding direct offsets. Profile COM round trips and test narrower or batched reads against the existing inventory. Compare the full set of exact IDs and color counts, including unknown color types. | `inspect_shapes` reads through batched CorelDRAW queries. Exact-ID lookups use `FindShape`. See [Inspection round trips](#inspection-round-trips). |

The live per-page, per-plate print preview still uses CorelDRAW's UI. An isolated layer proof and a list of configured plates answer separate questions from that preview.

### Fixture validation, 2026-11-17

All checks ran on CorelDRAW 2022 (24.4.0.636) through the local MCP HTTP server. Disposable fixtures were built by script, and no production document was opened or changed. The acceptance fixture has text with whitespace in a legacy spot ink, a weld stack between foreign neighbors, overprinting and hidden layers, and a same-named layer on page 2. All 33 MCP checks and 3 in-process failure-path checks passed.

#### Text fill runs

- **Finding.** `Story.Range(start, end)` is zero-based and end-exclusive. Each `Characters.Item(i)` exposes its own `Fill`. On mixed text, the shape-level `Fill` reports one color, here the first run's spot. The story-level fill reads as type 0, which cannot be distinguished from no fill. Per-character reads are the only reliable inventory.
- **Accepted.** `inspect_text_fill_runs` lists `[start, end)` runs with text, a `whitespace_only` flag, and the native fill: spot name and tint, process components, registration, none, or unknown. Characters are read through batched `TextRange.Evaluate` queries, with a direct-read fallback. The tool rejects text longer than 5,000 characters.
- **Accepted.** `copy_fill_to_text_range` copies a donor's uniform fill into an explicit range as one undo step. It rejects ranges that extend past the text or contain non-uniform fills. It verifies the text, every character in the range, and every character outside it. On the fixture, the two `PANTONE 300 C` spaces became `PANTONE 186 C` at 100% tint, and one Undo restored the original three runs.
- **Open.** A verification failure is reported, but the edit is not undone automatically. Test paragraph text, multi-line stories, and characters outside the Basic Multilingual Plane before relying on run positions in those cases.

#### Consecutive weld

- **Finding.** Successive `source.Weld(result, False, False)` calls keep the topmost target's name and stacking position. Neighbors stay adjacent without the Group-then-Weld workaround. One `BeginCommandGroup` makes the whole weld a single undo step, and `Undo(1)` restores the original StaticIDs and order. `Shape.DisplayCurve.Area` is available for rectangles as well as curves.
- **Accepted.** `weld_consecutive_shapes` requires that every shape be a direct child of one editable page layer, adjacent in stacking order, without effects, and with an identical uniform fill (`IsSame`, spot name and tint), outline, and overprint flags. It rejected non-consecutive shapes, a 60% tint, a different spot, and shapes on another layer. On success it returns the result ID, the neighbors above and below, bounds, the result area, and the sum and maximum of the source areas. It also verifies the paint, the stacking position, and that the bounds equal the union of the sources.
- **Coverage.** The fixture result area was 1,000 mm², matching three 20 mm squares minus two 5 × 20 mm overlaps. A separate trim probe left zero area for each source square and 25 mm² for a probe outside the result.
- **Rollback.** A failure after a partial weld leaves some source IDs missing. In that case the tool calls `Undo(1)` and checks that every source ID returned.

#### Single-layer proof

- **Finding.** Publish to PDF follows layer **Printable**, not **Visible**. A hidden but printable layer is published, and changing Printable sets `Document.Dirty`. Each PDF names only the separations it actually uses, so the spot list identifies which layers were included.
- **Accepted.** `export_layer_proof_pdf` takes an exact document, page, and page-local layer, and rejects master or special layers. Every other layer, including shared master layers, is non-printing only while publishing. Afterward the tool restores and verifies each layer's printability, the PDF settings, the active page, the document unit, and the dirty flag.
- **Fixture results.** The independent PDF stream scan found only `PANTONE 300 C` with fill overprint for **Ink B**, and only `PANTONE 354 C` for hidden **Hidden C**. The **Weld** layer showed only its two inks, and page 2 **Ink B** showed only `PANTONE 485 C`. The document, PDF, and layer state was identical after every publish, after each refused request (existing output, special layer), and after a simulated publish failure.
- **Open.** Ghostscript was not installed on this workstation, so no raster plates were checked. Render with `server/render_separation_proof.py` before judging overlap coverage.

#### Inspection round trips

- **Profile.** Every dynamic-dispatch property read costs two COM round trips: `GetIDsOfNames` plus `Invoke`. On a 4,452-object layer, direct reads cost about 116–250 ms per shape. Fill and outline color objects each took about 25 ms, `Name` 15 ms, and bounds 21 ms. A direct full inventory of the fixture took 1,152 s in 100-shape chunks. A single call cannot finish it, because the bridge cancels any operation after 60 s.
- **Change.** `Shape.Evaluate` runs CorelDRAW query expressions in-process. `array(...)` returns many values in one call, `iif` short-circuits, and `@com.*` reads use document units. `inspect_shapes` now reads 50 shapes per query through paths relative to an anchor shape on each layer, such as `@com.Layer.Shapes.Item(i).Shapes.Item(j)`. If a query fails, it retries per shape and then falls back to direct reads that report each unreadable property.
- **Parity.** On the 4,452-object flat layer, the batched reader returned the same records as the direct reader: identical IDs in the same order, 4,452 unique IDs, identical fill and outline color counts, and no unreadable fields. The paged output also matched. A mixed fixture matched the direct reader with nested groups, a zero-width line, registration, tinted spots, RGB, a fountain fill, a contour effect, and a contour group whose outline is unreadable.
- **Timings.**

  | Read | Time |
  | --- | --- |
  | Full inventory, one call, in process | 15.6 s |
  | Full inventory, one call, through MCP | 19.2 s |
  | Paged in 250-shape calls | 26.8 s |
  | Fill and outline fields only | 11.3 s |

- **Exact IDs.** Exact-ID tools previously read every object's StaticID over COM. Reaching the last of 4,452 shapes now takes 1.3 s through MCP. `Shapes.FindShape` cannot search by ID alone: its Name argument is a filter, so `""` matches only unnamed shapes.
- **Color identity.** Spot color identity now includes `tint` wherever shapes, contours, and text runs report colors.
- **Other findings.** `Document.SaveAs(path)` fails on documents created by `CreateDocument`, in addition to the CDT case above. Pass `Application.CreateStructSaveAsOptions()`. `save_document` still calls the single-argument form. `get_color_report` misses CorelDRAW 2022 spot colors (type 25) and still scans only top-level shapes on the active page.


## Validation

Use a disposable CorelDRAW 2022 fixture or a copy of a known multi-location document. Exclude live production artwork from mutation testing. Start with a grouped shape that has process color only on its outline, two pages with identically named layers and different printability, and a spot-color contour. Verify inspection finds the exact object, selecting IDs matches the actual UI selection, a layer change affects only its intended page, and break-apart and overprint target the resulting shapes. Compare any plate results against a dry-run CorelDRAW preview with the shop RIP selected.

The disposable PDF fixture confirmed that publication leaves the active page, document unit, dirty flag, PDF settings, and layer flags unchanged. Ghostscript returned only the two expected inked spot plates. Both retained ink at the overlap, and the nonprinting layer and different page were excluded. The legacy PNG export was also checked to preserve hidden/nonprinting layer flags after removing its forced visibility and printability writes.
