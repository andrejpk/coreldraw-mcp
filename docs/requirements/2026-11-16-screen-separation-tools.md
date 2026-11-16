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
- **New document from a CDT.** Verify the CorelDRAW template creation path. `open_template` calls `OpenDocument`; the desired outcome is a new document based on the CDT with the original template preserved.
- **Grouped edits.** Evaluate one undo group with exact target and result IDs and observed state after each operation. Test rollback after a partial failure separately from grouping edits for undo.
- **Per-page plates and previews.** Check which separation settings and plates CorelDRAW exposes without printing and whether those values reconcile with the live preview for each page/color. Keep artwork color inventory distinct from the print engine's output.

## Validation

Use a disposable CorelDRAW 2022 fixture or a copy of a known multi-location document. Exclude live production artwork from mutation testing. Start with a grouped shape that has process color only on its outline, two pages with identically named layers and different printability, and a spot-color contour. Verify inspection finds the exact object, selecting IDs matches the actual UI selection, a layer change affects only its intended page, and break-apart and overprint target the resulting shapes. Compare any plate results against a dry-run CorelDRAW preview with the shop RIP selected.
