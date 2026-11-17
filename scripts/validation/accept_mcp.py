r"""Acceptance tests through the running MCP server: E08 document management, the 2026-11-17 production
follow-up, the E17a structure cache, the E03 ink inventory and the E02+E05 color editing tools.

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
r = call("create_rectangle", x=1, y=1, width=1, height=1)
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
bad = call("set_fill", document_path=DOC, page_index=1, shape_ids=[IDS["text"]], text_range=[2, 9],
           from_shape_id=IDS["donor"])
check("text copy: out-of-range end is rejected", not bad["success"], bad.get("error"))
r = call("set_fill", document_path=DOC, page_index=1, shape_ids=[IDS["text"]], text_range=[2, 4],
         from_shape_id=IDS["donor"])
new_runs = [(x["start"], x["end"], x["text"], x["fill"].get("ink")) for x in r["data"]["shapes"][0]["runs"]]     if r["success"] else None
check("text copy: donor fill applied to whitespace range (set_fill text_range)", r["success"]
      and new_runs == [(0, 6, "AB  CD", "PANTONE 186 C")], r.get("error") or new_runs)
check("targets: an edit activates its document (so undo stays in it)", active_path() == DOC)
check("text copy: donor color includes tint", r["success"] and r["data"]["color"].get("tint") == 100)
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
Path(FX + r"\proof-p2.pdf").unlink(missing_ok=True)  # a rerun in the same folder must not find an old proof
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
r = call("inspect_contours", document_path=ST, page_index=1, shape_id=gone)
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
r = call("inspect_contours", document_path=ST, page_index=1, shape_id=r1)
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

# 6. Color editing: set_fill / set_outline / set_overprint / set_transparency / replace_color / undo (E02+E05) --
SAMPLES = Path(r"C:\CorelMcpLab\samples")
CL, CL2, KG, KN = (FX + rf"\{name}.cdr" for name in ("e02_colors", "e02_replace", "e02_kings", "e02_knights"))
for leftover in [*Path(FX).glob("e02_*.pdf"), Path(CL), Path(CL2)]:
    leftover.unlink(missing_ok=True)
built = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "e02" / "build_colors.py"), FX],
                       capture_output=True, text=True, check=False)
E = json.loads(built.stdout.strip().splitlines()[-1]) if built.returncode == 0 and Path(CL).is_file() else {}
check("colors: fixture built", bool(E), built.stdout[-300:] + built.stderr[-300:])
shutil.copyfile(CL, CL2)
shutil.copyfile(SAMPLES / "51019 - Kings High School 2021 Shirt Order - SCREENS.cdr", KG)
shutil.copyfile(SAMPLES / "Knights Striped HEad.cdr", KN)
for target in (CL, CL2, KG, KN):
    r = call("open_document", path=target, activate=False)
    check(f"colors setup: open {Path(target).name}", r["success"], r.get("error"))


def shapes_of(path, page=1):
    r = call("inspect_shapes", document_path=path, page_index=page)
    assert r["success"], r
    return {s["shape_id"]: s for s in r["data"]["shapes"]}


def runs_of(path, shape_id):
    r = call("inspect_text_fill_runs", document_path=path, page_index=1, shape_id=shape_id)
    assert r["success"], r
    return [(x["start"], x["end"], x["text"], x["fill"].get("color", {}).get("name"),
             x["fill"].get("color", {}).get("tint")) for x in r["data"]["runs"]]


def snapshot(path, texts=()):
    """Everything the color tools can change: every shape record, text runs, and the ink inventory."""
    inventory = call("get_ink_inventory", document_path=path, max_ids_per_ink=50)
    assert inventory["success"], inventory
    return {"shapes": shapes_of(path), "runs": {t: runs_of(path, t) for t in texts},
            "inks": inventory["data"]["pages"]}


def dirty(path):
    return direct(lambda: doc_of(path).Dirty)


def color(state, part="fill"):
    value = state.get(part) or {}
    value = value.get("color", value) if part == "outline" else value
    return (value.get("ink"), value.get("kind"), value.get("tint"))


def ink_ids(path, name):
    r = call("get_ink_inventory", document_path=path, max_ids_per_ink=50)
    found = [i for p in r["data"]["pages"] for i in p["inks"] if i["ink"] == name]
    return sorted({x["id"] for i in found for x in i.get("ids", [])}, key=int), found


# Exact-object edits.
before = shapes_of(CL)
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["spot_fill"]], color="485 C")
check("set_fill: spot by name without PANTONE prefix, palette reported, read back as a spot",
      r["success"] and color(r["data"]["shapes"][0]) == ("PANTONE 485 C", "spot", 100) and r["data"].get("palette"),
      r.get("error") or r["data"])
after = shapes_of(CL)
check("set_fill: independent read (inspect_shapes) agrees and nothing else changed",
      after[E["spot_fill"]]["fill"]["color"]["name"] == "PANTONE 485 C"
      and all(after[i] == before[i] for i in before if i != E["spot_fill"]))
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["tint60"]],
         color={"ink": "PANTONE 485 C", "tint": 40})
check("set_fill: spot tint", r["success"] and color(r["data"]["shapes"][0]) == ("PANTONE 485 C", "spot", 40),
      r.get("error"))
r = call("set_outline", document_path=CL, page_index=1, shape_ids=[E["spot_fill"]], color="PANTONE 485 C")
out = r["data"]["shapes"][0]["outline"] if r["success"] else {}
check("set_outline: spot outline by name stays a spot and keeps its 0.5 pt width",
      r["success"] and color({"outline": out}, "outline") == ("PANTONE 485 C", "spot", 100) and abs(out["width_pt"] - 0.5) < 1e-3
      and shapes_of(CL)[E["spot_fill"]]["outline"]["color"]["kind"] == "spot", r.get("error") or out)
r = call("set_outline", document_path=CL, page_index=1, shape_ids=[E["donor"]], width=1, unit="mm")
out = r["data"]["shapes"][0]["outline"] if r["success"] else {}
check("set_outline: width in mm, color kept", r["success"] and abs(out["width_pt"] - 72 / 25.4) < 1e-3
      and color({"outline": out}, "outline") == ("PANTONE 300 C", "spot", 100), r.get("error") or out)
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["rgb"]], color="registration")
check("set_fill: registration", r["success"]
      and color(r["data"]["shapes"][0]) == ("Registration", "registration", None), r.get("error"))
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["cmyk_red"]], from_shape_id=E["donor"])
check("set_fill: donor fill copied (spot and tint)", r["success"]
      and color(r["data"]["shapes"][0]) == ("PANTONE 300 C", "spot", 100), r.get("error"))
r = call("set_outline", document_path=CL, page_index=1, shape_ids=[E["cmyk_red"]],
         color={"from_shape_id": E["donor"], "part": "outline"})
out = r["data"]["shapes"][0]["outline"] if r["success"] else {}
check("set_outline: donor outline color copied, own 1 pt width kept", r["success"]
      and color({"outline": out}, "outline") == ("PANTONE 300 C", "spot", 100) and abs(out["width_pt"] - 1) < 1e-3,
      r.get("error") or out)
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["cmyk_red"]], color="CMYK 0/100/100/0")
check("set_fill: CMYK", r["success"] and r["data"]["shapes"][0]["fill"]["components"] == [0, 100, 100, 0],
      r.get("error"))
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["rgb"]], fountain={"type": "radial", "stops": [
    {"color": "PANTONE 300 C", "position": 0}, {"color": {"ink": "485 C", "tint": 50}, "position": 50},
    {"color": "CMYK 0/0/0/100", "position": 100}]})
stops = [(x["position"], x["color"]["ink"], x["color"].get("tint")) for x in r["data"]["shapes"][0]["fill"]["stops"]] \
    if r["success"] else None
check("set_fill: radial fountain with three stops (spot tint mid stop)", r["success"]
      and r["data"]["shapes"][0]["fill"]["type"] == "radial"
      and stops == [(0, "PANTONE 300 C", 100), (50, "PANTONE 485 C", 50), (100, "CMYK 0/0/0/100", None)],
      r.get("error") or stops)
snap = shapes_of(CL)
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["donor"]], color="PANTONE 4855 X")
check("set_fill: unknown spot fails with near matches, nothing changed", not r["success"]
      and "Near matches" in r["error"] and "485" in r["error"] and shapes_of(CL) == snap, r.get("error"))
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["donor"]], color="CMYK 0/120/0/0")
check("set_fill: out-of-range CMYK refused", not r["success"], r.get("error"))
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["donor"], E["locked_186"]], color="485 C")
check("refusal: locked-layer target refuses the whole call, per object, nothing changed", not r["success"]
      and r["data"]["refused"] == [{"id": E["locked_186"], "layer": "Locked", "reason": "on a locked layer"}]
      and shapes_of(CL) == snap, r.get("error"))
generated = next(i for i, s in snap.items() if s.get("effect_group_id"))
for label, ids in (("group", [E["grouped"]]), ("effect-generated object", [generated]),
                   ("master layer", [E["mark_186"]])):
    r = call("set_outline", document_path=CL, page_index=1, shape_ids=ids, color="485 C", width=1)
    check(f"refusal: {label}", not r["success"] and len(r["data"].get("refused", [])) == 1 and shapes_of(CL) == snap,
          r.get("error") or r.get("data"))
r = call("set_outline", document_path=CL, page_index=1, shape_ids=[E["tint60"]], color="485 C")
check("refusal: color alone on a shape without outline (needs a width)", not r["success"]
      and "width" in r["data"]["refused"][0]["reason"])
r = call("set_overprint", document_path=CL, page_index=1, shape_ids=[E["donor"], E["spot_fill"]], fill=True,
         outline=True)
check("set_overprint: both flags read back and agree with inspect_shapes", r["success"]
      and all(s["overprint"] == {"fill": True, "outline": True} for s in r["data"]["shapes"])
      and shapes_of(CL)[E["donor"]]["overprint_outline"] is True, r.get("error"))
r = call("set_overprint", document_path=CL, page_index=1, shape_ids=[E["donor"]], outline=False)
check("set_overprint: an omitted flag is kept", r["success"]
      and r["data"]["shapes"][0]["overprint"] == {"fill": True, "outline": False}, r.get("error"))
r = call("set_transparency", document_path=CL, page_index=1, shape_ids=[E["donor"]], opacity=40)
check("set_transparency: opacity 40 read back", r["success"] and r["data"]["shapes"][0]["opacity"] == 40,
      r.get("error"))
r = call("set_transparency", document_path=CL, page_index=1, shape_ids=[E["donor"]], opacity=100)
check("set_transparency: 100 removes it", r["success"] and r["data"]["shapes"][0]["opacity"] == 100, r.get("error"))
s0 = snapshot(CL, [E["text_mixed"]])
r = call("set_fill", document_path=CL, page_index=1, shape_ids=[E["text_mixed"]], color="PANTONE 485 C")
check("set_fill: whole text gets one fill (all runs)", r["success"]
      and color(r["data"]["shapes"][0]) == ("PANTONE 485 C", "spot", 100), r.get("error"))
r = call("undo", document_path=CL)
check("undo: one step restores the text runs and every shape", r["success"]
      and snapshot(CL, [E["text_mixed"]]) == s0, r.get("error"))
r = call("undo", document_path=CL, steps=0)
check("undo: steps out of range refused", not r["success"])

# Custom (non-Pantone) spot from the Knights document palette, matched case-insensitively.
kn = shapes_of(KN)
spot_target = next(i for i, s in kn.items() if s["layer"]["printable"] and not s["layer"]["locked"]
                   and not s["layer"]["master"] and (s.get("fill") or {}).get("color", {}).get("kind") == "spot"
                   and not s.get("effect_group_id") and s["type"] not in (7, 6))
r = call("set_fill", document_path=KN, page_index=1, shape_ids=[spot_target], color="rvw-bk01a")
check("set_fill: custom spot RVW-BK01A from the document palette", r["success"]
      and color(r["data"]["shapes"][0]) == ("RVW-BK01A", "spot", 100) and r["data"]["palette"] == "document palette",
      r.get("error") or r.get("data"))

# replace_color on a fresh copy.
texts = [E["text_mixed"], E["text_uniform"]]
r0 = snapshot(CL2, texts)
mixed_runs = r0["runs"][E["text_mixed"]]
dirty0 = dirty(CL2)
r = call("replace_color", document_path=CL2, from_color="PANTONE 186 C", to_color="485 C")
summary = r["data"]["summary"] if r["success"] else {}
check("replace dry run: exact plan (4 fills, 1 outline, 3 text-fill runs, 1 text-outline run, 1 stop, 1 contour)",
      r["success"] and {k: summary.get(k) for k in ("fill", "outline", "text_fill", "text_outline", "fountain_stop",
                                                    "effect")}
      == {"fill": 4, "outline": 1, "text_fill": 3, "text_outline": 1, "fountain_stop": 1, "effect": 1}, summary)
reasons = {g["reason"]: [x["id"] for x in g["ids"]] for g in r["data"]["not_changed"]} if r["success"] else {}
check("replace dry run: locked and master objects listed as not changed, contour objects follow their effect",
      reasons == {"on a locked layer": [E["locked_186"]], "on a master layer (shared by every page)": [E["mark_186"]]}
      and [x["effect_control_id"] for x in r["data"]["follows_effect"]] == [E["contour_control"]], reasons)
tint_group = [g for g in r["data"]["changes"] if g["old"].get("tint") == 60] if r["success"] else []
check("replace dry run: a spot tint is kept (186 C 60 % -> 485 C 60 %)",
      len(tint_group) == 1 and tint_group[0]["new"]["tint"] == 60 and tint_group[0]["ids"] == [E["tint60"]])
check("replace dry run: zero mutation (shapes, text runs, inventory, dirty flag)",
      snapshot(CL2, texts) == r0 and dirty(CL2) == dirty0)
entry186 = next(i for i in r0["inks"][0]["inks"] if i["ink"] == "PANTONE 186 C")
r = call("replace_color", document_path=CL2, from_color=entry186, to_color="485 C", parts=["fill"])
check("replace: an inventory ink entry is accepted as from_color", r["success"] and r["data"]["summary"]["fill"] == 4,
      r.get("error"))
for tolerance, expected in ((0, 0), (1, 1)):
    r = call("replace_color", document_path=CL2, from_color="CMYK 0/100/100/0", to_color="485 C", parts=["outline"],
             tolerance=tolerance)
    check(f"replace: process match tolerance {tolerance} -> {expected} outline", r["success"]
          and r["data"]["summary"]["changes"] == expected, r.get("error"))
for args, label in (({"parts": ["fills"]}, "unknown part"), ({"pages": [2]}, "page out of range"),
                    ({"layers": ["Nope"]}, "unknown layer"), ({"from_color": "none"}, "from none")):
    r = call("replace_color", **{"document_path": CL2, "from_color": "186 C", "to_color": "485 C", **args})
    check(f"replace: {label} refused", isinstance(r, list) or not r["success"], r if isinstance(r, list) else r.get("error"))
r = call("replace_color", document_path=CL2, from_color="PANTONE 186 C", to_color="485 C", dry_run=False)
check("replace real: one call, every change verified, only locked and master objects remain", r["success"]
      and r["data"]["verified"] == 11 and r["data"]["remaining"]["count"] == 2
      and sorted(x["id"] for x in r["data"]["remaining"]["ids"]) == sorted([E["locked_186"], E["mark_186"]], key=int),
      r.get("error") or r["data"].get("verification_errors"))
check("targets: replace_color activates its document", active_path() == CL2)
runs_after = runs_of(CL2, E["text_mixed"])
check("replace real: only the matching text runs changed",
      runs_after == [(0, 2, "AB", "PANTONE 485 C", 100), (2, 4, "  ", "PANTONE 300 C", 100),
                     (4, 6, "CD", "PANTONE 485 C", 100), (6, 9, " EF", None, None)]
      and mixed_runs[1] == runs_after[1], runs_after)
ids186, _ = ink_ids(CL2, "PANTONE 186 C")
ids485, found485 = ink_ids(CL2, "PANTONE 485 C")
check("replace real: inventory shows 186 C only on the locked and master objects, 485 C on the rest (tint 60 kept)",
      ids186 == sorted([E["locked_186"], E["mark_186"]], key=int) and E["tint60"] in ids485
      and E["text_mixed"] in ids485 and 60 in found485[0]["tints"], (ids186, ids485))
r = call("undo", document_path=CL2)
check("replace undo: one undo restores every shape, text run and the inventory", r["success"]
      and snapshot(CL2, texts) == r0, r.get("error"))
r = call("replace_color", document_path=CL2, from_color="PANTONE 186 C", to_color="none", parts=["outline", "text"],
         dry_run=False)
runs_none = runs_of(CL2, E["text_mixed"])
check("replace to none: matching text runs lose their fill, other runs intact", r["success"]
      and r["data"]["verified"] == 5 and runs_none[1] == mixed_runs[1] and runs_none[0][3] is None,
      r.get("error") or runs_none)
call("undo", document_path=CL2)

# E01 T6b on a Kings copy: 186 C -> 485 C across fills, outlines and text in one call; plates prove it.
r = call("replace_color", document_path=KG, from_color="PANTONE 186 C", to_color="PANTONE 485 C", dry_run=False)
check("Kings T6b: one replace_color call recolors fills, outlines and text runs", r["success"]
      and {k: r["data"]["summary"].get(k) for k in ("fill", "outline", "text_fill")}
      == {"fill": 2, "outline": 2, "text_fill": 1}
      and r["data"]["verified"] == 5 and r["data"]["remaining"]["count"] == 0, r.get("error") or r.get("data"))
gone, _ = ink_ids(KG, "PANTONE 186 C")
new, found = ink_ids(KG, "PANTONE 485 C")
check("Kings T6b: inventory has no 186 C and 485 C on 4 objects (2 spot outlines)", not gone and len(new) == 4
      and found[0]["parts"] == {"fill": 2, "outline": 2, "text_fill": 1}, (gone, found and found[0]["parts"]))
pdf = FX + r"\e02_kings_p1.pdf"
out = call("export_page_proof_pdf", document_path=KG, page_index=1, output_path=pdf)
plates = inked_plates(pdf) if out["success"] else []
check("Kings T6b: Ghostscript plates show PANTONE 485 C and no PANTONE 186 C", out["success"]
      and "PANTONE 485 C" in plates and "PANTONE 186 C" not in plates, plates)


async def _catalog():
    async with Client("http://127.0.0.1:8765/mcp") as client:
        return await client.list_tools()


names = {tool.name for tool in asyncio.run(_catalog())}
removed = {"set_fill_cmyk", "set_fill_rgb", "set_fill_pantone", "set_no_fill", "set_fountain_fill", "copy_shape_fill",
           "set_no_outline", "set_shape_overprint", "get_shape_overprint", "copy_fill_to_text_range"}
check("catalog: color tools consolidated", {"set_fill", "set_outline", "set_overprint", "set_transparency",
                                            "replace_color", "undo"} <= names and not removed & names,
      sorted(removed & names))
for target in (CL, CL2, KG, KN):
    r = call("close_document", document_path=target, discard_changes=True)
    check(f"cleanup: close {Path(target).name}", r["success"], r.get("error"))

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
