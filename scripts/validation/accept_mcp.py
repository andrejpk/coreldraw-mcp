r"""Acceptance tests through the running MCP server: E08 document management, the 2026-11-17 production
follow-up, the E17a structure cache and the E03 ink inventory.

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

# 4. Structure cache and bridge status (E17a) ------------------------------------------------------
# The server caches each page's structure between calls. Edits made here through this process's own COM
# bridge bypass the server (like edits in the CorelDRAW UI); edits through MCP tools invalidate the cache.
import threading
import time
from core import structure as structure_cache
from core.drawing import walk_shapes
from tools import inspection
ST, BIG = FX + r"\e17a_structure.cdr", FX + r"\e17a_big_flat.cdr"
for source, target in (("structure.cdr", ST), ("big_flat.cdr", BIG)):
    shutil.copyfile(TEMPLATES / source, target)
    r = call("open_document", path=target, activate=False)
    check(f"cache setup: open {Path(target).name}", r["success"], r.get("error"))


def mcp_inventory(path, limit=5):
    shapes, offset = [], 0
    while offset is not None:
        r = call("inspect_shapes", document_path=path, page_index=1, offset=offset, limit=limit)
        assert r["success"], r
        shapes += r["data"]["shapes"]
        offset = r["data"]["next_offset"]
    return shapes


def truth(path):
    """A fresh walk in this process (its own empty cache), as the ground truth."""
    structure_cache.invalidate()
    r = inspection.inspect_shapes(path, 1)
    assert r.success, r.error
    return r.data["shapes"]


def edit(func):
    """An out-of-band edit of the structure fixture: returns func(doc, layers by name, shapes by name)."""
    def run():
        doc = doc_of(ST)
        layers = {layer.Name: layer for layer in doc.Pages(1).AllLayers}
        named = {s.Name: s for layer in layers.values() for s, _ in walk_shapes(layer.Shapes) if s.Name}
        return func(doc, layers, named)
    return direct(run)


def ids(shapes):
    return [s["shape_id"] for s in shapes]


base = mcp_inventory(ST)
check("cache: paged inventory (limit 5) equals a fresh full walk", base == truth(ST), len(base))
check("cache: repeated paged inventory (cache hits) is identical", mcp_inventory(ST) == base)
r1, r2 = edit(lambda doc, layers, named: [str(layers[n].CreateRectangle2(1, 1, 0.5, 0.5).StaticID)
                                          for n in ("Groups", "PowerClip")])
inv = mcp_inventory(ST)
check("cache, out-of-band: new top-level shapes appear", r1 in ids(inv) and r2 in ids(inv) and inv == truth(ST))
gone = edit(lambda doc, layers, named: (str(named["deep_leaf2"].StaticID), named["deep_leaf2"].Delete())[0])
inv = mcp_inventory(ST)
check("cache, out-of-band: a deleted nested shape disappears", gone not in ids(inv) and inv == truth(ST))
r = call("get_shape_overprint", document_path=ST, page_index=1, shape_id=gone)
check("cache, out-of-band: exact-ID read of the deleted shape fails as not found",
      not r["success"] and "not found" in r["error"], r.get("error"))
before = ids(mcp_inventory(ST))
edit(lambda doc, layers, named: named["nest_l1"].OrderToBack())
inv = mcp_inventory(ST)
check("cache, out-of-band: top-level reorder (same counts, no new IDs) is reflected",
      ids(inv) != before and inv == truth(ST), ids(inv)[:6])
before = ids(inv)
edit(lambda doc, layers, named: named["nest_l2_rect"].OrderBackOne())  # first of two children -> second
inv = mcp_inventory(ST)
check("cache, out-of-band: reorder inside a nested group is reflected", ids(inv) != before and inv == truth(ST),
      ids(inv)[:8])
restored = edit(lambda doc, layers, named: (str(named["nest_l3_ellipse"].StaticID), named["nest_l3_ellipse"].Delete())[0])
mcp_inventory(ST)
undo(ST)
inv = mcp_inventory(ST)
check("cache, out-of-band: undo of a delete restores the old ID", restored in ids(inv) and inv == truth(ST))
def swap(doc, layers, named):
    first = [x for x in layers["Groups"].Shapes if str(x.StaticID) == r1][0]
    second = [x for x in layers["PowerClip"].Shapes if str(x.StaticID) == r2][0]
    first.MoveToLayer(layers["PowerClip"])
    second.MoveToLayer(layers["Groups"])


edit(swap)
inv = mcp_inventory(ST)
moved = {s["shape_id"]: s["layer"]["name"] for s in inv}
check("cache, out-of-band: shapes swapped between layers (counts unchanged) are reported on their new layers",
      moved.get(r1) == "PowerClip" and moved.get(r2) == "Groups" and inv == truth(ST), (moved.get(r1), moved.get(r2)))
r = call("get_shape_overprint", document_path=ST, page_index=1, shape_id=r1)
check("cache, out-of-band: exact-ID read finds a moved shape", r["success"], r.get("error"))
r = call("duplicate_shape_by_id", document_path=ST, page_index=1, shape_id=r2, unit="in", dx=0.1)
inv = mcp_inventory(ST)
new_ids = set(ids(inv)) - set(ids(base)) - {r1, r2}
check("cache, MCP edit: a duplicate made through the server appears", r["success"] and len(new_ids) == 1
      and inv == truth(ST), r.get("error") or new_ids)
dup = next(iter(new_ids), None)
r = call("delete_shape_by_id", document_path=ST, page_index=1, shape_id=dup)
inv = mcp_inventory(ST)
check("cache, MCP edit: a shape deleted through the server disappears", r["success"] and dup not in ids(inv)
      and inv == truth(ST), r.get("error"))
r = call("list_all_text_shapes", document_path=ST, page_index=1)
check("text list: batched listing reads the structure fixture", r["success"], r.get("error"))

# Status while a tool runs: get_coreldraw_status answers at once and names the running tool.
holder, polls = {}, []
worker = threading.Thread(target=lambda: holder.setdefault("r", call("inspect_shapes", document_path=BIG, page_index=1)))
worker.start()
while worker.is_alive():
    status = call("get_coreldraw_status")
    if not status["success"]:
        polls.append(status)
    time.sleep(0.05)
worker.join()
running = [p["data"].get("running") for p in polls if p["data"].get("running")]
check("status: reports the running tool, its time and that it is read-only",
      any(run["operation"] == "inspect_shapes" and run["read_only"] for run in running)
      and all(p["data"]["state"] == "busy" and "inspect_shapes" in p["error"] for p in polls),
      (len(polls), running[:1], holder["r"].get("_seconds")))
check("status: the full big_flat inventory finishes in one call", holder["r"]["success"]
      and len(holder["r"]["data"]["shapes"]) == 4000 and holder["r"]["data"]["next_offset"] is None)
for target in (ST, BIG):
    r = call("close_document", document_path=target, discard_changes=True)
    check(f"cleanup: close {Path(target).name}", r["success"], r.get("error"))

# 5. Ink inventory, PowerClip contents and effect links (E03) ----------------------------------------
import asyncio
import subprocess
from fastmcp import Client
INKS, ST3, EF3 = FX + r"\inks.cdr", FX + r"\e03_structure.cdr", FX + r"\e03_effects.cdr"
for leftover in Path(FX).glob("e03_*.pdf"):
    leftover.unlink()
Path(INKS).unlink(missing_ok=True)
built = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "e03" / "build_inks.py"), FX],
                       capture_output=True, text=True, check=False)
check("inks: fixture built", built.returncode == 0 and Path(INKS).is_file(), built.stdout[-300:] + built.stderr[-300:])
for source, target in (("structure.cdr", ST3), ("effects.cdr", EF3)):
    shutil.copyfile(TEMPLATES / source, target)
for target in (INKS, ST3, EF3):
    r = call("open_document", path=target, activate=False)
    check(f"inks setup: open {Path(target).name}", r["success"], r.get("error"))


def names(path, page):
    r = call("inspect_shapes", document_path=path, page_index=page, fields=["name", "type"])
    assert r["success"], r
    return {s["name"]: s for s in r["data"]["shapes"] if s.get("name")}, r["data"]["shapes"]


def ink(page, name):
    return next((i for i in page["inks"] if i["ink"] == name), None)


def warning(page, code):
    return next((w for w in page["warnings"] if w["code"] == code), None)


r = call("get_ink_inventory", document_path=INKS, max_ids_per_ink=10)
check("inks: whole fixture in one call under 3 s", r["success"] and r["_seconds"] < 3 and r["data"]["next_pages"] is None
      and len(r["data"]["pages"]) == 3, (r.get("error"), r.get("_seconds")))
p1, p2, p3 = r["data"]["pages"]
n1, _ = names(INKS, 1)
check("inks p1: PDF-proof plates", p1["plates"]["pdf_proof"] == ["PANTONE 186 C", "PANTONE 300 C", "PANTONE 354 C",
                                                                  "PANTONE Trans. White", "Cyan", "Magenta"],
      p1["plates"])
check("inks p1: hidden-but-printable layer adds 116 C to print only, with a warning",
      p1["plates"]["hidden_printable_only"] == ["PANTONE 116 C"] and "PANTONE 116 C" in p1["plates"]["print"]
      and warning(p1, "hidden_printable_ink")["ids"] == [n1["hidden_print_116"]["shape_id"]], p1["plates"])
check("inks p1: off-page and clipped spots are blank plates", p1["plates"]["blank_spot_plates"]
      == ["PANTONE 485 C", "PANTONE 7508 C"] and warning(p1, "ink_only_off_page")["inks"] == ["PANTONE 7508 C"])
text_id = n1["text_mixed"]["shape_id"]
runs = {name: [i for i in ink(p1, name)["ids"] if i["id"] == text_id][0]["parts"]
        for name in ("PANTONE 186 C", "PANTONE 300 C", "PANTONE 116 C", "CMYK 100/0/0/0")}
check("inks p1: every text run is reported (spot fill, spot outline, whitespace-only spot, CMYK fill)",
      runs == {"PANTONE 186 C": ["text_fill"], "PANTONE 300 C": ["text_outline"], "PANTONE 116 C": ["text_whitespace"],
               "CMYK 100/0/0/0": ["text_fill"]} and warning(p1, "ink_on_whitespace_text"), runs)
magenta = ink(p1, "CMYK 0/100/0/0")["ids"][0]
check("inks p1: contour-generated process ink links to its effect group and control shape",
      magenta["state"] == "prints" and magenta["effect_control_id"] == n1["contour_control"]["shape_id"]
      and "effect_group_id" in magenta, magenta)
white = ink(p1, "PANTONE Trans. White")["ids"][0]
clipped = ink(p1, "PANTONE 485 C")["ids"][0]
check("inks p1: PowerClip contents carry their container; content outside it is clipped",
      white["powerclip_container_id"] == n1["clip_container"]["shape_id"] == clipped["powerclip_container_id"]
      and white["state"] == "prints" and clipped["state"] == "clipped", (white, clipped))
check("inks p1: a hidden object does not print its ink", ink(p1, "CMYK 0/0/100/0")["objects"] == {"hidden_object": 1}
      and "Yellow" not in p1["plates"]["print"] and warning(p1, "hidden_object_with_ink"))
check("inks p1: locked layer, master layer, non-printing layer and transparency are flagged",
      ink(p1, "PANTONE 354 C")["flags"] == {"locked_layer": 1} and ink(p1, "PANTONE 300 C")["flags"]["master_layer"] == 1
      and ink(p1, "RGB 255/0/0")["objects"] == {"non_printable_layer": 1}
      and ink(p1, "PANTONE 186 C")["flags"] == {"transparent": 1} and warning(p1, "transparency_with_ink"))
check("inks p1: fountain stops and a fountain between two spots",
      ink(p1, "PANTONE 186 C")["parts"]["fountain_stop"] == 3 and ink(p1, "PANTONE 186 C")["tints"] == [100, 20]
      and warning(p1, "fountain_mixes_inks")["ids"] == [n1["fountain_spot_spot"]["shape_id"]])
modes = sorted(b["mode"] for b in p2["bitmaps"])
check("inks p2: bitmap color modes, process warning, registration on every plate", modes == ["RGB", "grayscale"]
      and warning(p2, "bitmap_process")["count"] == 1 and ink(p2, "Registration")["objects"] == {"prints": 1}
      and p2["plates"]["pdf_proof"] == ["PANTONE 300 C", "Cyan", "Magenta", "Yellow", "Black"], p2["plates"])
close = warning(p3, "similar_spot_colors")
check("inks p3: similar spots flagged with a color distance", close and close["inks"][0]["inks"]
      == ["PANTONE 354 C", "PANTONE 355 C"] and ink(p3, "PANTONE 300 C")["flags"] == {"master_layer": 1}, close)
for page in (1, 2, 3):
    pdf = FX + rf"\e03_inks_p{page}.pdf"
    out = call("export_page_proof_pdf", document_path=INKS, page_index=page, output_path=pdf)
    predicted = r["data"]["pages"][page - 1]["plates"]["pdf_proof"]
    check(f"inks p{page}: predicted plates equal the Ghostscript render", out["success"]
          and inked_plates(pdf) == sorted(predicted), (predicted, out.get("error")))
q = call("get_ink_inventory", document_path=INKS, pages=[1], printable_only=True, max_ids_per_ink=0)
check("inks: printable_only drops inks that print nothing; max_ids 0 omits IDs",
      q["success"] and {i["ink"] for i in q["data"]["pages"][0]["inks"]}.isdisjoint(
          {"PANTONE 485 C", "PANTONE 7508 C", "CMYK 0/0/100/0", "RGB 255/0/0"})
      and all(not i.get("ids") for i in q["data"]["pages"][0]["inks"]), q.get("error"))
for args, label in (({"pages": [4]}, "page out of range"), ({"max_ids_per_ink": 51}, "max_ids_per_ink out of range")):
    q = call("get_ink_inventory", document_path=INKS, **args)
    check(f"inks: {label} refused", not q["success"], q.get("error"))
named, shapes = names(ST3, 1)
contents = [s for s in shapes if s.get("in_powerclip")]
check("inspect_shapes: descends into PowerClip contents (7 on structure.cdr) with container IDs",
      len(contents) == 7 and named["clip_rect"]["powerclip_container_id"] == named["clip_container"]["shape_id"]
      and named["clip_rect"]["parent_id"] == named["clip_container"]["shape_id"]
      and named["clip_nested_content"]["powerclip_container_id"] == named["clip_inner"]["shape_id"]
      and named["clip_grp_a"]["parent_id"] == named["clip_group"]["shape_id"], len(contents))
paged = mcp_inventory(ST3, limit=3)
structure_cache.invalidate()
check("inspect_shapes: paged PowerClip inventory equals a fresh walk", paged == truth(ST3), len(paged))


def drop_clip_content(doc, layers, named_shapes):
    container = [s for s in layers["PowerClip"].Shapes if s.Name == "clip_container"][0]
    victim = [s for s in container.PowerClip.Shapes if s.Name == "clip_rect"][0]
    shape_id = str(victim.StaticID)
    victim.Delete()
    return shape_id


def edit_st3(func):
    def run():
        doc = doc_of(ST3)
        layers = {layer.Name: layer for layer in doc.Pages(1).AllLayers}
        return func(doc, layers, None)
    return direct(run)


gone = edit_st3(drop_clip_content)
after = mcp_inventory(ST3, limit=3)
check("cache, out-of-band: a deleted PowerClip content disappears", gone not in ids(after) and after == truth(ST3), gone)
named, shapes = names(EF3, 1)
groups = {s["shape_id"]: s for s in call("inspect_shapes", document_path=EF3, page_index=1)["data"]["shapes"]}
by_id = {v["shape_id"]: k for k, v in named.items()}
links = sorted(by_id.get(s["effect_control_id"]) for s in groups.values() if "effect_control_id" in s)
generated = [s for s in groups.values() if s.get("effect_group_id")]
check("inspect_shapes: contour and shadow groups name their control shape; generated objects their group",
      links == ["contour_control", "contour_inside_control", "shadow_source"] and len(generated) == 4
      and all(groups[s["effect_group_id"]]["type"] in (13, 16) for s in generated), links)


async def _tools():
    async with Client("http://127.0.0.1:8765/mcp") as client:
        return await client.list_tools()


listed = {tool.name for tool in asyncio.run(_tools())}
check("catalog: get_color_report and check_rgb_colors removed, get_ink_inventory added",
      "get_ink_inventory" in listed and not {"get_color_report", "check_rgb_colors"} & listed)
r = call("get_print_separation_settings", document_path=INKS)
check("separation settings: real settings only, no color-set plate list",
      r["success"] and "available_plates" not in r["data"] and r["data"]["disabled_plates"] == []
      and isinstance(r["data"]["separations_enabled"], bool), r.get("error") or r.get("data"))
for target in (INKS, ST3, EF3):
    r = call("close_document", document_path=target, discard_changes=True)
    check(f"cleanup: close {Path(target).name}", r["success"], r.get("error"))

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
