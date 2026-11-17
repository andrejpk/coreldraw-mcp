r"""Acceptance tests through the running MCP server: E08 document management, the 2026-11-17 production
follow-up, the E17a structure cache, the E03 ink inventory, the E02+E05 color editing tools, E10 plate
feedback (render_plates, merged export_page_proof_pdf, simulate_overprints) and E04+E11 find_shapes (filters,
artifact presets, paging, selection, the named-shape cache fingerprint) and E13/E13b geometry and effect tools
(offset_silhouette, combine_shapes, duplicate_shapes, separate_effect, convert_to_curves, apply_contour,
inspect_shapes effect_details) on fresh template copies in <dir>\fx\e13b, and the E07+E06 layer,
transform, arrange, delete, group, PowerClip, create and import tools on copies in <dir>\fx\e07.

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
r = call("create_text_frame", document_path=B, x=10, y=10, width=20, height=10, text="x")
check("mismatch: legacy edit on a non-active document refused", not r["success"] and "mismatch" in r["error"]
      and direct(lambda: doc_of(B).Pages(1).Shapes.Count) == b_shapes, r.get("error"))
r = call("create_text_frame", x=10, y=10, width=20, height=10, text="x")
check("mismatch: legacy edit without document_path refused", not (isinstance(r, dict) and r.get("success")), r)
r = call("check_dimensions", expected_width=215.9, expected_height=279.4, document_path=B)
check("mismatch: legacy read naming a non-active document refused", not r["success"] and "mismatch" in r["error"])
r = call("check_dimensions", expected_width=215.9, expected_height=279.4)
check("legacy read defaults to the active document and names it", r["success"] and r["data"]["document_path"] == A)
r = call("create_shape", document_path=A, page_index=1, kind="rectangle", x=1, y=1, width=0.5, height=0.5, unit="in")
check("exact edit (create_shape) works, reports document and unit",
      r["success"] and r["data"]["document_path"] == A and r["data"]["document_unit"] == "in", r.get("error"))
rect = r["data"]["shape_id"] if r["success"] else None

# Tools that used to force Unit = mm must leave the unit alone.
for name, args in (
    ("add_guideline", {"document_path": A, "position": 25.4}),
    ("create_text_frame", {"document_path": A, "x": 25.4, "y": 25.4, "width": 50.8, "height": 12.7, "text": "unit"}),
    ("check_dimensions", {"document_path": A, "expected_width": 215.9, "expected_height": 279.4}),
    ("arrange_shapes", {"document_path": A, "page_index": 1, "shape_ids": [rect], "align": "left", "reference": "page"}),
    ("export_png", {"document_path": A, "path": FX + r"\e08_unit.png", "dpi": 72}),
    ("find_shapes", {"document_path": A, "pages": [1], "filters": {"type": "text"}}),
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
r = call("create_shape", document_path=B, page_index=1, kind="rectangle", x=2, y=2, width=0.5, height=0.5, unit="in")
check("setup: make the document dirty", r["success"] and direct(lambda: doc_of(B).Dirty), r.get("error"))
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

# 2. Weld consecutive shapes (E13: combine_shapes weld with preserve_order) ----------------------------
W = lambda *names: [IDS[n] for n in names]  # noqa: E731
WELD = dict(document_path=DOC, page_index=1, operation="weld")
r = call("combine_shapes", **WELD, shape_ids=W("c1", "c3"))
check("weld: non-consecutive shapes rejected", not r["success"] and "consecutive" in r["error"], r.get("error"))
r = call("combine_shapes", **WELD, shape_ids=W("c3", "tint60"))
check("weld: different tint rejected", not r["success"] and "tint" in r["error"], r.get("error"))
r = call("combine_shapes", **WELD, shape_ids=W("top", "c1"))
check("weld: different spot rejected", not r["success"], r.get("error"))
r = call("combine_shapes", **WELD, shape_ids=W("c1", "b"))
check("weld: shapes on different layers rejected", not r["success"], r.get("error"))
before = call("inspect_shapes", document_path=DOC, page_index=1, layer_name="Weld", fields=["z_order", "fill"])
order_before = [s["shape_id"] for s in before["data"]["shapes"]]
r = call("combine_shapes", **WELD, shape_ids=W("c2", "c3", "c1"))
check("weld: consecutive run welds", r["success"], r.get("error") or {k: r["data"][k] for k in ("result_ids", "stack_index")})
data = dict(r["data"], result_shape_id=r["data"]["result_ids"][0], area=r["data"]["results"][0]["area"],
            source_area_sum=sum(x["area"] for x in r["data"]["sources"]))
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
    r = call("export_page_proof_pdf", document_path=DOC, page_index=1, layer_name=name, output_path=out)
    found = scan(out) if r["success"] else {}
    check(f"layer proof {name!r}: only its inks", r["success"] and found["spots"] == spots,
          r.get("error") or (found, r["data"]["excluded_printable_layers"], f"{r['_seconds']}s"))
    if GS:
        check(f"layer proof {name!r}: Ghostscript inks only its plates", r["success"] and inked_plates(out) == spots,
              inked_plates(out) if r["success"] else None)
    check(f"layer proof {name!r}: fill overprint preserved={overprint}", r["success"] and (found["overprint_fill_true"] > 0) == overprint)
    check(f"layer proof {name!r}: document and PDF settings unchanged", direct(pdf_state) == state0)
Path(FX + r"\proof-p2.pdf").unlink(missing_ok=True)  # a rerun in the same folder must not find an old proof
r = call("export_page_proof_pdf", document_path=DOC, page_index=2, layer_name="Ink B", output_path=FX + r"\proof-p2.pdf")
found = scan(FX + r"\proof-p2.pdf") if r["success"] else {}
check("layer proof: same-named layer on page 2 publishes only page 2 ink", found.get("spots") == ["PANTONE 485 C"], found)
if GS:
    check("layer proof: page 2 Ghostscript plates", inked_plates(FX + r"\proof-p2.pdf") == ["PANTONE 485 C"])
r = call("export_page_proof_pdf", document_path=DOC, page_index=1, layer_name="Guides", output_path=FX + r"\x.pdf")
check("layer proof: master/special layer rejected", not r["success"], r.get("error"))
r = call("export_page_proof_pdf", document_path=DOC, page_index=1, layer_name="Ink B", output_path=FX + r"\proof-Ink_B.pdf")
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
r = call("find_shapes", document_path=ST, pages=[1], filters={"ids": [gone]})
check("cache, out-of-band: exact-ID lookup of the deleted shape reports it missing",
      r["success"] and r["data"]["count"] == 0 and r["data"].get("missing_ids") == [gone], r.get("error") or r["data"])
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
r = call("find_shapes", document_path=ST, pages=[1], filters={"ids": [r1]})
check("cache, out-of-band: exact-ID lookup finds a moved shape on its new layer",
      r["success"] and [x["layer"] for x in r["data"]["shapes"]] == ["PowerClip"], r.get("error") or r["data"])
r = call("duplicate_shapes", document_path=ST, page_index=1, shape_ids=[r2], unit="in", dx=0.1)
inv = mcp_inventory(ST)
new_ids = set(ids(inv)) - set(ids(base)) - {r1, r2}
check("cache, MCP edit: a duplicate made through the server appears", r["success"] and len(new_ids) == 1
      and inv == truth(ST), r.get("error") or new_ids)
dup = next(iter(new_ids), None)
r = call("delete_shapes", document_path=ST, page_index=1, shape_ids=[dup])
inv = mcp_inventory(ST)
check("cache, MCP edit: a shape deleted through the server disappears", r["success"] and dup not in ids(inv)
      and inv == truth(ST), r.get("error"))
r = call("find_shapes", document_path=ST, pages=[1], filters={"type": "text"})
check("text list: find_shapes lists the structure fixture's text (PowerClip content included)",
      r["success"] and r["data"]["count"] >= 1, r.get("error") or r["data"]["count"])

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


# 7. Plate feedback: render_plates, merged proof export, simulate_overprints (E10) ----------------------------
SJ, SEED, KN10 = FX + r"\e10_screen_job.cdr", FX + r"\e10_seeded.cdr", FX + r"\e10_knights.cdr"
OUT10 = Path(FX) / "e10"
shutil.rmtree(OUT10, ignore_errors=True)
OUT10.mkdir()
shutil.copyfile(TEMPLATES / "screen_job.cdr", SJ)
shutil.copyfile(TEMPLATES / "screen_job.cdr", SEED)
shutil.copyfile(SAMPLES / "Knights Striped HEad.cdr", KN10)
for target in (SJ, KN10, INKS):
    r = call("open_document", path=target, activate=False)
    check(f"e10 setup: open {Path(target).name}", r["success"], r.get("error"))
UB = ["PANTONE 186 C", "PANTONE 300 C", "PANTONE 116 C"]
ART = [0, 1.5, 13, 18]  # screen_job above the film titles, in inches


def bounds_of(path, page=1):
    r = call("inspect_shapes", document_path=path, page_index=page, fields=["name", "bounds"])
    return {s["name"]: s["bounds"] for s in r["data"]["shapes"] if s.get("name")}


def plate(data, name):
    return next((p for p in data["plates"] if p["plate"] == name), None)


def contains(box, b, tol):
    return (box["left"] <= b["left"] + tol and box["bottom"] <= b["bottom"] + tol
            and box["right"] >= b["left"] + b["width"] - tol and box["top"] >= b["bottom"] + b["height"] - tol)


def sj_state():
    doc = doc_of(SJ)
    return doc.Dirty, doc.ActivePage.Index, doc.Unit, [(l.Name, bool(l.Visible), bool(l.Printable))
                                                       for l in doc.Pages(1).AllLayers]


state_before = direct(sj_state)
inventory = call("get_ink_inventory", document_path=SJ, max_ids_per_ink=0)["data"]["pages"][0]
r = call("render_plates", document_path=SJ, page_index=1, output_dir=str(OUT10 / "sj"),
         contain=[{"plate": "PANTONE Trans. White", "within": UB, "region": ART},
                  {"plate": "PANTONE Trans. White", "within": UB, "region": ART, "inset": 2, "unit": "pt"},
                  {"plate": "PANTONE Trans. White", "within": UB, "region": ART, "inset": 2.5, "unit": "pt"},
                  {"plate": "PANTONE Trans. White", "within": UB},
                  {"plate": "PANTONE 999 C", "within": UB}])
d = r.get("data") or {}
check("render_plates screen_job: one call, under 10 s, small response without image bytes", r["success"]
      and r["_seconds"] < 10 and len(json.dumps(r)) < 20000 and "base64" not in json.dumps(r),
      (r.get("error"), r.get("_seconds"), len(json.dumps(r))))
check("render_plates screen_job: predicted = rendered = inventory pdf_proof", r["success"]
      and sorted(d["predicted_plates"]) == sorted(d["rendered_plates"]) == sorted(inventory["plates"]["pdf_proof"])
      and d["differences"] == [], d.get("differences"))
check("render_plates screen_job: CMYK films are registration-only, spot films carry art",
      r["success"] and all(plate(d, n)["registration_only"] for n in ("Cyan", "Magenta", "Yellow", "Black"))
      and not any(plate(d, n)["registration_only"] for n in UB + ["PANTONE Trans. White"])
      and d["registration_detected"])
named = bounds_of(SJ)
ids = {p["plate"]: sorted(x["id"] for x in p.get("attributed_ids", [])) for p in d.get("plates", [])}
check("render_plates screen_job: attribution names the art and underbase objects (approximate, by bbox)",
      r["success"] and "4" in ids["PANTONE 186 C"] and "6" in ids["PANTONE 300 C"] and "8" in ids["PANTONE 116 C"]
      and {"5", "7", "9"} <= set(ids["PANTONE Trans. White"])
      and not any(p.get("unattributed_art") for p in d["plates"]), ids)
art300 = plate(d, "PANTONE 300 C")["art_bbox"] if r["success"] else None
check("render_plates screen_job: plate bbox in document units, comparable with inspect_shapes bounds",
      r["success"] and contains(art300, named["art_band"], 0.01) and d["document_unit"] == "in"
      and abs(art300["left"] - named["art_band"]["left"]) < 0.02, (art300, named.get("art_band")))
check("render_plates screen_job: hidden-but-printable Halftone Scale is warned about", r["success"] and any(
      w["code"] == "hidden_printable_layers_omitted" and w["layers"] == ["Halftone Scale"] for w in d["warnings"]))
files = sorted(f.name for f in (OUT10 / "sj").iterdir())
check("render_plates screen_job: proof.pdf, composite, one PNG per plate and plates.json written",
      r["success"] and {"proof.pdf", "composite.png", "plates.json"} <= set(files)
      and all(Path(p["png"]).is_file() for p in d["plates"]) and len(d["plates"]) == 8, files)
if GS:
    independent = render(OUT10 / "sj" / "proof.pdf", OUT10 / "sj.independent", GS, 150, 1)
    check("render_plates screen_job: independent render_separation_proof agrees on non-blank plates",
          sorted(p["name"] for p in independent["plates"] if not p["blank"]) == sorted(d["rendered_plates"]))
c = d.get("containment", [{}] * 5)
check("containment: underbase within the inks, region above the titles, inset 0 -> 0 protrusion",
      c[0].get("protrusion_px") == 0 and c[0].get("plate_art_px", 0) > 100000, c[0])
check("containment: inset 2 pt still 0 (choke 0.03 in = 2.16 pt)", c[1].get("protrusion_px") == 0, c[1])
check("containment: inset 2.5 pt protrudes (choke measured between 2 and 2.5 pt)",
      c[2].get("protrusion_px", 0) > 1000 and c[2]["inset_px"] > 5, c[2])
check("containment: without a region the white film title is the protrusion",
      c[3].get("protrusion_px", 0) > 0 and c[3]["protrusion_bbox"]["top"] < 1.5, c[3].get("protrusion_bbox"))
check("containment: unknown plate reported per check", "error" in c[4] and "PANTONE 999 C" in c[4]["error"], c[4])
check("render_plates: document state unchanged (dirty, page, unit, layer visibility and printability)",
      direct(sj_state) == state_before)

r2 = call("render_plates", document_path=SJ, page_index=1, output_dir=str(OUT10 / "sj_hidden"),
          include_hidden_printable=True)
d2 = r2.get("data") or {}
check("render_plates include_hidden_printable: Halftone Scale tints rendered, layer restored, no warning",
      r2["success"] and d2["proof"]["hidden_printable_layers_included"] == ["Halftone Scale (page)"]
      and plate(d2, "PANTONE 116 C")["inked_area"] > plate(d, "PANTONE 116 C")["inked_area"] * 1.2
      and not d2["warnings"] and d2["differences"] == [] and direct(sj_state) == state_before,
      r2.get("error") or d2.get("proof"))
r3 = call("render_plates", document_path=SJ, page_index=1, output_dir=str(OUT10 / "sj_layer"), layer_name="Halftone Scale")
check("render_plates layer_name: only that layer's three inks, predicted = rendered",
      r3["success"] and sorted(r3["data"]["rendered_plates"]) == sorted(UB) == sorted(r3["data"]["predicted_plates"]),
      r3.get("error") or r3["data"]["rendered_plates"])
pdf = FX + r"\e10_hidden.pdf"
Path(pdf).unlink(missing_ok=True)
r4 = call("export_page_proof_pdf", document_path=SJ, page_index=1, output_path=pdf, include_hidden_printable=True)
check("export_page_proof_pdf include_hidden_printable: hidden layer published, state restored", r4["success"]
      and r4["data"]["hidden_printable_layers_included"] == ["Halftone Scale (page)"]
      and direct(sj_state) == state_before, r4.get("error"))
if GS and r4["success"]:
    check("export_page_proof_pdf include_hidden_printable: Ghostscript sees the halftone scale inks",
          inked_plates(pdf) == sorted(inventory["plates"]["pdf_proof"]))
r5 = call("export_page_proof_pdf", document_path=SJ, page_index=1, output_path=FX + r"\e10_x.pdf",
          layer_name="Halftone Scale", include_hidden_printable=True)
check("export_page_proof_pdf: layer_name with include_hidden_printable refused", not r5["success"], r5.get("error"))
pdf = FX + r"\e10_plain.pdf"
Path(pdf).unlink(missing_ok=True)
r6 = call("export_page_proof_pdf", document_path=SJ, page_index=1, output_path=pdf)
check("export_page_proof_pdf: whole page names the omitted hidden-but-printable layer", r6["success"]
      and r6["data"]["hidden_printable_layers_omitted"] == ["Halftone Scale (page)"], r6.get("error") or r6.get("data"))

# Seeded fault: a white object sticking out of the 186 C circle (x 3.5-9.5 in).
r = call("open_document", path=SEED, activate=True)
created = call("create_shape", document_path=SEED, page_index=1, kind="rectangle", x=9.3, y=12.0, width=0.5,
               height=0.5, unit="in")
seed_id = created["data"]["shape_id"] if created["success"] else None
filled = call("set_fill", document_path=SEED, page_index=1, shape_ids=[seed_id], color="PANTONE Trans. White") \
    if seed_id else {"success": False}
check("seeded: white rectangle added on a copy", created["success"] and filled["success"], created.get("error"))
r = call("render_plates", document_path=SEED, page_index=1, output_dir=str(OUT10 / "seeded"),
         contain=[{"plate": "PANTONE Trans. White", "within": UB, "region": ART}])
cs = (r.get("data") or {}).get("containment", [{}])[0]
box = cs.get("protrusion_bbox") or {}
check("seeded: containment catches the protruding white (area and bbox outside the circle)",
      cs.get("protrusion_px", 0) > 500 and box.get("right", 0) > 9.5 and box.get("left", 99) >= 9.25,
      (cs.get("protrusion_px"), box))
check("seeded: the new object is attributed to the white film",
      r["success"] and seed_id in [x["id"] for x in plate(r["data"], "PANTONE Trans. White")["attributed_ids"]])
call("close_document", document_path=SEED, discard_changes=True)

r = call("render_plates", document_path=KN10, page_index=2, output_dir=str(OUT10 / "knights_p2"))
types = {(x["type"], x["plate"]) for x in r["data"]["differences"]} if r["success"] else set()
check("render_plates Knights p2: CMY films reported as coming only from bitmaps",
      {("process_plate_from_bitmaps_only", n) for n in ("Cyan", "Magenta", "Yellow")} <= types, types)
inks_inventory = call("get_ink_inventory", document_path=INKS, max_ids_per_ink=0)["data"]["pages"]
for page in (1, 2, 3):
    r = call("render_plates", document_path=INKS, page_index=page, output_dir=str(OUT10 / f"inks_p{page}"))
    check(f"render_plates inks.cdr p{page}: rendered = predicted = inventory pdf_proof", r["success"]
          and sorted(r["data"]["rendered_plates"]) == sorted(r["data"]["predicted_plates"])
          == sorted(inks_inventory[page - 1]["plates"]["pdf_proof"]),
          r.get("error") or (r["data"]["rendered_plates"], r["data"]["differences"]))

for args, label in (({"output_dir": str(OUT10 / "sj")}, "non-empty output folder"),
                    ({"output_dir": "relative\\dir"}, "relative output folder"),
                    ({"output_dir": str(OUT10 / "bad"), "dpi": 1200}, "dpi out of range"),
                    ({"output_dir": str(OUT10 / "bad"), "contain": [{"plate": "A"}]}, "contain without within"),
                    ({"output_dir": str(OUT10 / "bad"), "page_index": 9}, "page out of range")):
    r = call("render_plates", **{"document_path": SJ, "page_index": 1, **args})
    check(f"render_plates: {label} refused", not r["success"], r.get("error"))

before = call("simulate_overprints", document_path=SJ)
on = call("simulate_overprints", document_path=SJ, enabled=not before["data"]["simulate_overprints"])
back = call("simulate_overprints", document_path=SJ, enabled=before["data"]["simulate_overprints"])
check("simulate_overprints: read, set and set back with read-back", before["success"] and on["success"]
      and on["data"]["simulate_overprints"] != before["data"]["simulate_overprints"]
      and back["data"]["simulate_overprints"] == before["data"]["simulate_overprints"], (before, on, back))
listed10 = {tool.name for tool in asyncio.run(_catalog())}
check("catalog: render_plates and simulate_overprints added; layer proof and overprint get/set merged",
      {"render_plates", "simulate_overprints", "export_page_proof_pdf"} <= listed10
      and not {"export_layer_proof_pdf", "get_simulate_overprints", "set_simulate_overprints"} & listed10)
for target in (SJ, KN10, INKS):
    r = call("close_document", document_path=target, discard_changes=True)
    check(f"cleanup: close {Path(target).name}", r["success"], r.get("error"))

# 8. find_shapes: filters, artifact presets, paging, selection, cache fingerprint (E04+E11) -------------------
FD, KN4, ART4 = FX + r"\finds.cdr", FX + r"\e04_knights.cdr", FX + r"\e04_artifacts.cdr"
Path(FD).unlink(missing_ok=True)
built = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "e04" / "build_finds.py"), FX],
                       capture_output=True, text=True, check=False)
F = json.loads(built.stdout) if built.returncode == 0 and Path(FD).is_file() else {"ids": {}, "expected": {}}
check("finds: fixture built", bool(F["ids"]), built.stdout[-300:] + built.stderr[-300:])
shutil.copyfile(SAMPLES / "Knights Striped HEad.cdr", KN4)
shutil.copyfile(TEMPLATES / "artifacts.cdr", ART4)
for target in (FD, KN4, ART4):
    r = call("open_document", path=target, activate=False)
    check(f"find setup: open {Path(target).name}", r["success"], r.get("error"))
FID = F["ids"]


def found(preset=None, path=FD, **args):
    """{name (finds.cdr; PowerClip contents get new IDs on reopen) or ID: reason} of every match."""
    r = call("find_shapes", document_path=path, preset=preset, limit=1000, **args)
    assert r["success"], r
    return {x.get("name", x["id"]) if path == FD else x["id"]: x["reason"] for x in r["data"]["shapes"]}, r


seeded, flagged = set(), set()
for preset, expected in F["expected"].items():
    got, r = found(preset)
    seeded |= set(expected)
    flagged |= set(got)
    check(f"finds preset {preset}: every seeded artifact with its reason, nothing else", got == expected,
          {"missing": {k: v for k, v in expected.items() if got.get(k) != v},
           "extra": {k: v for k, v in got.items() if k not in expected}, "s": r["_seconds"]})
check("finds: no clean object is flagged by any preset (No Print and Locked layers included)", flagged <= seeded,
      sorted(flagged - seeded))

# artifacts.cdr (E01 T3: audit_manifest.py found 7 of 9): every seeded artifact by the right preset.
ART_EXPECTED = {"4": ("zero_size", "zero_size"), "5": ("zero_size", "zero_area"),
                "6": ("invisible", "no_fill_no_outline"), "8": ("stacked_duplicates", "stacked_duplicate"),
                "10": ("off_page", "off_page"), "11": ("off_page", "crosses_page_edge"), "12": ("hairline", "hairline"),
                "13": ("tiny", "tiny")}
hits = {}
for preset in ("zero_size", "invisible", "stacked_duplicates", "off_page", "hairline", "empty_text", "tiny",
               "hidden_printable_content", "registration_on_art"):
    got, _ = found(preset, path=ART4)
    for shape_id, reason in got.items():
        hits.setdefault(shape_id, set()).add((preset, reason))
check("artifacts.cdr: all 9 seeded targets found by the right preset (8 is the copy of 7), nothing else",
      all(want in hits.get(shape_id, set()) for shape_id, want in ART_EXPECTED.items()) and set(hits) <= set(ART_EXPECTED),
      hits)
r = call("find_shapes", document_path=ART4, preset="stacked_duplicates")
check("artifacts.cdr: the duplicate names its original", r["success"] and r["data"]["shapes"][0]["duplicate_of"] == "7")


def names_of(**args):
    got, r = found(**args)
    return set(got), r


got, r = names_of(filters={"name": "rect_186"})
check("filters: exact name, bounds [left, bottom, right, top] in inches", got == {"rect_186"}
      and r["data"]["shapes"][0]["bounds"] == [1, 8, 3, 10] and r["data"]["document_unit"] == "in", r["data"]["shapes"])
got, _ = names_of(filters={"name": "logo_part_*"})
check("filters: glob name finds nested shapes", got == {"logo_part_1", "logo_part_2"}, got)
got, _ = names_of(filters={"name": "*BOUNDS*"})
check("filters: glob is a case-insensitive substring", got == {"same_bounds_186", "same_bounds_300"}, got)
got, r = names_of(pages=[1], filters={"type": "text"})
check("filters: type text lists every text with a preview (replaces list_all_text_shapes)",
      got == {"text_art", "label_ink", "label_reg", "blank_text", "dup_text_a", "dup_text_b"}
      and {x["text"] for x in r["data"]["shapes"]} >= {"ART", "DUP", "   "}, got)
got, _ = names_of(filters={"text": "dup"})
check("filters: text substring", got == {"dup_text_a", "dup_text_b"}, got)
got, r = names_of(filters={"color": "PANTONE Trans. White", "color_part": "outline"})
check("filters: Trans. White outline found, with the matched part",
      got == {"trans_white_outline"} and r["data"]["shapes"][0]["color_parts"] == ["outline"], got)
got, _ = names_of(filters={"color": "Trans. White", "color_part": "fill"})
check("filters: Trans. White as a fill: none (outline only)", got == set(), got)
got, _ = names_of(pages=[1], filters={"color": "registration"})
check("filters: registration color on fills, outlines and text", got == {"reg_mark", "label_reg", "reg_on_art"}, got)
got, _ = names_of(pages=[1], layers=["Art"], filters={"type": "rectangle", "color": "186 C", "color_part": "fill",
                                                      "depth": [0, 0]})
check("filters: type + color without PANTONE prefix + depth + layer combine (AND)",
      got == {"rect_186", "same_bounds_186", "trans_white_outline", "hidden_186", "speck"}, got)
got, _ = names_of(pages=[1], filters={"outline_width": [0.5, 1.5]}, unit="pt")
check("filters: outline width range in pt", got == {"line_1pt", "trans_white_outline", "clip_box", "reg_mark",
                                                    "point_line", "np_point_line"}, got)
got, _ = names_of(pages=[1], filters={"size": [None, 0.01], "type": "rectangle"})
check("filters: size (larger side) in the document unit", got == {"speck"} | {f"dot_{k}" for k in range(25)}, got)
got, _ = names_of(pages=[1], filters={"area": [3.9, 4.1]})
check("filters: bbox area (2 x 2 in)", got == {"rect_186", "ellipse_outline_300"}, got)
got, _ = names_of(pages=[1], filters={"intersects": [2.9, 9.9, 3.1, 10.1]})
check("filters: bounds intersect a rectangle", got == {"rect_186", "reg_on_art"}, got)
got, _ = names_of(pages=[1], filters={"inside": [0.9, 4.9, 2.1, 6.1]})
check("filters: bounds inside a rectangle", got == {"tri_left", "tri_mirror", "dup_tri"}, got)
got, _ = names_of(pages=[2], filters={"on_page": False})
got2, _ = names_of(pages=[2], filters={"on_page": "crossing"})
check("filters: on_page false / crossing", got == {"off_186"} and got2 == {"edge_186"}, (got, got2))
got, _ = names_of(pages=[1], filters={"printable": False})
check("filters: printable false (No Print layer)", got == {"np_no_paint", "np_point_line"}, got)
got, _ = names_of(pages=[1], filters={"visible": False})
check("filters: visible false (hidden object, hidden layer)", got == {"hidden_186", "hidden_print_300"}, got)
got, _ = names_of(pages=[1], filters={"locked": True})
check("filters: locked layer", got == {"locked_186"}, got)
got, _ = names_of(pages=[1], filters={"powerclip": "content"})
got2, _ = names_of(pages=[1], filters={"powerclip": "container"})
check("filters: PowerClip content / container", got == {"clip_inside", "clip_outside"} and got2 == {"clip_box"},
      (got, got2))
got, r = names_of(filters={"ids": [FID["rect_186"], "999999"]})
check("filters: exact IDs, missing IDs reported", got == {"rect_186"} and r["data"]["missing_ids"] == ["999999"],
      r["data"].get("missing_ids"))
got, _ = names_of(pages=[1], cql="$item.Outline.Width > 0.02")
check("cql: read-only condition over $item", got == {"ellipse_outline_300"}, got)
for bad, label in (("$item.Bogus > 1", "unknown property"), ("$item.Name = ", "syntax error"),
                   ("$item.Delete", "method call"), ("@com.Application.Documents.Count > 0", "Application")):
    r = call("find_shapes", document_path=FD, pages=[1], cql=bad)
    check(f"cql: {label} refused with a message", not r["success"] and r["error"], r.get("error"))
got, _ = names_of(pages=[1], filters={"type": "text"})
check("cql: the document is unchanged after the refused queries", len(got) == 6 and not dirty(FD), got)
for args, label in (({"filters": {"colour": "x"}}, "unknown filter key"), ({"filters": {"color": "CMYK 0/0"}}, "bad color"),
                    ({"filters": {"size": [1]}}, "bad range"), ({"preset": "zero_size", "threshold": 1}, "threshold "
                     "outside hairline/tiny"), ({"pages": [3]}, "page out of range"), ({"layers": ["Nope"]}, "unknown layer"),
                    ({"limit": 0}, "limit 0")):
    r = call("find_shapes", document_path=FD, **args)
    check(f"find_shapes: {label} refused", not r["success"], r.get("error"))
got, _ = names_of(preset="tiny", threshold=0.001)
got2, _ = names_of(preset="hairline", threshold=0.05, unit="pt")
check("presets: threshold in the given unit (tiny 0.001 in, hairline 0.05 pt) narrows the result",
      got == set() and got2 == set(), (got, got2))
got, _ = names_of(preset="zero_size", filters={"printable": False})
check("presets: filters.printable=false looks at non-printing layers", got == {"np_point_line"}, got)

# Paging with a continuation; later pages come from the cached result.
pages_out, offset, seconds = [], 0, []
while offset is not None:
    r = call("find_shapes", document_path=FD, preset="tiny", limit=10, offset=offset)
    pages_out += r["data"]["shapes"]
    seconds.append(r["_seconds"])
    offset = r["data"]["next_offset"]
full = call("find_shapes", document_path=FD, preset="tiny", limit=1000)
check("paging: limit-10 pages join to the full result; counts and cluster summary",
      pages_out == full["data"]["shapes"] and full["data"]["count"] == 26
      and full["data"]["counts"] == {"tiny": 1, "tiny_in_cluster": 25}
      and full["data"]["summary"]["tiny_clusters"][0]["parent_id"] == FID["dots"],
      (len(pages_out), full["data"]["counts"], seconds))
inv = call("get_ink_inventory", document_path=FD, pages=[1])
tiny_warning = next((w for w in inv["data"]["pages"][0]["warnings"] if w["code"] == "tiny_with_ink"), {})
check("inventory: tiny objects summarized (isolated vs clusters); speck_with_ink gone",
      tiny_warning.get("count") == 26 and tiny_warning.get("isolated") == 1
      and tiny_warning.get("clusters") == [{"parent_id": FID["dots"], "count": 25}]
      and tiny_warning.get("ids") == [FID["speck"]]
      and not any(w["code"] == "speck_with_ink" for w in inv["data"]["pages"][0]["warnings"]), tiny_warning)

# Knights page 1: the 23 off-page RVW-BK01A objects (E01: the blank film).
r = call("find_shapes", document_path=KN4, pages=[1], preset="off_page", limit=1000)
rvw = call("find_shapes", document_path=KN4, pages=[1], preset="off_page", filters={"color": "RVW-BK01A"}, limit=1000)
check("Knights p1 off_page: 25 objects, 23 of them carry RVW-BK01A", r["success"]
      and r["data"]["counts"] == {"off_page": 25} and rvw["data"]["count"] == 23,
      (r["data"].get("counts"), rvw["data"].get("count")))
r = call("find_shapes", document_path=KN4, pages=[1], preset="stacked_duplicates", limit=1000)
check("Knights p1 stacked_duplicates: copies name their bottom original",
      r["success"] and r["data"]["count"] >= 10 and all(x["duplicate_of"] for x in r["data"]["shapes"]),
      r["data"].get("counts"))

# Selection: select=True makes the matches the UI selection (replaces select_shapes_by_id).
r = call("find_shapes", document_path=FD, pages=[1], preset="stacked_duplicates", select=True)
ui = call("get_ui_selection", document_path=FD, page_index=1)
check("select: duplicates selected and read back", r["success"] and r["data"]["selected_count"] == 2
      and sorted(ui["data"]["shape_ids"]) == sorted([FID["dup_text_b"], FID["dup_tri"]]),
      (r.get("error"), ui.get("data")))
r = call("find_shapes", document_path=FD, filters={"ids": [FID["rect_186"], FID["text_art"]]}, pages=[1], select=True)
ui = call("get_ui_selection", document_path=FD, page_index=1)
check("select: exact IDs (the select_shapes_by_id case)", r["success"]
      and sorted(ui["data"]["shape_ids"]) == sorted([FID["rect_186"], FID["text_art"]]), ui.get("data"))
r = call("find_shapes", document_path=FD, filters={"ids": [FID["rect_186"], "999999"]}, pages=[1], select=True)
ui2 = call("get_ui_selection", document_path=FD, page_index=1)
check("select: a missing ID refuses and keeps the selection", not r["success"]
      and ui2["data"]["shape_ids"] == ui["data"]["shape_ids"], r.get("error"))
r = call("find_shapes", document_path=FD, preset="off_page", select=True)
check("select: more than one page refused", not r["success"] and "one page" in r["error"], r.get("error"))
r = call("find_shapes", document_path=FD, pages=[2], preset="off_page", select=True)
ui = call("get_ui_selection", document_path=FD, page_index=2)
check("select: activates the page and selects there", r["success"]
      and sorted(ui["data"]["shape_ids"]) == sorted([FID["edge_186"], FID["off_186"]]),
      (r.get("error"), ui.get("data") or ui.get("error")))
call("edit_pages", document_path=FD, action="activate", page_index=1)

# An MCP edit (generation) is seen by the next query.
r = call("set_overprint", document_path=FD, page_index=1, shape_ids=[FID["rect_186"]], fill=True)
got, _ = names_of(pages=[1], filters={"overprint_fill": True})
check("filters: overprint_fill after an MCP edit", r["success"] and got == {"rect_186"}, (r.get("error"), got))


# Cache fingerprint (E17a blind spot): out-of-band COM edits that add NAMED nested shapes.
def fd_edit(func):
    def run():
        doc = doc_of(FD)
        art = next(layer for layer in doc.Pages(1).Layers if layer.Name == "Art")
        named = {s.Name: s for s, _ in walk_shapes(art.Shapes) if s.Name}
        return func(art, named)
    return direct(run)


def swap_named(art, named):
    """Delete a named group member and add a named rectangle to the same group: every count stays the same."""
    named["logo_base"].Delete()
    new = art.CreateRectangle2(6, 5.9, 0.2, 0.2)
    new.Name = "e04_named_in_group"
    new.OrderFrontOf(named["logo_inner"])
    return str(new.StaticID), str(new.Evaluate("@com.ParentGroup.StaticID"))


def named_into_clip(art, named):
    new = art.CreateRectangle2(3.2, 5.2, 0.2, 0.2)
    new.Name = "e04_named_in_clip"
    new.AddToPowerClip(named["same_bounds_300"], 0)
    return str(new.StaticID)


warm, _ = names_of(pages=[1], filters={"name": "e04_*"})  # walks and caches page 1
new_id, parent = fd_edit(swap_named)
got, r = names_of(pages=[1], filters={"name": "e04_*"})
everything, _ = names_of(pages=[1])
check("cache, out-of-band: a named shape swapped into a group (all counts unchanged) is found, the deleted one gone",
      warm == set() and parent == FID["group_logo"] and got == {"e04_named_in_group"}
      and "logo_base" not in everything, (parent, got))
new_clip = fd_edit(named_into_clip)
got, r = names_of(pages=[1], filters={"powerclip": "content"})
got2, _ = names_of(pages=[1], filters={"powerclip": "container"})
check("cache, out-of-band: a named shape put into a new PowerClip is found, its container reported",
      "e04_named_in_clip" in got and "same_bounds_300" in got2, (got, got2))

listed4 = {tool.name for tool in asyncio.run(_catalog())}
check("catalog: find_shapes added; find_shape_by_name, select_shapes, list_all_text_shapes, select_shapes_by_id "
      "removed; get_ui_selection kept", {"find_shapes", "get_ui_selection"} <= listed4 and not {
          "find_shape_by_name", "select_shapes", "list_all_text_shapes", "select_shapes_by_id"} & listed4)
for target in (FD, KN4, ART4):
    r = call("close_document", document_path=target, discard_changes=True)
    check(f"cleanup: close {Path(target).name}", r["success"], r.get("error"))

# E13/E13b: geometry and effect tools on fresh template copies, MCP only ---------------------------------------
G = Path(FX) / "e13b"
for leftover in call("list_documents")["data"]["documents"]:  # an earlier interrupted run's copies
    if str(leftover.get("document_path") or "").lower().startswith(str(G).lower() + "\\"):
        call("close_document", document_path=leftover["document_path"], discard_changes=True)
shutil.rmtree(G, ignore_errors=True)
G.mkdir(parents=True, exist_ok=True)
GEO = {}
for stem in ("screen_job", "colors", "text_runs", "structure", "effects", "layers", "artifacts"):
    shutil.copyfile(TEMPLATES / f"{stem}.cdr", G / f"{stem}.cdr")
    GEO[stem] = str(G / f"{stem}.cdr")
    assert call("open_document", path=GEO[stem])["success"], stem


def snapshot(path, page=1):
    """Everything an undo must restore on a page: IDs, layers, names, types, paint, overprint, bounds."""
    r = call("inspect_shapes", document_path=path, page_index=page,
             fields=["name", "type", "fill", "outline", "bounds", "overprint_fill", "z_order"])
    assert r["success"], r
    return sorted((s["shape_id"], s["layer"]["name"], s.get("name"), s.get("type"), str(s.get("fill")),
                   str(s.get("outline")), str(s.get("bounds")), str(s.get("overprint_fill")), s.get("z_order"))
                  for s in r["data"]["shapes"])


def page_ids(path, page=1):
    return {row[0] for row in snapshot(path, page)}


def refused_reasons(r):
    return {x["id"]: x["reason"] for x in (r.get("data") or {}).get("refused", [])}


SJ, CO, TR, SU, EF, LA, AR = (GEO[k] for k in ("screen_job", "colors", "text_runs", "structure", "effects", "layers",
                                                 "artifacts"))
BLACK = ["CMYK 0/0/0/100", "PANTONE Black C"]

# offset_silhouette: E01 T6a (combined underbase) in one call on screen_job
before = snapshot(SJ)
r = call("offset_silhouette", document_path=SJ, page_index=1, shape_ids=["4", "6", "8", "10"], offset=0.75, unit="pt",
         direction="inside", exclude_colors=BLACK, color="PANTONE Trans. White", target_layer="White Underbase",
         overprint_fill=True, name="ub_combined")
d = r.get("data") or {}
off = d.get("offset", {})
check("offset_silhouette: T6a underbase in one call (layer, white, overprint, name)", r["success"]
      and d["layer"] == "White Underbase" and d["fill"].get("ink") == "PANTONE Trans. White"
      and d["overprint"]["fill"] is True and d["name"] == "ub_combined" and d["participant_count"] == 4,
      r.get("error") or {k: d.get(k) for k in ("layer", "fill", "overprint", "participant_count")})
check("offset_silhouette: inset 0.75 pt achieved (every side, mean)", r["success"]
      and off["achieved_min_side"] >= 0.74 and 0.7 <= off["achieved_mean"] <= 0.8
      and off["direction_achieved"] == "inside" and d["area"] < d["silhouette"]["area"], off)
after = page_ids(SJ)
check("offset_silhouette: only the result is new, sources kept", r["success"]
      and after == {row[0] for row in before} | {d["result_id"]}, sorted(after - {row[0] for row in before}))
u = call("undo", document_path=SJ)
check("offset_silhouette: one undo restores the page exactly", u["success"] and snapshot(SJ) == before)

# black exclusion, outlines and fills (colors.cdr)
r = call("offset_silhouette", document_path=CO, page_index=1, shape_ids=["19", "22", "14", "4"], offset=1, unit="pt",
         direction="outside", exclude_colors=BLACK)
d = r.get("data") or {}
skipped = {x["id"]: (x["reason"], x["parts"]) for x in d.get("skipped", [])}
parts = {x["id"]: x["parts"] for x in d.get("participants", [])}
check("offset_silhouette: black fill and black outline objects excluded and listed", r["success"]
      and skipped == {"14": ("excluded_color", ["fill"]), "19": ("excluded_color", ["outline"])}, skipped)
check("offset_silhouette: an outline-only shape takes part as its stroke (the ring's hole stays open)", r["success"]
      and parts == {"22": ["outline"], "4": ["fill"]} and d["holes"] >= 1, (parts, d.get("holes")))
check("offset_silhouette: outside 1 pt achieved", r["success"] and d["offset"]["direction_achieved"] == "outside"
      and d["offset"]["achieved_min_side"] >= 0.99, d.get("offset"))
excluded_area = d.get("area", 0)
call("undo", document_path=CO)
r = call("offset_silhouette", document_path=CO, page_index=1, shape_ids=["19", "22", "14", "4"], offset=1, unit="pt",
         direction="outside")
d = r.get("data") or {}
parts = {x["id"]: x["parts"] for x in d.get("participants", [])}
check("offset_silhouette: without exclusion fill + outline both count", r["success"]
      and parts.get("19") == ["fill", "outline"] and d["area"] > excluded_area, parts)
call("undo", document_path=CO)
r = call("offset_silhouette", document_path=CO, page_index=1, shape_ids=["14"], offset=1, exclude_colors=BLACK)
check("offset_silhouette: everything excluded is refused, nothing changed", not r["success"]
      and "nothing takes part" in refused_reasons(r).get("14", ""), r.get("error"))

# target layer, overprint, consumed sources, undo
before = snapshot(CO)
r = call("offset_silhouette", document_path=CO, page_index=1, shape_ids=["4", "5"], offset=0, target_layer="Process",
         overprint_fill=True, keep_sources=False, color="PANTONE Trans. White")
d = r.get("data") or {}
after = page_ids(CO)
check("offset_silhouette: target_layer + overprint + keep_sources=false (sources removed)", r["success"]
      and d["layer"] == "Process" and d["overprint"]["fill"] is True and not {"4", "5"} & after
      and d["result_id"] in after and d["sources_kept"] is False, r.get("error") or d.get("layer"))
check("offset_silhouette: offset 0 keeps the silhouette's area", r["success"] and abs(d["area"] - 2.0) < 1e-4,
      d.get("area"))
u = call("undo", document_path=CO)
check("offset_silhouette: undo restores consumed sources and removes the result", u["success"]
      and snapshot(CO) == before)

# text (converted on copies; counters stay open) and black text exclusion
r = call("offset_silhouette", document_path=TR, page_index=1, shape_ids=["6", "7", "8"], offset=0.5,
         direction="outside", color="PANTONE Trans. White")
d = r.get("data") or {}
check("offset_silhouette: artistic, paragraph and outlined text take part (holes kept)", r["success"]
      and all(x["parts"] == ["text"] for x in d["participants"]) and d["participant_count"] == 3
      and d["holes"] > 10 and d["offset"]["achieved_min_side"] >= 0.49, r.get("error") or d.get("holes"))
call("undo", document_path=TR)
r = call("offset_silhouette", document_path=TR, page_index=1, shape_ids=["4", "8", "6"], offset=0.5,
         exclude_colors=BLACK, color="PANTONE Trans. White")
d = r.get("data") or {}
check("offset_silhouette: text with a black run or black outline is excluded",
      r["success"] and {x["id"] for x in d["skipped"]} == {"4", "8"} and d["participant_count"] == 1,
      r.get("error") or d.get("skipped"))
call("undo", document_path=TR)

# groups expand to their leaves; PowerClips refused
r = call("offset_silhouette", document_path=SU, page_index=1, shape_ids=["6", "15"], offset=1,
         color="PANTONE Trans. White")
d = r.get("data") or {}
sources = {x["id"]: x["source"] for x in d.get("participants", [])}
check("offset_silhouette: groups (flat and 4 levels deep) expand to 7 leaves", r["success"]
      and sources == {"4": "6", "5": "6", "7": "15", "8": "15", "10": "15", "12": "15", "14": "15"}, sources)
call("undo", document_path=SU)
r = call("offset_silhouette", document_path=SU, page_index=1, shape_ids=["21"], offset=1)
check("offset_silhouette: a PowerClip container is refused", not r["success"]
      and "PowerClip" in refused_reasons(r).get("21", ""), r.get("error"))

# effects: control shape allowed, effect group and generated objects refused
r = call("offset_silhouette", document_path=EF, page_index=1, shape_ids=["4"], offset=1, color="PANTONE Trans. White")
check("offset_silhouette: an effect's control shape takes part", r["success"]
      and r["data"]["participant_count"] == 1, r.get("error"))
call("undo", document_path=EF)
before = snapshot(EF)
r = call("offset_silhouette", document_path=EF, page_index=1, shape_ids=["6", "5"], offset=1)
reasons = refused_reasons(r)
check("offset_silhouette: an effect group and an effect-generated object are refused, nothing changed",
      not r["success"] and "effect group" in reasons.get("6", "") and "generated by an effect" in reasons.get("5", "")
      and snapshot(EF) == before, reasons)

# layers: locked, master, mixed pages
r = call("offset_silhouette", document_path=LA, page_index=1, shape_ids=["17", "14"], offset=1, target_layer="Art")
check("offset_silhouette: a kept source on a locked layer may take part (result on Art)", r["success"]
      and r["data"]["layer"] == "Art", r.get("error"))
call("undo", document_path=LA)
before = snapshot(LA)
r = call("offset_silhouette", document_path=LA, page_index=1, shape_ids=["14"], offset=1, target_layer="Locked")
check("offset_silhouette: a locked target layer is refused", not r["success"] and "locked" in r["error"], r.get("error"))
r = call("offset_silhouette", document_path=LA, page_index=1, shape_ids=["17", "14"], offset=1, target_layer="Art",
         keep_sources=False)
check("offset_silhouette: consuming a source on a locked layer is refused", not r["success"]
      and "locked" in refused_reasons(r).get("17", ""), r.get("error"))
r = call("offset_silhouette", document_path=LA, page_index=1, shape_ids=["22"], offset=1)
check("offset_silhouette: a master-layer object is refused", not r["success"]
      and "master" in refused_reasons(r).get("22", ""), r.get("error"))
r = call("offset_silhouette", document_path=LA, page_index=1, shape_ids=["14", "18"], offset=1)
check("offset_silhouette: IDs from two pages are refused (page 2 ID not on page 1)", not r["success"]
      and "18" in r["error"], r.get("error"))
check("offset_silhouette: refusals changed nothing", snapshot(LA) == before)

# an offset that removes everything; argument validation
before = snapshot(AR)
r = call("offset_silhouette", document_path=AR, page_index=1, shape_ids=["13"], offset=2, unit="pt")
check("offset_silhouette: an inset that removes the shape fails and the page is unchanged", not r["success"]
      and "removes" in r["error"] and "unchanged" in r["error"] and snapshot(AR) == before, r.get("error"))
r = call("offset_silhouette", document_path=AR, page_index=1, shape_ids=["13"], offset=-1)
r2 = call("offset_silhouette", document_path=AR, page_index=1, shape_ids=["13"], offset=1, unit="furlong")
check("offset_silhouette: negative offset and unknown unit rejected before any edit", not r["success"]
      and not (isinstance(r2, dict) and r2.get("success")) and snapshot(AR) == before, (r.get("error"), r2))
r = call("offset_silhouette", document_path=AR, page_index=1, shape_ids=["7"], offset=1,
         exclude_colors=["PANTONE Blakc C"])
check("offset_silhouette: an exclusion that matches nothing (typo) is named in exclude_unmatched", r["success"]
      and r["data"].get("exclude_unmatched") == ["PANTONE Blakc C"], r.get("error") or r.get("data"))
call("undo", document_path=AR)

# combine_shapes on colors.cdr: an overlapping duplicate (1 in squares, 0.5 in apart)
r = call("duplicate_shapes", document_path=CO, page_index=1, shape_ids=["4"], dx=0.5, unit="in")
D = r["data"]["new_ids"][0] if r["success"] else None
check("duplicate_shapes: copy offset by 0.5 in", r["success"] and abs(r["data"]["copies"][0]["bounds"][0] - 1.5) < 1e-6,
      r.get("error") or r["data"]["copies"])
base = snapshot(CO)
S = dict(document_path=CO, page_index=1)


def combined(operation, ids, **extra):
    r = call("combine_shapes", **S, shape_ids=ids, operation=operation, **extra)
    return r, (r.get("data") or {}).get("results", [{}])


r, res = combined("weld", ["4", D])
check("combine_shapes weld (preserve_order: adjacent duplicate): union area 1.5, between the old neighbors",
      r["success"] and abs(res[0]["area"] - 1.5) < 1e-6 and res[0]["layer"] == "Spots"
      and r["data"].get("stack_index") is not None, r.get("error") or res)
call("undo", **{"document_path": CO})
check("combine_shapes weld: undo restores", snapshot(CO) == base)
for operation, area, subpaths in (("trim", 0.5, 1), ("intersect", 0.5, 1), ("combine", 2.0, 2)):
    r, res = combined(operation, ["4", D])
    check(f"combine_shapes {operation}: area {area}, {subpaths} subpath(s), sources consumed", r["success"]
          and abs(res[0]["area"] - area) < 1e-6 and res[0]["subpaths"] == subpaths
          and not {"4", D} & (page_ids(CO) - set(r["data"]["result_ids"])), r.get("error") or res)
    call("undo", document_path=CO)
r, res = combined("combine", ["4", "6"], preserve_order=False)
rid = r["data"]["result_ids"][0]
r, res = combined("break_apart", [rid])
check("combine_shapes break_apart: a combined curve splits into its 2 subpaths", r["success"]
      and len(r["data"]["result_ids"]) == 2 and all(x["subpaths"] == 1 for x in res), r.get("error") or res)
call("undo", document_path=CO)
call("undo", document_path=CO)
check("combine_shapes: combine + break apart undone in two steps", snapshot(CO) == base)
r, res = combined("weld", ["4", "8"], preserve_order=False, keep_sources=True, target_layer="Outlines")
check("combine_shapes: keep_sources welds copies onto target_layer, sources kept", r["success"]
      and res[0]["layer"] == "Outlines" and {"4", "8"} <= page_ids(CO), r.get("error") or res)
call("undo", document_path=CO)
r, res = combined("intersect", ["4", "5"])
check("combine_shapes: disjoint intersect fails, undone, page unchanged", not r["success"]
      and "unchanged" in r["error"] and snapshot(CO) == base, r.get("error"))
# consecutive-weld safety refusals (old weld_consecutive_shapes)
r, _ = combined("weld", ["22", "23"])
check("combine_shapes weld refuses shapes without a uniform fill", not r["success"] and "uniform fill" in r["error"],
      r.get("error"))
r = call("combine_shapes", document_path=SU, page_index=1, shape_ids=["4", "5"], operation="weld")
check("combine_shapes weld refuses group members (not direct layer children)", not r["success"]
      and "inside groups" in r["error"], r.get("error"))
r = call("combine_shapes", document_path=SU, page_index=1, shape_ids=["6", "15"], operation="weld",
         preserve_order=False)
check("combine_shapes refuses groups", not r["success"] and "group" in refused_reasons(r).get("6", ""), r.get("error"))
r = call("combine_shapes", document_path=EF, page_index=1, shape_ids=["4", "7"], operation="weld",
         preserve_order=False)
check("combine_shapes refuses shapes with effects", not r["success"]
      and "effect" in refused_reasons(r).get("4", ""), r.get("error"))
r = call("combine_shapes", document_path=EF, page_index=1, shape_ids=["13", "14"], operation="weld")
check("combine_shapes weld refuses a different overprint flag", not r["success"] and "overprint" in r["error"],
      r.get("error"))
r = call("combine_shapes", document_path=LA, page_index=1, shape_ids=["17", "14"], operation="weld",
         preserve_order=False)
check("combine_shapes refuses consuming a shape on a locked layer", not r["success"]
      and "locked" in refused_reasons(r).get("17", ""), r.get("error"))
r, _ = combined("weld", ["4", D], keep_sources=True)
check("combine_shapes: preserve_order with keep_sources rejected", not r["success"] and "preserve_order" in r["error"],
      r.get("error"))
check("combine_shapes: refusals changed nothing", snapshot(CO) == base)

# duplicate_shapes
r = call("duplicate_shapes", document_path=CO, page_index=1, shape_ids=["4", "5", "6", "19"], dx=0.25, dy=-0.25,
         unit="in")
d = r.get("data") or {}
check("duplicate_shapes: four copies (three adjacent on one layer) in input order, offset verified", r["success"]
      and len(d["new_ids"]) == 4 and [x["source"] for x in d["copies"]] == ["4", "5", "6", "19"], r.get("error") or d)
call("undo", document_path=CO)
check("duplicate_shapes: undo restores", snapshot(CO) == base)
r = call("duplicate_shapes", document_path=SU, page_index=1, shape_ids=["15"], target_layer="PowerClip", dy=-1,
         unit="in")
check("duplicate_shapes: a nested group copied onto another layer", r["success"]
      and r["data"]["copies"][0]["layer"] == "PowerClip", r.get("error") or r.get("data"))
call("undo", document_path=SU)
r = call("duplicate_shapes", document_path=LA, page_index=1, shape_ids=["17"])
r2 = call("duplicate_shapes", document_path=EF, page_index=1, shape_ids=["6"])
check("duplicate_shapes: refuses copies onto a locked layer and effect groups", not r["success"]
      and "locked" in refused_reasons(r).get("17", "") and not r2["success"] and refused_reasons(r2).get("6"),
      (r.get("error"), refused_reasons(r2)))

# inspect_shapes effect_details (replaces inspect_contours) and apply_contour
info = {s["shape_id"]: s for s in call("inspect_shapes", document_path=EF, page_index=1)["data"]["shapes"]}
c4 = (info["4"].get("effect_details") or [{}])[0]
c7 = (info["7"].get("effect_details") or [{}])[0]
s11 = (info["11"].get("effect_details") or [{}])[0]
check("inspect_shapes effect_details: outside contour (offset, steps, fill, group)", c4.get("type") == "contour"
      and c4["direction"] == "outside" and abs(c4["offset"] - 0.1) < 1e-6 and c4["steps"] == 1
      and c4["fill_color"].get("name", c4["fill_color"].get("ink")) == "PANTONE 186 C" and c4["group_id"] == "6", c4)
check("inspect_shapes effect_details: inside contour with 2 steps", c7.get("direction") == "inside"
      and c7["steps"] == 2 and abs(c7["offset"] - 0.15) < 1e-6 and c7["group_id"] == "10", c7)
check("inspect_shapes effect_details: drop shadow type and group", s11.get("type") == "drop_shadow"
      and s11.get("group_id") == "12", s11)
before = snapshot(EF)
r = call("apply_contour", document_path=EF, page_index=1, shape_ids=["13", "14"], direction="outside", offset=2,
         unit="pt", steps=2, fill="PANTONE 300 C", corner="round")
info = {s["shape_id"]: s for s in call("inspect_shapes", document_path=EF, page_index=1)["data"]["shapes"]}
got = [(info[i].get("effect_details") or [{}])[0] for i in ("13", "14")]
check("apply_contour: two exact IDs, read back through effect_details", r["success"]
      and all(g.get("steps") == 2 and g.get("corner") == "round" and abs(g.get("offset", 0) - 2 / 72) < 1e-5
              for g in got), r.get("error") or got)
call("undo", document_path=EF)
check("apply_contour: undo restores", snapshot(EF) == before)

# separate_effect: by control shape, by effect group, drop shadow
r = call("separate_effect", document_path=EF, page_index=1, shape_ids=["4", "10"], effect="contour")
d = r.get("data") or {}
controls = {x["id"]: x["control_id"] for x in d.get("shapes", [])}
info = {s["shape_id"]: s for s in call("inspect_shapes", document_path=EF, page_index=1)["data"]["shapes"]}
check("separate_effect: contour by control and by group ID; effects gone", r["success"]
      and controls == {"4": "4", "10": "7"} and not info["4"].get("effect_details")
      and not info["7"].get("effect_details"), r.get("error") or controls)
call("undo", document_path=EF)
r = call("separate_effect", document_path=EF, page_index=1, shape_ids=["11"], effect="drop_shadow")
check("separate_effect: drop shadow becomes plain objects", r["success"] and r["data"]["shapes"][0]["result_ids"],
      r.get("error") or r.get("data"))
call("undo", document_path=EF)
r = call("separate_effect", document_path=EF, page_index=1, shape_ids=["13"])
check("separate_effect: a shape without the effect is refused", not r["success"] and refused_reasons(r).get("13"),
      r.get("error"))
check("separate_effect: undo and refusals restore the page", snapshot(EF) == before)

# convert_to_curves on several exact IDs
before = snapshot(TR)
r = call("convert_to_curves", document_path=TR, page_index=1, shape_ids=["6", "7", "8"])
d = r.get("data") or {}
check("convert_to_curves: three texts converted (paragraph text to a group), IDs kept", r["success"]
      and all(x["type_after"] in (3, 7) and x["id_after"] == x["id"] for x in d["shapes"]), r.get("error") or d)
call("undo", document_path=TR)
check("convert_to_curves: undo restores the text", snapshot(TR) == before)
r = call("convert_to_curves", document_path=SU, page_index=1, shape_ids=["6"])
check("convert_to_curves: a group is refused", not r["success"] and refused_reasons(r).get("6"), r.get("error"))

for path in GEO.values():
    r = call("close_document", document_path=path, discard_changes=True)
    check(f"cleanup: close {Path(path).name}", r["success"], r.get("error"))
listed13 = {tool.name for tool in asyncio.run(_catalog())}
check("catalog: offset_silhouette, combine_shapes, duplicate_shapes, separate_effect added; boolean_operation, "
      "weld_copies_by_id, weld_consecutive_shapes, break_contour_apart, inspect_contours, duplicate_shape_by_id, "
      "duplicate_shapes_by_id, convert_text_to_curves removed",
      {"offset_silhouette", "combine_shapes", "duplicate_shapes", "separate_effect", "convert_to_curves",
       "apply_contour"} <= listed13 and not {"boolean_operation", "weld_copies_by_id", "weld_consecutive_shapes",
                                             "break_contour_apart", "inspect_contours", "duplicate_shape_by_id",
                                             "duplicate_shapes_by_id", "convert_text_to_curves"} & listed13)

# E07+E06: layers, transform/arrange, delete, group/PowerClip, create, import (exact IDs, one undo step) --------
E7 = Path(FX) / "e07"
for leftover in call("list_documents")["data"]["documents"]:
    if str(leftover.get("document_path") or "").lower().startswith(str(E7).lower() + "\\"):
        call("close_document", document_path=leftover["document_path"], discard_changes=True)
shutil.rmtree(E7, ignore_errors=True)
E7.mkdir(parents=True, exist_ok=True)
F7 = {}
for stem in ("layers", "colors", "structure", "effects", "text_runs"):
    shutil.copyfile(TEMPLATES / f"{stem}.cdr", E7 / f"{stem}.cdr")
    F7[stem] = str(E7 / f"{stem}.cdr")
    assert call("open_document", path=F7[stem])["success"], stem
LY, C7, S7, X7, T7 = (F7[k] for k in ("layers", "colors", "structure", "effects", "text_runs"))


def layers_of(path, page=1):
    r = call("list_layers", document_path=path, pages=[page])
    assert r["success"], r
    return {(x["name"], x["master"]): x for x in r["data"]["pages"][0]["layers"]}


def layer_state(path, name, page=1, master=False):
    return layers_of(path, page)[(name, master)]


def boxes(r):
    return {x["id"]: x["bounds"] for x in (r.get("data") or {}).get("shapes", [])}


def near(a, b, tol=1e-4):
    return a is not None and b is not None and len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def stack_of(path, layer, page=1):
    """Top-level IDs of a layer, front first (inspect_shapes z_order)."""
    r = call("inspect_shapes", document_path=path, page_index=page, layer_name=layer, fields=["z_order"])
    return [s["shape_id"] for s in sorted((s for s in r["data"]["shapes"] if not s.get("parent_id")),
                                          key=lambda s: s["z_order"])]


# list_layers
r = call("list_layers", document_path=LY)
d = r.get("data") or {}
p1 = {(x["name"], x["master"]): x for x in d["pages"][0]["layers"]} if r["success"] else {}
check("list_layers: every page, flags, master, counts, readable color", r["success"] and len(d["pages"]) == 3
      and p1[("Locked", False)]["locked"] and not p1[("Halftone", False)]["visible"]
      and p1[("Halftone", False)]["printable"] and p1[("Master Marks", True)]["objects"] == 1
      and all(str(x["color"]).startswith("#") for x in p1.values()) and d["pages"][0]["page_name"] == "Front"
      and d["dirty"] is False and d["active_layer"]["name"], r.get("error") or p1.get(("Master Marks", True)))
names = [x["name"] for x in d["pages"][0]["layers"]] if r["success"] else []
check("list_layers: stacking order, front first (newer local layers above older; masters where they stack)",
      names.index("Locked") < names.index("Art") < names.index("Master Marks"), names)
r = call("list_layers", document_path=LY, pages=[2])
p2 = {x["name"]: x for x in r["data"]["pages"][0]["layers"]} if r["success"] else {}
check("list_layers: pages=[2] only, page-specific flags", r["success"] and len(r["data"]["pages"]) == 1
      and not p2["Ink A"]["visible"] and not p2["Ink A"]["printable"], r.get("error"))
r = call("list_layers", document_path=LY, pages=[4])
check("list_layers: out-of-range page refused", not r["success"], r.get("error"))
grp = layer_state(S7, "Groups")
n_groups = len(call("inspect_shapes", document_path=S7, page_index=1, layer_name="Groups")["data"]["shapes"])
check("list_layers: objects (top level) and objects_total (recursive) from the cache", grp["objects"] == 2
      and grp["objects_total"] == n_groups == 12, (grp, n_groups))

# set_layer_properties
r = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Art", printable=False)
d = r.get("data") or {}
check("set_layer_properties: printable=false read back, dirty reported", r["success"] and d["layer"]["printable"] is False
      and d["changed"] == ["printable"] and d["dirty"] is True and d["dirty_before"] is False
      and layer_state(LY, "Art")["printable"] is False, r.get("error") or d)
call("undo", document_path=LY)
check("set_layer_properties: undo restores printable", layer_state(LY, "Art")["printable"] is True)
r = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Art", visible=False, editable=False,
         rename="Art 2")
st = layers_of(LY)
check("set_layer_properties: rename + visible + editable in one call", r["success"] and ("Art 2", False) in st
      and not st[("Art 2", False)]["visible"] and st[("Art 2", False)]["locked"], r.get("error"))
call("undo", document_path=LY)
st = layers_of(LY)
check("set_layer_properties: one undo step restores name and flags", ("Art", False) in st
      and st[("Art", False)]["visible"] and not st[("Art", False)]["locked"], list(st))
r = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Master Marks", visible=False)
check("set_layer_properties: a master layer needs master=true", not r["success"] and "master=true" in r["error"]
      and layer_state(LY, "Master Marks", master=True)["visible"], r.get("error"))
r = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Master Marks", visible=False, master=True)
check("set_layer_properties: master=true changes the master layer on every page", r["success"]
      and r["data"]["applies_to"] == "every page"
      and not any(layer_state(LY, "Master Marks", page=p, master=True)["visible"] for p in (1, 2, 3)), r.get("error"))
call("undo", document_path=LY)
r1 = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Art", rename="Ink A")
r2 = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Guides", rename="G")
r3 = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Ink B", visible=False)
r4 = call("set_layer_properties", document_path=LY, page_index=1, layer_name="Art")
check("set_layer_properties: duplicate name, special-layer rename, layer of another page, no change: refused",
      not any(x["success"] for x in (r1, r2, r3, r4)) and "already exists" in r1["error"] and "special" in r2["error"]
      and "Ink B" in r3["error"], [x.get("error") for x in (r1, r2, r3, r4)])

# create_layer
before_active = call("list_layers", document_path=LY)["data"]["active_layer"]
r = call("create_layer", document_path=LY, page_index=2, name="Cut", color="#FF0000", printable=False)
p2 = layers_of(LY, 2)
after_active = call("list_layers", document_path=LY)["data"]["active_layer"]
check("create_layer: on an exact (non-active) page, on top, color and flags read back, active layer kept",
      r["success"] and ("Cut", False) in p2 and p2[("Cut", False)]["color"] == "#FF0000"
      and not p2[("Cut", False)]["printable"] and ("Cut", False) not in layers_of(LY, 1)
      and before_active == after_active, r.get("error") or (before_active, after_active))
call("undo", document_path=LY)
check("create_layer: undo removes it", ("Cut", False) not in layers_of(LY, 2))
r = call("create_layer", document_path=LY, page_index=1, name="Art")
check("create_layer: an existing name on the page is refused", not r["success"] and "already exists" in r["error"])
r = call("create_layer", document_path=LY, page_index=1, name="Shared Marks", master=True)
check("create_layer: master=true appears on every page", r["success"]
      and all(("Shared Marks", True) in layers_of(LY, p) for p in (1, 2, 3)), r.get("error"))
call("undo", document_path=LY)

# move_shapes_to_layer (colors.cdr: Outlines above Process above Spots; 6 is above 4 on Spots)
base7 = snapshot(C7)
active0 = call("list_layers", document_path=C7)["data"]["active_layer"]
r = call("move_shapes_to_layer", document_path=C7, page_index=1, shape_ids=["4", "13", "6"], layer_name="Outlines")
check("move_shapes_to_layer: to the top, global stacking order kept (13 over 6 over 4), active layer unchanged",
      r["success"] and stack_of(C7, "Outlines")[:3] == ["13", "6", "4"]
      and [x["index"] for x in r["data"]["shapes"]] == [3, 1, 2]
      and call("list_layers", document_path=C7)["data"]["active_layer"] == active0, r.get("error") or r.get("data"))
call("undo", document_path=C7)
check("move_shapes_to_layer: undo restores layers and order", snapshot(C7) == base7)
r = call("move_shapes_to_layer", document_path=C7, page_index=1, shape_ids=["4", "13", "6"], layer_name="Outlines",
         position="bottom")
check("move_shapes_to_layer: to the bottom in order", r["success"] and stack_of(C7, "Outlines")[-3:] == ["13", "6", "4"],
      r.get("error"))
call("undo", document_path=C7)
r = call("move_shapes_to_layer", document_path=LY, page_index=1, shape_ids=["16"], layer_name="Art")
check("move_shapes_to_layer: from a hidden layer (CorelDRAW ignores those moves); the layer stays hidden",
      r["success"] and stack_of(LY, "Art")[0] == "16" and not layer_state(LY, "Halftone")["visible"], r.get("error"))
call("undo", document_path=LY)
la7 = snapshot(LY)
r1 = call("move_shapes_to_layer", document_path=LY, page_index=1, shape_ids=["14"], layer_name="Ink B")
r2 = call("move_shapes_to_layer", document_path=LY, page_index=1, shape_ids=["14"], layer_name="Master Marks")
r3 = call("move_shapes_to_layer", document_path=LY, page_index=1, shape_ids=["14"], layer_name="Locked")
r4 = call("move_shapes_to_layer", document_path=LY, page_index=1, shape_ids=["17", "14"], layer_name="Art")
check("move_shapes_to_layer: another page's layer, a master layer, a locked target, a locked source: refused",
      not any(x["success"] for x in (r1, r2, r3, r4)) and "another page" in r1["error"] and "master" in r2["error"]
      and "locked" in r3["error"] and "locked" in refused_reasons(r4).get("17", "") and snapshot(LY) == la7,
      [x.get("error") for x in (r1, r2, r3, r4)])
r1 = call("move_shapes_to_layer", document_path=S7, page_index=1, shape_ids=["4"], layer_name="PowerClip")
r2 = call("move_shapes_to_layer", document_path=X7, page_index=1, shape_ids=["5"], layer_name="Overprint")
check("move_shapes_to_layer: a group member and an effect-generated object refused per object",
      "group" in refused_reasons(r1).get("4", "") and "effect" in refused_reasons(r2).get("5", ""),
      (refused_reasons(r1), refused_reasons(r2)))

# transform_shapes (colors.cdr is in inches; 4 is [1, 9, 2, 10])
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["4"], move_to={"x": 50, "y": 100},
         anchor="bottom_left", unit="mm")
check("transform_shapes: move_to in mm on an inch document, bottom_left anchor", r["success"]
      and near(boxes(r)["4"], [50, 100, 75.4, 125.4]) and r["data"]["unit"] == "mm", r.get("error") or boxes(r))
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["4"], move_to={"x": 3}, anchor="top_right",
         unit="in")
check("transform_shapes: move_to x only, top_right anchor (y kept)", r["success"]
      and near(boxes(r)["4"], [2, 100 / 25.4, 3, 125.4 / 25.4]), boxes(r))
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["4"], move_by={"dx": 36, "dy": -72}, unit="pt")
check("transform_shapes: move_by in pt (bounds reported in pt)", r["success"] and r["data"]["unit"] == "pt"
      and near([v / 72 for v in boxes(r)["4"]], [2.5, 100 / 25.4 - 1, 3.5, 125.4 / 25.4 - 1]), boxes(r))
for _ in range(3):
    call("undo", document_path=C7)
check("transform_shapes: three moves, three undos restore the page", snapshot(C7) == base7)
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["5"], size={"width": 2}, anchor="top_left",
         unit="in")
check("transform_shapes: size width only keeps the aspect ratio about the top_left anchor", r["success"]
      and near(boxes(r)["5"], [2.2, 8, 4.2, 10]), boxes(r))
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["6"], scale={"x": 2, "y": 0.5})
check("transform_shapes: scale x2 / y0.5 about the center (no unit needed)", r["success"]
      and near(boxes(r)["6"], [2.9, 9.25, 4.9, 9.75]), boxes(r))
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["7"], size={"width": 1, "height": 2},
         unit="in", anchor="bottom_center")
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["7"], rotate=90)
check("transform_shapes: rotate 90 about the center swaps the box; rotation_angle read back", r["success"]
      and near(boxes(r)["7"], [4.1, 9.5, 6.1, 10.5]) and r["data"]["shapes"][0]["rotation_angle"] == 90, r.get("data"))
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["8", "9"], scale=2, anchor="bottom_left",
         as_group=True)
check("transform_shapes: as_group scales the pair about their combined bottom_left", r["success"]
      and near(r["data"]["group_bounds"], [1, 7.5, 5.4, 9.5]) and near(boxes(r)["9"], [3.4, 7.5, 5.4, 9.5]),
      r.get("data"))
for _ in range(5):
    call("undo", document_path=C7)
check("transform_shapes: undo after size, scale, size, rotate, group scale restores the page", snapshot(C7) == base7)
r = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["10", "11"], size={"height": 0.5}, rotate=45,
         move_to={"x": 4, "y": 4}, anchor="center", unit="in")
d = r.get("data") or {}
check("transform_shapes: size + rotate + move in one call, every center at move_to", r["success"]
      and all(near([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2], [4, 4]) for b in boxes(r).values()), r.get("error") or d)
call("undo", document_path=C7)
check("transform_shapes: that combined transform is one undo step", snapshot(C7) == base7)
r = call("transform_shapes", document_path=T7, page_index=1, shape_ids=["4"], scale=2, anchor="bottom_left")
b0, b1 = (r.get("data") or {}).get("shapes", [{}])[0].get("bounds_before"), boxes(r).get("4")
check("transform_shapes: artistic text scales with its characters", r["success"]
      and abs((b1[2] - b1[0]) - 2 * (b0[2] - b0[0])) < 1e-3, r.get("error") or (b0, b1))
call("undo", document_path=T7)
r = call("transform_shapes", document_path=S7, page_index=1, shape_ids=["15"], move_by={"dx": 0.5}, unit="in")
check("transform_shapes: a nested group moves as a whole", r["success"], r.get("error"))
call("undo", document_path=S7)
st7 = snapshot(S7)
r1 = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["4"], move_by={"dx": 1})
r2 = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["4"], move_by={"dx": 1}, move_to={"x": 1},
          unit="in")
r3 = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["4"])
r4 = call("transform_shapes", document_path=C7, page_index=1, shape_ids=["4"], scale=0)
check("transform_shapes: missing unit, move_to with move_by, no operation, zero scale: refused",
      not any(x["success"] for x in (r1, r2, r3, r4)) and "unit" in r1["error"], [x.get("error") for x in (r1, r2, r3, r4)])
r1 = call("transform_shapes", document_path=LY, page_index=1, shape_ids=["17", "14"], rotate=10)
r2 = call("transform_shapes", document_path=S7, page_index=1, shape_ids=["15", "14"], rotate=10)
r3 = call("transform_shapes", document_path=X7, page_index=1, shape_ids=["6", "5"], rotate=10)
r4 = call("transform_shapes", document_path=LY, page_index=1, shape_ids=["22"], rotate=10)
clip16 = next(s["shape_id"] for s in call("inspect_shapes", document_path=S7, page_index=1)["data"]["shapes"]
              if s.get("powerclip_container_id") == "21")
r5 = call("transform_shapes", document_path=S7, page_index=1, shape_ids=[clip16], rotate=10)
check("transform_shapes: locked, inside a targeted group, effect group, effect-generated, master, PowerClip content: "
      "refused per object, nothing changed",
      "locked" in refused_reasons(r1).get("17", "") and "14" not in refused_reasons(r1)
      and "also a target" in refused_reasons(r2).get("14", "") and "effect group" in refused_reasons(r3).get("6", "")
      and "effect" in refused_reasons(r3).get("5", "") and "master" in refused_reasons(r4).get("22", "")
      and "PowerClip" in refused_reasons(r5).get(clip16, "") and snapshot(LY) == la7 and snapshot(S7) == st7,
      [refused_reasons(x) for x in (r1, r2, r3, r4, r5)])

# arrange_shapes (Spots: 4 [1,9,2,10], 5 [2.2..], 6 [3.4..]; 9-11 one row lower)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4", "9", "13"], align="left")
check("arrange_shapes: align left to the selection", r["success"]
      and {b[0] for b in boxes(r).values()} == {1.0} and r["data"]["reference_bounds"][0] == 1.0, boxes(r))
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4", "5"], align="top", reference="page", unit="mm")
check("arrange_shapes: align top to the page, bounds in mm", r["success"]
      and all(abs(b[3] - 279.4) < 1e-3 for b in boxes(r).values()), boxes(r))
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4", "5"], align="middle", reference="9")
mid9 = 8.0
check("arrange_shapes: align middle to a reference shape (which stays)", r["success"]
      and all(abs((b[1] + b[3]) / 2 - mid9) < 1e-6 for b in boxes(r).values()), boxes(r))
call("undo", document_path=C7)
check("arrange_shapes: undo restores", snapshot(C7) == base7)
call("transform_shapes", document_path=C7, page_index=1, shape_ids=["5"], move_by={"dx": -0.9}, unit="in")
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["6", "4", "5"], distribute="horizontal")
b = boxes(r)
gaps = [round(b["5"][0] - b["4"][2], 6), round(b["6"][0] - b["5"][2], 6)] if r["success"] else []
check("arrange_shapes: distribute horizontal, equal gaps, outermost fixed", r["success"] and len(set(gaps)) == 1
      and b["4"][0] == 1.0 and b["6"][2] == 4.4, (gaps, b))
call("undo", document_path=C7)
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4", "9", "13"], distribute="vertical",
         spacing=0.25, unit="in")
b = boxes(r)
check("arrange_shapes: distribute vertical with a fixed 0.25 in gap from the top shape", r["success"]
      and abs(b["4"][1] - b["9"][3] - 0.25) < 1e-6 and abs(b["9"][1] - b["13"][3] - 0.25) < 1e-6
      and b["4"][3] == 10.0, b)
call("undo", document_path=C7)
check("arrange_shapes: undo after two distributions restores", snapshot(C7) == base7)
spots = stack_of(C7, "Spots")  # 12 front ... 4 back
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4", "6"], z_order="front")
z = {x["id"]: x for x in (r.get("data") or {}).get("shapes", [])}
check("arrange_shapes: z_order front keeps relative order (6 over 4), neighbours reported", r["success"]
      and stack_of(C7, "Spots")[:2] == ["6", "4"] and z["6"]["above"] is None and z["4"]["above"] == "6"
      and z["4"]["below"] == "12", z)
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["12", "10"], z_order="back")
check("arrange_shapes: z_order back keeps relative order", r["success"] and stack_of(C7, "Spots")[-2:] == ["12", "10"])
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["8"], z_order="forward")
z = r["data"]["shapes"][0] if r["success"] else {}
check("arrange_shapes: forward one step swaps with the neighbour above", r["success"] and z["above"] == "10"
      and z["below"] == "9" and stack_of(C7, "Spots") == ["12", "11", "10", "8", "9", "7", "6", "5", "4"], z)
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["11", "12"], z_order="backward")
check("arrange_shapes: backward one step for two adjacent shapes keeps them together",
      r["success"] and stack_of(C7, "Spots")[:3] == ["10", "12", "11"], stack_of(C7, "Spots"))
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["12", "4"], z_order="below", reference="8")
check("arrange_shapes: below a reference shape keeps relative order",
      r["success"] and stack_of(C7, "Spots") == ["11", "10", "9", "8", "12", "4", "7", "6", "5"], stack_of(C7, "Spots"))
call("undo", document_path=C7)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4"], z_order="above", reference="10")
check("arrange_shapes: above a reference shape", r["success"] and r["data"]["shapes"][0]["below"] == "10"
      and r["data"]["shapes"][0]["above"] == "11", r.get("data"))
call("undo", document_path=C7)
check("arrange_shapes: undo after every z_order restores", snapshot(C7) == base7 and stack_of(C7, "Spots") == spots)
r = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4"], z_order="above", reference="13")
check("arrange_shapes: above a shape of another layer is refused", not r["success"] and "same layer" in r["error"],
      r.get("error"))
r = call("arrange_shapes", document_path=S7, page_index=1, shape_ids=["5"], z_order="back")
check("arrange_shapes: z_order inside a group reorders its children", r["success"]
      and r["data"]["shapes"][0]["above"] == "4" and r["data"]["shapes"][0]["below"] is None, r.get("data"))
call("undo", document_path=S7)
r1 = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4"], align="left", z_order="front")
r2 = call("arrange_shapes", document_path=C7, page_index=1, shape_ids=["4"], z_order="above")
check("arrange_shapes: two operations, above without a reference: refused", not r1["success"] and not r2["success"])

# delete_shapes
r = call("delete_shapes", document_path=C7, page_index=1, shape_ids=["4", "13", "19"])
check("delete_shapes: three shapes on three layers in one call", r["success"]
      and not {"4", "13", "19"} & page_ids(C7) and r["data"]["deleted_ids"] == ["4", "13", "19"], r.get("error"))
call("undo", document_path=C7)
check("delete_shapes: one undo restores all three", snapshot(C7) == base7)
r = call("delete_shapes", document_path=S7, page_index=1, shape_ids=["6"])
check("delete_shapes: a group with its members", r["success"] and r["data"]["members_deleted"] == 2
      and not {"4", "5", "6"} & page_ids(S7), r.get("data"))
call("undo", document_path=S7)
r1 = call("delete_shapes", document_path=LY, page_index=1, shape_ids=["14", "17"])
r2 = call("delete_shapes", document_path=S7, page_index=1, shape_ids=["6", "4"])
r3 = call("delete_shapes", document_path=X7, page_index=1, shape_ids=["5"])
check("delete_shapes: locked, member of a targeted group, effect-generated: refused, nothing deleted",
      "locked" in refused_reasons(r1).get("17", "") and "also a target" in refused_reasons(r2).get("4", "")
      and "effect" in refused_reasons(r3).get("5", "") and snapshot(LY) == la7 and snapshot(S7) == st7)

# group_shapes / powerclip
r = call("group_shapes", document_path=C7, page_index=1, shape_ids=["5", "10", "7"], name="trio")
d = r.get("data") or {}
check("group_shapes: group in stacking order at the topmost member's place", r["success"]
      and d["member_ids"] == ["10", "7", "5"] and stack_of(C7, "Spots")[:3] == ["12", "11", d["group_id"]], d)
call("undo", document_path=C7)
check("group_shapes: undo", snapshot(C7) == base7)
r = call("group_shapes", document_path=C7, page_index=1, shape_ids=["5", "13"])
check("group_shapes: shapes on two layers refused", not r["success"] and refused_reasons(r), r.get("error"))
r = call("group_shapes", document_path=S7, page_index=1, shape_ids=["6"], action="ungroup")
check("group_shapes: ungroup gives the direct children", r["success"]
      and sorted(r["data"]["groups"][0]["child_ids"]) == ["4", "5"] and "6" not in page_ids(S7), r.get("data"))
call("undo", document_path=S7)
r = call("group_shapes", document_path=S7, page_index=1, shape_ids=["15"], action="ungroup_all")
check("group_shapes: ungroup_all dissolves nested groups", r["success"]
      and not {"15", "13", "11", "9"} & page_ids(S7) and {"7", "8", "10", "12", "14"} <= page_ids(S7), r.get("data"))
call("undo", document_path=S7)
check("group_shapes: undo restores the groups", snapshot(S7) == st7)
r = call("create_shape", document_path=C7, page_index=1, kind="ellipse", x=1, y=1, width=3, height=3, unit="in",
         layer="Spots")
cont = r["data"]["shape_id"] if r["success"] else None
r = call("powerclip", document_path=C7, page_index=1, container_id=cont, shape_ids=["4", "6"])
check("powerclip: place two shapes into a new container", r["success"]
      and sorted(r["data"]["content_ids"]) == ["4", "6"], r.get("error") or r.get("data"))
r = call("powerclip", document_path=C7, page_index=1, container_id=cont, action="extract")
check("powerclip: extract returns them", r["success"] and sorted(r["data"]["extracted_ids"]) == ["4", "6"],
      r.get("error") or r.get("data"))
for _ in range(3):
    call("undo", document_path=C7)
check("powerclip: create, place and extract undo in three steps", snapshot(C7) == base7)
inside21 = sorted(s["shape_id"] for s in call("inspect_shapes", document_path=S7, page_index=1)["data"]["shapes"]
                  if s.get("powerclip_container_id") == "21" and s.get("parent_id") == "21")
r = call("powerclip", document_path=S7, page_index=1, container_id="21", action="extract")
check("powerclip: extract a fixture container (rect, text, group)", r["success"] and len(inside21) == 3
      and sorted(r["data"]["extracted_ids"]) == inside21, (inside21, r.get("data")))
call("undo", document_path=S7)
check("powerclip: undo puts them back", snapshot(S7) == st7)
r = call("powerclip", document_path=S7, page_index=1, container_id="21", shape_ids=["4"])
check("powerclip: a group member is refused", not r["success"] and "group" in refused_reasons(r).get("4", ""))

# create_shape
r = call("create_shape", document_path=C7, page_index=1, kind="rectangle", x=10, y=20, width=30, height=15, unit="mm",
         layer="Process", fill="PANTONE 485 C", outline="none", corner_radius=2, name="mm_rect")
d = r.get("data") or {}
check("create_shape: rectangle in mm on an inch document, exact layer, spot fill, no outline", r["success"]
      and near(d["bounds"], [10, 20, 40, 35]) and d["layer"] == "Process" and d["fill"]["ink"] == "PANTONE 485 C"
      and d["outline"]["kind"] == "none", r.get("error") or d)
r = call("create_shape", document_path=C7, page_index=1, kind="ellipse", x=1, y=1, width=2, height=1, unit="in",
         outline="CMYK 0/0/0/100", outline_width=2, layer="Spots")
check("create_shape: ellipse bounds, outline color and width in the unit", r["success"]
      and near(r["data"]["bounds"], [1, 1, 3, 2]) and abs(r["data"]["outline"]["width_pt"] - 144) < 1e-3,
      r.get("error") or r.get("data"))
r = call("create_shape", document_path=C7, page_index=1, kind="line", x=72, y=72, x2=144, y2=0, unit="pt",
         layer="Spots")
check("create_shape: line from x,y to x2,y2 in pt", r["success"] and near(r["data"]["bounds"], [72, 0, 144, 72]),
      r.get("data"))
for _ in range(3):
    call("undo", document_path=C7)
check("create_shape: one undo step each", snapshot(C7) == base7)
r1 = call("create_shape", document_path=LY, page_index=1, kind="rectangle", x=1, y=1, width=1, height=1, unit="in",
          layer="Locked")
r2 = call("create_shape", document_path=LY, page_index=1, kind="rectangle", x=1, y=1, width=1, height=1, unit="in",
          layer="Ink B")
r3 = call("create_shape", document_path=LY, page_index=1, kind="line", x=1, y=1, width=1, height=1, unit="in")
r4 = call("create_shape", document_path=LY, page_index=1, kind="rectangle", x=1, y=1, width=1, height=1, unit="in",
          fill="PANTONE 9999 Q")
check("create_shape: locked layer, another page's layer, line with width, unknown spot: refused",
      not any(x["success"] for x in (r1, r2, r3, r4)) and "locked" in r1["error"] and snapshot(LY) == la7,
      [x.get("error") for x in (r1, r2, r3, r4)])
r = call("create_shape", document_path=LY, page_index=3, kind="rectangle", x=1, y=1, width=1, height=1, unit="in")
check("create_shape: default layer on a non-active page is that page's", r["success"]
      and r["data"]["layer"] in ("Ink B", "Ink A") and r["data"]["page_index"] == 3, r.get("data"))
call("undo", document_path=LY)

# import_file (SVG and PNG onto page 2 of layers.cdr; page 1 stays active)
svg = E7 / "box.svg"
svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50" viewBox="0 0 100 50">'
               '<rect x="0" y="0" width="60" height="50" fill="#ff0000"/>'
               '<circle cx="80" cy="25" r="20" fill="#0000ff"/></svg>', encoding="utf-8")
from PIL import Image  # noqa: E402

png = E7 / "green.png"
Image.new("RGB", (40, 20), (0, 200, 0)).save(png)
la2 = snapshot(LY, 2)
r = call("import_file", document_path=LY, page_index=2, path=str(svg), layer="Art", x=1, y=2, width=3, unit="in")
d = r.get("data") or {}
check("import_file: SVG onto an exact non-active page and layer, positioned and sized (aspect kept)", r["success"]
      and len(d["new_ids"]) == 1 and d["shapes"][0]["layer"] == "Art" and near(d["bounds"], [1, 2, 4, 3.5], 1e-3)
      and d["active_page"] == 1 and set(d["new_ids"]) <= page_ids(LY, 2), r.get("error") or d)
r2 = call("import_file", document_path=LY, page_index=2, path=str(png), layer="Ink A", height=10, unit="mm")
d2 = r2.get("data") or {}
check("import_file: PNG as a bitmap, height only keeps the aspect ratio", r2["success"]
      and d2["shapes"][0]["type"] == 5 and abs(d2["bounds"][3] - d2["bounds"][1] - 10) < 1e-3
      and abs(d2["bounds"][2] - d2["bounds"][0] - 20) < 1e-2, r2.get("error") or d2)
call("undo", document_path=LY)
call("undo", document_path=LY)
check("import_file: each import is one undo step", snapshot(LY, 2) == la2)
# (A malformed file is not tried here: CorelDRAW may answer it with a modal dialog on the shared instance.)
r1 = call("import_file", document_path=LY, page_index=2, path=str(E7 / "missing.svg"))
r2 = call("import_file", document_path=LY, page_index=1, path=str(svg), layer="Locked")
r3 = call("import_file", document_path=LY, page_index=2, path=str(svg), x=1)
check("import_file: missing file, locked layer, position without unit: refused, page unchanged",
      not any(x["success"] for x in (r1, r2, r3)) and snapshot(LY, 2) == la2 and snapshot(LY) == la7,
      [x.get("error") for x in (r1, r2, r3)])

for path in F7.values():
    r = call("close_document", document_path=path, discard_changes=True)
    check(f"cleanup: close {Path(path).name}", r["success"], r.get("error"))
listed7 = {tool.name for tool in asyncio.run(_catalog())}
check("catalog: E07 tools added, 25 legacy shape/layer tools removed",
      {"list_layers", "set_layer_properties", "create_layer", "move_shapes_to_layer", "transform_shapes",
       "arrange_shapes", "delete_shapes", "group_shapes", "powerclip", "create_shape", "import_file",
       "get_ui_selection"} <= listed7
      and not {"get_layers", "get_page_layers", "set_layer_visible", "lock_layer", "set_page_layer_state",
               "assign_to_layer", "set_shape_position", "set_shape_position_exact", "set_shape_size", "scale_shape",
               "rotate_shape", "align_shapes", "distribute_shapes", "set_z_order", "delete_shape",
               "delete_shape_by_id", "create_rectangle", "create_ellipse", "create_line", "import_svg",
               "import_image", "ungroup_shapes"} & listed7, sorted(listed7))

# 19. E19+E12: catalog diet and feedback quality ----------------------------------------------------------------
import catalog_size  # noqa: E402

summary19, rows19 = asyncio.run(catalog_size.measure())
check("catalog budget: list_tools within the budget in catalog_size.BUDGET", not catalog_size.over_budget(summary19),
      {k: summary19[k] for k in catalog_size.BUDGET} | {"problems": catalog_size.over_budget(summary19)})
check("catalog: no tool declares an output schema (the shared ToolResult schema is gone)",
      summary19["output_schema_chars"] == 0)
check("catalog: every description is English", not summary19["cjk_descriptions"], summary19["cjk_descriptions"])
check("catalog: server instructions carry the shared conventions",
      0 < summary19["instructions_chars"] and summary19["instructions_chars"] <= catalog_size.BUDGET["instructions_chars"])
hints19 = {r["name"]: r["annotations"] for r in rows19}
check("catalog: read-only tools are annotated readOnlyHint",
      all(hints19[name].get("read_only_hint") for name in ("list_documents", "inspect_shapes", "get_ink_inventory",
                                                            "list_layers", "get_print_separation_settings",
                                                            "get_coreldraw_status"))
      and not any(hints19[name].get("read_only_hint") for name in ("set_fill", "delete_shapes", "find_shapes")),
      hints19)
check("catalog: optional parameters are advertised without a null branch",
      all(
          "anyOf" not in prop or {"type": "null"} not in prop["anyOf"]
          for tool in asyncio.run(_catalog()) for prop in tool.input_schema.get("properties", {}).values()))

E19 = Path(FX) / "e19"
E19.mkdir(exist_ok=True)
LY19 = str(E19 / "layers.cdr")
shutil.copyfile(TEMPLATES / "layers.cdr", LY19)
r = call("open_document", path=LY19, activate=False)
check("feedback setup: open layers.cdr copy", r["success"], r.get("error"))
r = call("set_fill", document_path=LY19, page_index=1, shape_ids=["999999"], color=None, fountain=None,
         from_shape_id=None, text_range=None)
check("feedback: explicit nulls for optional parameters are still accepted (argument check runs)",
      not r["success"] and "exactly one" in r["error"], r.get("error"))
r = call("set_fill", document_path=LY19, page_index=1, shape_ids=["999999"], color="CMYK 0/0/0/100")
check("feedback: a missing shape ID names the page and the next step, with state and target in data",
      not r["success"] and "page 1" in r["error"] and "find_shapes" in r["error"]
      and r["data"] == {"state": "tool_error", "document_path": LY19, "page_index": 1}, r)
r = call("inspect_shapes", document_path=LY19, page_index=99)
check("feedback: an out-of-range page is a tool_error with the target", not r["success"]
      and r["data"]["state"] == "tool_error" and r["data"]["page_index"] == 99 and "page_index" in r["error"], r)
r = call("inspect_shapes", document_path=str(E19 / "not-open.cdr"), page_index=1)
check("feedback: a document that is not open points to list_documents",
      not r["success"] and "list_documents" in r["error"], r.get("error"))
r = call("list_documents")
entry19 = entry(r, LY19)
check("units: list_documents reports the unit as a name", entry19 and entry19["unit"] in ("in", "mm", "cm", "pt"),
      entry19 and entry19["unit"])
r = call("check_text_overflow", shape_id="nope", document_path="")
check("feedback: legacy text tools answer in English with a next step",
      not r["success"] and r["error"].isascii() and ("find_shapes" in r["error"] or "open_document" in r["error"]),
      r.get("error"))
r = call("close_document", document_path=LY19, discard_changes=True)
check("cleanup: close the E19 layers.cdr copy", r["success"], r.get("error"))

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
