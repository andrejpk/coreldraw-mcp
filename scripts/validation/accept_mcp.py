r"""Acceptance tests through the running MCP server: E08 document management, then the 2026-11-17 production follow-up.

Usage (from server/): python accept_mcp.py <dir> '<ids json from build_accept.py>'
<dir>\fx must hold accept.cdr (build_accept.py). The document section copies C:\CorelMcpLab\templates\colors.cdr
(inch unit) into <dir>\fx and leaves another of its documents active, so the production checks run against an
accept.cdr that is not the active document. Direct COM checks (undo, coverage, PDF settings) use this process's
own bridge between MCP calls and resolve documents by exact path.
"""
import json, ntpath, os, shutil, sys
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, sys.argv[1]); sys.path.insert(0, str(Path(__file__).resolve().parent))
from mc import call
from pdfscan import scan
from render_separation_proof import render
from core.connection import get_connection
from core.targets import activate_document, document_identity, find_documents, normalize_path
GS = next(iter(sorted(Path("C:/Program Files/gs").glob("*/bin/gswin64c.exe"))), None) if Path("C:/Program Files/gs").is_dir() else None
TEMPLATES = Path(r"C:\CorelMcpLab\templates")


def inked_plates(pdf):
    """Ghostscript tiffsep: names of plates with any visible ink."""
    out = Path(pdf).with_suffix(".plates")
    shutil.rmtree(out, ignore_errors=True)
    return sorted(p["name"] for p in render(Path(pdf), out, GS, 72, 1)["plates"] if not p["blank"])


FX = sys.argv[1].replace("/", "\\") + r"\fx"
DOC = FX + r"\accept.cdr"
IDS = json.loads(sys.argv[2])
conn = get_connection()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


def direct(func):
    r = conn.safe_call(func)
    assert r["success"], r
    return r["result"]


def doc_of(path):
    """The open document at exactly this path (this script's own fixtures only)."""
    docs = find_documents(conn.app, path)
    assert len(docs) == 1 and normalize_path(path).startswith(normalize_path(FX)), (path, len(docs))
    return docs[0]


def active_path():
    return direct(lambda: document_identity(conn.app.ActiveDocument) if conn.app.ActiveDocument else None)


def undo(path):
    """Undo one step in the fixture. CorelDRAW applies Document.Undo to the active document, so activate it first."""
    def run():
        doc = doc_of(path)
        activate_document(conn.app, doc)
        doc.Undo(1)
    direct(run)


def com_unit(path):
    return direct(lambda: doc_of(path).Unit)


def entry(listing, path):
    return next((d for d in listing["data"]["documents"] if normalize_path(d["document_path"]) == normalize_path(path)), None)


# 0. Document management and exact-document targeting (E08) ------------------------------------
A, B = FX + r"\e08_a.cdr", FX + r"\e08_b.cdr"
SAVED, EXISTING, NEW = FX + r"\e08_a_saved.cdr", FX + r"\e08_existing.cdr", FX + r"\e08_new.cdr"
for leftover in (SAVED, EXISTING, NEW, FX + r"\e08_untitled.cdr", FX + r"\e08_unit.png"):
    Path(leftover).unlink(missing_ok=True)
shutil.copyfile(TEMPLATES / "colors.cdr", A)
shutil.copyfile(TEMPLATES / "colors.cdr", B)
shutil.copyfile(TEMPLATES / "colors.cdr", EXISTING)

# Document.Unit is a session setting (not saved in the file); build_accept.py left accept.cdr open in mm.
r = call("open_document", path=DOC, activate=False)
check("open: already-open accept.cdr is returned as is, its session unit (mm) untouched",
      r["success"] and r["data"]["already_open"] and r["data"]["unit"] == "mm" and com_unit(DOC) == 3,
      r.get("error") or r["data"]["unit"])
r = call("open_document", path=A)
check("open: inch template keeps unit 'in' (no forced mm)", r["success"] and r["data"]["unit"] == "in" and com_unit(A) == 1,
      r.get("error") or r["data"]["unit"])
check("open: activate=True makes it active", r["success"] and r["data"]["active"] and active_path() == A)
count = direct(lambda: conn.app.Documents.Count)
r = call("open_document", path=A.upper().replace("\\", "/"))
check("open: already-open path (other case, slashes) returns the existing document",
      r["success"] and r["data"]["already_open"] and direct(lambda: conn.app.Documents.Count) == count, r.get("error"))
r = call("open_document", path=B, activate=False)
check("open: activate=False keeps the previously active document", r["success"] and not r["data"]["active"]
      and active_path() == A, (r.get("error"), active_path()))
r = call("open_document", path=FX + r"\missing.cdr")
check("open: missing file refused", not r["success"], r.get("error"))
listing = call("list_documents")
a_entry = entry(listing, A)
check("list: entry has path, unit name, pages with sizes, one active document",
      listing["success"] and a_entry and a_entry["unit"] == "in" and a_entry["pages"][0]["width"] == 8.5
      and sum(d["active"] for d in listing["data"]["documents"]) == 1 and listing["data"]["active_document_path"] == A,
      a_entry)
detail = call("list_documents", detail=True)
check("list: detail adds layers with printable flags",
      detail["success"] and "printable" in entry(detail, A)["pages"][0]["layers"][0], detail.get("error"))

# Mismatched-document refusal for active-document tools.
b_shapes = direct(lambda: doc_of(B).Pages(1).Shapes.Count)
r = call("create_rectangle", document_path=B, x=1, y=1, width=1, height=1)
check("mismatch: legacy edit on a non-active document refused", not r["success"] and "mismatch" in r["error"]
      and direct(lambda: doc_of(B).Pages(1).Shapes.Count) == b_shapes, r.get("error"))
r = call("set_fill_cmyk", c=0, m=0, y=0, k=100, shape_id="1")
check("mismatch: legacy edit without document_path refused", not (isinstance(r, dict) and r.get("success")), r)
r = call("get_layers", document_path=B)
check("mismatch: legacy read naming a non-active document refused", not r["success"] and "mismatch" in r["error"])
r = call("get_layers")
check("legacy read defaults to the active document and names it", r["success"] and r["data"]["document_path"] == A)
r = call("create_rectangle", document_path=A, x=1, y=1, width=0.5, height=0.5)
check("legacy edit on the active document works, reports document and unit",
      r["success"] and r["data"]["document_path"] == A and r["data"]["document_unit"] == "in", r.get("error"))
rect = r["data"]["shape_id"] if r["success"] else None

# Tools that used to force Unit = mm must leave the unit alone.
for name, args in (
    ("add_guideline", {"document_path": A, "position": 25.4}),
    ("create_text_frame", {"document_path": A, "x": 25.4, "y": 25.4, "width": 50.8, "height": 12.7, "text": "unit"}),
    ("check_dimensions", {"document_path": A, "expected_width": 215.9, "expected_height": 279.4}),
    ("align_shapes", {"document_path": A, "shape_ids": str(rect), "alignment": "left", "reference": "page"}),
    ("export_png", {"document_path": A, "path": FX + r"\e08_unit.png", "dpi": 72}),
    ("list_all_text_shapes", {"document_path": A, "page_index": 1}),
):
    r = call(name, **args)
    check(f"unit: {name} succeeds and leaves the document in inches", r["success"] and com_unit(A) == 1,
          r.get("error") or {k: r["data"].get(k) for k in ("document_unit", "passed", "actual") if k in r["data"]})
    if name == "check_dimensions":
        check("unit: check_dimensions converts the inch page to mm explicitly", r["success"] and r["data"]["passed"],
              r["data"].get("actual") if r["success"] else None)
    if name == "create_text_frame" and r["success"]:
        box = direct(lambda: (lambda s: (s.PositionX, s.SizeWidth))(
            [x for x in doc_of(A).Pages(1).Shapes if str(x.StaticID) == r["data"]["shape_id"]][0]))
        check("unit: create_text_frame mm arguments land at the right inch position",
              abs(box[0] - 1) < 1e-3 and abs(box[1] - 2) < 1e-3, box)

# Pages
r = call("edit_pages", document_path=B, action="add", count=2)
check("pages: add two pages to an exact document (activates it, keeps its active page)", r["success"]
      and r["data"]["page_count"] == 3 and r["data"]["active_page"] == 1 and active_path() == B, r.get("error"))
r = call("edit_pages", document_path=B, action="resize", page_index=2, width=100, height=50, unit="mm")
check("pages: resize page 2 in mm, document stays in inches",
      r["success"] and abs(r["data"]["pages"][1]["width"] - 100 / 25.4) < 1e-4 and r["data"]["document_unit"] == "in"
      and com_unit(B) == 1, r.get("error"))
r = call("edit_pages", document_path=B, action="activate", page_index=3)
check("pages: activate page 3", r["success"] and r["data"]["active_page"] == 3)
r = call("edit_pages", document_path=B, action="delete", page_index=2)
check("pages: delete page 2", r["success"] and r["data"]["page_count"] == 2)
undo(B)
check("pages: delete is one undo step", direct(lambda: doc_of(B).Pages.Count) == 3)
r = call("edit_pages", document_path=B, action="delete", page_index=9)
check("pages: out-of-range page refused", not r["success"], r.get("error"))

# Save
r = call("save_document", document_path=A, save_as_path=SAVED)
check("save-as: new path written, document now refers to it, clean",
      r["success"] and Path(SAVED).stat().st_size > 0 and r["data"]["document_path"] == SAVED
      and direct(lambda: doc_of(SAVED).Dirty) is False and not direct(lambda: find_documents(conn.app, A)),
      r.get("error"))
check("save-as: unit unchanged", com_unit(SAVED) == 1)
mtime = os.stat(EXISTING).st_mtime_ns
r = call("save_document", document_path=B, save_as_path=EXISTING)
check("save-as: existing file refused without overwrite", not r["success"] and "overwrite" in r["error"]
      and os.stat(EXISTING).st_mtime_ns == mtime, r.get("error"))
r = call("save_document", document_path=B, save_as_path=SAVED, overwrite=True)
check("save-as: a path open in another document is refused even with overwrite", not r["success"], r.get("error"))
r = call("save_document", document_path=B, save_as_path=EXISTING, overwrite=True)
check("save-as: overwrite=True replaces the file", r["success"] and os.stat(EXISTING).st_mtime_ns != mtime
      and r["data"]["overwrote"], r.get("error"))
B = EXISTING
r = call("save_document", document_path=B, save_as_path=FX + r"\no_such_folder\x.cdr")
check("save-as: missing folder refused", not r["success"], r.get("error"))
r = call("save_document", document_path=SAVED)
check("save: in place", r["success"] and r["data"]["saved_path"] == SAVED, r.get("error"))

# Close
r = call("create_rectangle", document_path=B, x=2, y=2, width=0.5, height=0.5)
check("setup: make the active document dirty", r["success"] and direct(lambda: doc_of(B).Dirty), r.get("error"))
r = call("close_document", document_path=B)
check("close: dirty document refused", not r["success"] and direct(lambda: bool(find_documents(conn.app, B))),
      r.get("error"))
r = call("close_document", document_path=B, discard_changes=True)
check("close: discard_changes closes only that document", r["success"] and not direct(lambda: find_documents(conn.app, B))
      and direct(lambda: bool(find_documents(conn.app, SAVED))) and direct(lambda: bool(find_documents(conn.app, DOC))),
      r.get("error"))
r = call("close_document", document_path=B)
check("close: a path that is not open is refused", not r["success"], r.get("error"))

# New documents
r = call("create_document", width=4, height=3, unit="in", save_as_path=NEW)
check("create: saved new document reports its path and unit", r["success"] and r["data"]["document_path"] == NEW
      and r["data"]["unit"] == "in" and Path(NEW).is_file(), r.get("error"))
call("close_document", document_path=NEW)
r = call("create_document", width=100, height=50, unit="mm")
title = r["data"]["document_path"] if r["success"] else ""
check("create: unsaved document is addressed by its title", r["success"] and not ntpath.isabs(title)
      and not r["data"]["saved"], title)
r = call("save_document", document_path=title, save_as_path=FX + r"\e08_untitled.cdr")
check("save-as: unsaved document gets a path", r["success"] and r["data"]["document_path"] == FX + r"\e08_untitled.cdr",
      r.get("error"))
call("close_document", document_path=FX + r"\e08_untitled.cdr")

# Leave another document active: accept.cdr is now inactive for the production checks.
call("open_document", path=SAVED)
check("setup: accept.cdr is open but not active", active_path() == SAVED)

fmt = lambda runs: [(r["start"], r["end"], r["text"], r["fill"].get("color", {}).get("name")) for r in runs]  # noqa: E731

# 1. Text fill runs -------------------------------------------------------------------------------
r = call("inspect_text_fill_runs", document_path=DOC, page_index=1, shape_id=IDS["text"])
check("text runs: inspect succeeds", r["success"], r.get("error") or f"{r['_seconds']}s")
check("targets: a read on an inactive document does not activate it", active_path() == SAVED)
runs = r["data"]["runs"]
check("text runs: whitespace run with legacy spot is listed",
      fmt(runs) == [(0, 2, "AB", "PANTONE 186 C"), (2, 4, "  ", "PANTONE 300 C"), (4, 6, "CD", "PANTONE 186 C")]
      and runs[1]["whitespace_only"], fmt(runs))
shape_level = call("inspect_shapes", document_path=DOC, page_index=1, layer_name="Text", fields=["fill"])
check("inspect_shapes: document_unit is a name", shape_level["data"]["document_unit"] == "mm")
label = [s for s in shape_level["data"]["shapes"] if s["shape_id"] == IDS["text"]][0]
check("text runs: shape-level fill hides the mixed run (motivating finding)",
      label["fill"].get("color", {}).get("name") == "PANTONE 186 C", label["fill"])
bad = call("copy_fill_to_text_range", document_path=DOC, page_index=1, shape_id=IDS["text"], start=2, end=9,
           source_shape_id=IDS["donor"])
check("text copy: out-of-range end is rejected", not bad["success"], bad.get("error"))
r = call("copy_fill_to_text_range", document_path=DOC, page_index=1, shape_id=IDS["text"], start=2, end=4,
         source_shape_id=IDS["donor"])
check("text copy: donor fill applied to whitespace range", r["success"]
      and fmt(r["data"]["runs"]) == [(0, 6, "AB  CD", "PANTONE 186 C")], r.get("error") or fmt(r["data"]["runs"]))
check("targets: an edit activates its document (so undo stays in it)", active_path() == DOC)
check("text copy: donor color includes tint", r["success"] and r["data"]["source_fill"]["color"].get("tint") == 100)
again = call("inspect_text_fill_runs", document_path=DOC, page_index=1, shape_id=IDS["text"])
check("text copy: independent readback agrees", fmt(again["data"]["runs"]) == [(0, 6, "AB  CD", "PANTONE 186 C")])
undo(DOC)
undone = call("inspect_text_fill_runs", document_path=DOC, page_index=1, shape_id=IDS["text"])
check("text copy: one undo restores the original runs", fmt(undone["data"]["runs"]) == fmt(runs), fmt(undone["data"]["runs"]))

# 2. Weld consecutive shapes ----------------------------------------------------------------------
W = lambda *names: [IDS[n] for n in names]  # noqa: E731
r = call("weld_consecutive_shapes", document_path=DOC, page_index=1, shape_ids=W("c1", "c3"))
check("weld: non-consecutive shapes rejected", not r["success"] and "consecutive" in r["error"], r.get("error"))
r = call("weld_consecutive_shapes", document_path=DOC, page_index=1, shape_ids=W("c3", "tint60"))
check("weld: different tint rejected", not r["success"] and "tint" in r["error"], r.get("error"))
r = call("weld_consecutive_shapes", document_path=DOC, page_index=1, shape_ids=W("top", "c1"))
check("weld: different spot rejected", not r["success"], r.get("error"))
r = call("weld_consecutive_shapes", document_path=DOC, page_index=1, shape_ids=W("c1", "b"))
check("weld: shapes on different layers rejected", not r["success"], r.get("error"))
before = call("inspect_shapes", document_path=DOC, page_index=1, layer_name="Weld", fields=["z_order", "fill"])
order_before = [s["shape_id"] for s in before["data"]["shapes"]]
r = call("weld_consecutive_shapes", document_path=DOC, page_index=1, shape_ids=W("c2", "c3", "c1"))
check("weld: consecutive run welds", r["success"], r.get("error") or {k: r["data"][k] for k in ("result_shape_id", "stack_index", "area")})
data = r["data"]
check("weld: neighbors unchanged", data["above_shape_id"] == IDS["top"] and data["below_shape_id"] == IDS["tint60"],
      (data["above_shape_id"], data["below_shape_id"]))
after = call("inspect_shapes", document_path=DOC, page_index=1, layer_name="Weld", fields=["z_order", "fill"])
order_after = [s["shape_id"] for s in after["data"]["shapes"]]
expected_order = [IDS["top"], data["result_shape_id"], IDS["tint60"], IDS["bottom"]]
check("weld: layer stacking is top, result, tint60, bottom", order_after == expected_order, order_after)
res = [s for s in after["data"]["shapes"] if s["shape_id"] == data["result_shape_id"]][0]
check("weld: result keeps spot and tint", res["fill"]["color"] == {"kind": "spot", "type": 25, "name": "PANTONE 186 C", "tint": 100}, res["fill"])
check("weld: area within source bounds (3x400 minus two 5x20 overlaps = 1000)",
      abs(data["area"] - 1000) < 1e-3, (data["area"], data["source_area_sum"]))


def coverage():
    """Every original source rectangle must lie inside the result: trim a stand-in by the result."""
    doc = doc_of(DOC)
    activate_document(conn.app, doc)
    assert doc.Unit == 3
    layer = [l for l in doc.Pages(1).Layers if l.Name == "Weld"][0]
    result = [x for x in layer.Shapes if str(x.StaticID) == data["result_shape_id"]][0]
    leftovers = []
    doc.BeginCommandGroup("coverage probe")
    try:
        for x in (10, 25, 40):
            probe = layer.CreateRectangle2(x, 100, 20, 20)
            rest = result.Trim(probe, True, False)
            leftovers.append(0 if rest is None else rest.DisplayCurve.Area)
            if rest is not None:
                rest.Delete()
        outside = layer.CreateRectangle2(10, 125, 5, 5)
        rest = result.Trim(outside, True, False)
        leftovers.append(rest.DisplayCurve.Area if rest is not None else 0)
        rest is not None and rest.Delete()
    finally:
        doc.EndCommandGroup()
    doc.Undo(1)
    return leftovers


left = direct(coverage)
check("weld: each source fully covered by the result", all(a < 1e-6 for a in left[:3]), left)
check("weld: coverage probe detects area outside the result", left[3] > 20, left)
undo(DOC)
undone = call("inspect_shapes", document_path=DOC, page_index=1, layer_name="Weld", fields=["z_order"])
check("weld: one undo restores original IDs and order", [s["shape_id"] for s in undone["data"]["shapes"]] == order_before)

# 3. Layer proof ----------------------------------------------------------------------------------
def pdf_state():
    doc = doc_of(DOC)
    s = doc.PDFSettings
    return {
        "pdf": {n: getattr(s, n) for n in ("PublishRange", "PageRange", "ColorMode", "OutputSpotColorsAs", "Overprints",
                                           "CropMarks", "RegistrationMarks")},
        "dirty": doc.Dirty, "page": doc.ActivePage.Index, "unit": doc.Unit,
        "layers": [(p, l.Name, bool(l.Printable), bool(l.Visible)) for p in (1, 2) for l in doc.Pages(p).AllLayers],
    }


r = call("save_document", document_path=DOC)
check("save: accept.cdr in place before the proofs", r["success"], r.get("error"))
state0 = direct(pdf_state)
for name, spots, overprint in (("Ink B", ["PANTONE 300 C"], True), ("Hidden C", ["PANTONE 354 C"], False),
                               ("Weld", ["PANTONE 186 C", "PANTONE 300 C"], False)):
    out = FX + f"\\proof-{name.replace(' ', '_')}.pdf"
    Path(out).unlink(missing_ok=True)
    r = call("export_layer_proof_pdf", document_path=DOC, page_index=1, layer_name=name, output_path=out)
    found = scan(out) if r["success"] else {}
    check(f"layer proof {name!r}: only its inks", r["success"] and found["spots"] == spots,
          r.get("error") or (found, r["data"]["excluded_printable_layers"], f"{r['_seconds']}s"))
    if GS:
        check(f"layer proof {name!r}: Ghostscript inks only its plates", r["success"] and inked_plates(out) == spots,
              inked_plates(out) if r["success"] else None)
    check(f"layer proof {name!r}: fill overprint preserved={overprint}", r["success"] and (found["overprint_fill_true"] > 0) == overprint)
    check(f"layer proof {name!r}: document and PDF settings unchanged", direct(pdf_state) == state0)
r = call("export_layer_proof_pdf", document_path=DOC, page_index=2, layer_name="Ink B", output_path=FX + r"\proof-p2.pdf")
found = scan(FX + r"\proof-p2.pdf") if r["success"] else {}
check("layer proof: same-named layer on page 2 publishes only page 2 ink", found.get("spots") == ["PANTONE 485 C"], found)
if GS:
    check("layer proof: page 2 Ghostscript plates", inked_plates(FX + r"\proof-p2.pdf") == ["PANTONE 485 C"])
r = call("export_layer_proof_pdf", document_path=DOC, page_index=1, layer_name="Guides", output_path=FX + r"\x.pdf")
check("layer proof: master/special layer rejected", not r["success"], r.get("error"))
r = call("export_layer_proof_pdf", document_path=DOC, page_index=1, layer_name="Ink B", output_path=FX + r"\proof-Ink_B.pdf")
check("layer proof: existing output refused", not r["success"], r.get("error"))
check("layer proof: state unchanged after refusals", direct(pdf_state) == state0)

r = call("close_document", document_path=SAVED, discard_changes=True)
check("cleanup: close the E08 document by path", r["success"], r.get("error"))

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
