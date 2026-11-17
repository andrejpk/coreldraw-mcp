"""In-process failure-path checks: layer and hidden-printable proof restore, render_plates without Ghostscript, rollback of
geometry edits that fail part way (combine_shapes, offset_silhouette after the union, after the contour, and when its
time budget runs out), E13b refusals (locked layer, mixed pages, effect-generated input) on copies of layers.cdr and
effects.cdr, and why edits activate their document (Undo)."""
import json, sys
sys.path.insert(0, ".")
from core.connection import get_connection
from core.targets import activate_document, find_documents
from tools import geometry, proof
conn = get_connection()
FX = sys.argv[1].replace("/", "\\") + r"\fx"
DOC = FX + r"\accept.cdr"
IDS = json.loads(sys.argv[2])


def fixture():
    """accept.cdr by exact path, made active (CorelDRAW applies Undo to the active document)."""
    doc, = find_documents(conn.app, DOC)
    activate_document(conn.app, doc)
    return doc


def state():
    def run():
        doc = fixture()
        s = doc.PDFSettings
        return {"pdf": {n: getattr(s, n) for n in ("PublishRange", "PageRange", "ColorMode", "Overprints")},
                "dirty": doc.Dirty, "page": doc.ActivePage.Index, "unit": doc.Unit,
                "layers": [(l.Name, bool(l.Printable), bool(l.Visible)) for p in (1, 2) for l in doc.Pages(p).AllLayers]}
    return conn.safe_call(run)["result"]


s0 = state()


def failing_publish(doc, page_index, destination):
    # Fail after the layers were switched, inside the proof attempt.
    printable = [l.Name for l in doc.Pages(page_index).AllLayers if l.Printable]
    raise RuntimeError(f"simulated publish failure; printable during attempt: {printable}")


original = proof._publish_page
proof._publish_page = failing_publish
try:
    r = proof.export_page_proof_pdf(DOC, 1, FX + r"\never.pdf", layer_name="Ink B")
finally:
    proof._publish_page = original
print("PASS" if not r.success and "simulated" in r.error else "FAIL", "layer proof reports the failure:", r.error)
print("PASS" if state() == s0 else "FAIL", "layer flags, dirty flag, PDF settings restored after failure")

# Same for a whole-page proof that makes hidden-but-printable layers visible ("Hidden C" on page 1).
proof._publish_page = failing_publish
try:
    r = proof.export_page_proof_pdf(DOC, 1, FX + r"\never2.pdf", include_hidden_printable=True)
finally:
    proof._publish_page = original
print("PASS" if not r.success and "simulated" in r.error and state() == s0 else "FAIL",
      "hidden-printable proof: failure reported and layer visibility restored:", r.error)


# render_plates fails clearly without Ghostscript, before touching CorelDRAW.
import os  # noqa: E402
from tools import plates  # noqa: E402
os.environ["GHOSTSCRIPT_EXE"] = FX + r"\no-such-gswin64c.exe"
try:
    r = plates.render_plates(DOC, 1, FX + r"\never-render")
finally:
    del os.environ["GHOSTSCRIPT_EXE"]
print("PASS" if not r.success and "GHOSTSCRIPT_EXE" in r.error and not os.path.exists(FX + r"\never-render") else "FAIL",
      "render_plates without Ghostscript fails clearly and writes nothing:", r.error)


# E13: a geometry edit that fails part way is undone as one step (core.shapeops.undoable): the sources come back,
# and an earlier edit in the same document is not undone with it.
def layer_ids(name):
    def run():
        doc = fixture()
        layer = [l for l in doc.Pages(1).Layers if l.Name == name][0]
        return sorted(str(x.StaticID) for x in layer.Shapes)
    return conn.safe_call(run)["result"]


def earlier_edit():
    doc = fixture()
    layer = [l for l in doc.Pages(1).Layers if l.Name == "Ink B"][0]
    return str(layer.CreateRectangle2(150, 150, 5, 5).StaticID)


marker = conn.safe_call(earlier_edit)["result"]
weld_before = layer_ids("Weld")


def failing_weld(shapes, ids, keep, layer, order):
    stack = order["stack"]
    shapes[stack[1]].Weld(shapes[stack[0]], False, False)  # a partial weld really happens
    raise RuntimeError("simulated failure after a partial weld")


original_weld = geometry._OPERATIONS["weld"]
geometry._OPERATIONS["weld"] = failing_weld
try:
    r = geometry.combine_shapes(DOC, 1, [IDS["c2"], IDS["c3"], IDS["c1"]], "weld")
finally:
    geometry._OPERATIONS["weld"] = original_weld
ok = (not r.success and "simulated" in r.error and "page is unchanged" in r.error
      and layer_ids("Weld") == weld_before and marker in layer_ids("Ink B"))
print("PASS" if ok else "FAIL", "combine_shapes: a failure after a partial weld is undone; an earlier edit survives:",
      r.error, layer_ids("Weld"), weld_before)


def failing_contour(*args, **kwargs):
    raise RuntimeError("simulated failure after the copies were made")


from tools import silhouette  # noqa: E402
original_union = silhouette._union


def union_then_fail(*args, **kwargs):
    original_union(*args, **kwargs)  # copies and the welded temporary exist now
    failing_contour()


silhouette._union = union_then_fail
try:
    r = silhouette.offset_silhouette(DOC, 1, [IDS["c1"], IDS["b"]], 1.0, unit="mm", target_layer="Ink B")
finally:
    silhouette._union = original_union
ok = (not r.success and "simulated" in r.error and "page is unchanged" in r.error
      and layer_ids("Weld") == weld_before and marker in layer_ids("Ink B"))
print("PASS" if ok else "FAIL", "offset_silhouette: a failure after the temporary copies is undone; an earlier edit "
      "survives:", r.error)


def remove_marker():
    doc = fixture()
    layer = [l for l in doc.Pages(1).Layers if l.Name == "Ink B"][0]
    [x for x in layer.Shapes if str(x.StaticID) == marker][0].Delete()


conn.safe_call(remove_marker)


# E13b: refusals before any change (locked layer, mixed pages, effect-generated input) and rollbacks part way,
# on fresh copies of layers.cdr and effects.cdr.
import shutil  # noqa: E402
from core.structure import page_layers  # noqa: E402
from tools import effects  # noqa: E402

GEO = {}
for stem in ("layers", "effects"):
    GEO[stem] = FX + rf"\e13b_fail_{stem}.cdr"
    shutil.copyfile(rf"C:\CorelMcpLab\templates\{stem}.cdr", GEO[stem])
    conn.safe_call(lambda path=GEO[stem]: conn.app.OpenDocument(path))
LA, EF = GEO["layers"], GEO["effects"]


def ids_of(path, page=1):
    def run():
        doc, = find_documents(conn.app, path)
        return sorted(e.shape_id for v in page_layers(doc, doc.Pages(page), fresh=True) for e in v.structure.entries)
    return conn.safe_call(run)["result"]


def report(ok, label, detail):
    print("PASS" if ok else "FAIL", label, detail)


def refused(r):
    return {x["id"]: x["reason"] for x in (r.data or {}).get("refused", [])}


la0, ef0 = ids_of(LA), ids_of(EF)
r1 = silhouette.offset_silhouette(LA, 1, ["17", "14"], 1.0, target_layer="Art", keep_sources=False)
r2 = silhouette.offset_silhouette(LA, 1, ["14"], 1.0, target_layer="Locked")
r3 = geometry.combine_shapes(LA, 1, ["17", "14"], "weld", preserve_order=False)
r4 = geometry.duplicate_shapes(LA, 1, ["17"])
r5 = geometry.convert_to_curves(LA, 1, ["17"])
report(not any(r.success for r in (r1, r2, r3, r4, r5)) and "locked" in refused(r1).get("17", "")
       and "locked" in r2.error and "locked" in refused(r3).get("17", "") and "locked" in refused(r4).get("17", "")
       and "locked" in refused(r5).get("17", "") and ids_of(LA) == la0,
       "locked layer: offset_silhouette (consume, target), combine_shapes, duplicate_shapes, convert_to_curves refuse; "
       "page unchanged:", [r.error for r in (r1, r2, r3, r4, r5)])

r1 = silhouette.offset_silhouette(LA, 1, ["14", "18"], 1.0)
r2 = geometry.combine_shapes(LA, 1, ["14", "18"], "weld", preserve_order=False)
r3 = geometry.duplicate_shapes(LA, 1, ["14", "21"])
report(not any(r.success for r in (r1, r2, r3)) and all("18" in r.error or "21" in r.error for r in (r1, r2, r3))
       and ids_of(LA) == la0,
       "mixed pages: IDs from pages 2 and 3 are not found on page 1; nothing changed:", [r.error for r in (r1, r2, r3)])

r1 = silhouette.offset_silhouette(EF, 1, ["5"], 1.0)
r2 = geometry.combine_shapes(EF, 1, ["5", "13"], "weld", preserve_order=False)
r3 = geometry.convert_to_curves(EF, 1, ["5"])
r4 = effects.apply_contour(EF, 1, ["6"], "outside", 1.0)
r5 = geometry.duplicate_shapes(EF, 1, ["5"])
report(not any(r.success for r in (r1, r2, r3, r4, r5)) and "effect" in refused(r1).get("5", "")
       and "effect" in refused(r2).get("5", "") and "effect" in refused(r3).get("5", "")
       and "effect" in refused(r4).get("6", "") and "effect" in refused(r5).get("5", "") and ids_of(EF) == ef0,
       "effect-generated input (contour step 5, contour group 6) refused by every geometry tool; page unchanged:",
       [r.error for r in (r1, r2, r3, r4, r5)])


# Rollback part way: the contour is created and separated, then a failure; and the time budget running out.
original_contour = silhouette._contour


def contour_then_fail(*args, **kwargs):
    original_contour(*args, **kwargs)
    raise RuntimeError("simulated failure after the contour was separated")


silhouette._contour = contour_then_fail
try:
    r = silhouette.offset_silhouette(LA, 1, ["14", "15"], 1.0, target_layer="Art")
finally:
    silhouette._contour = original_contour
report(not r.success and "simulated" in r.error and "page is unchanged" in r.error and ids_of(LA) == la0,
       "offset_silhouette: a failure after the contour step is undone:", r.error)

original_budget = silhouette._BUDGET_SECONDS
silhouette._BUDGET_SECONDS = 0.0
try:
    r = silhouette.offset_silhouette(LA, 1, ["14", "15"], 1.0, target_layer="Art")
finally:
    silhouette._BUDGET_SECONDS = original_budget
report(not r.success and "Stopped after" in r.error and "page is unchanged" in r.error and ids_of(LA) == la0,
       "offset_silhouette: running out of the time budget stops and undoes the edit:", r.error)

r = effects.separate_effect(EF, 1, ["4", "7"], effect="contour")


def undo_ef():
    doc, = find_documents(conn.app, EF)
    activate_document(conn.app, doc)
    doc.Undo(1)


conn.safe_call(undo_ef)
report(r.success and ids_of(EF) == ef0, "separate_effect on two controls of one layer, then one undo restores:",
       r.error or [x["result_ids"] for x in r.data["shapes"]])


def close_geo():
    for path in GEO.values():
        for doc in find_documents(conn.app, path):
            doc.Dirty = False
            doc.Close()


conn.safe_call(close_geo)


# Edits activate their document because CorelDRAW applies Document.Undo to the active document.
def undo_follows_active():
    import shutil
    paths = [FX + r"\e08_undo_x.cdr", FX + r"\e08_undo_y.cdr"]
    for path in paths:
        shutil.copyfile(r"C:\CorelMcpLab\templates\colors.cdr", path)
    app = conn.app
    x, y = (app.OpenDocument(path) for path in paths)  # y is active
    try:
        layers = [[layer for layer in d.Pages(1).Layers if not layer.IsSpecialLayer][0] for d in (x, y)]
        counts = [layer.Shapes.Count for layer in layers]
        layers[0].CreateRectangle2(0, 0, 1, 1)  # x is inactive
        layers[1].CreateRectangle2(0, 0, 1, 1)
        x.Undo(1)  # called on x while y is active
        hazard = [layer.Shapes.Count - n for layer, n in zip(layers, counts)]
        activate_document(app, x)
        x.Undo(1)
        fixed = [layer.Shapes.Count - n for layer, n in zip(layers, counts)]
        return {"undo_on_inactive_x": hazard, "after_activating_x": fixed}
    finally:
        for d in (x, y):
            d.Dirty = False
            d.Close()


r = conn.safe_call(undo_follows_active)["result"]
print("PASS" if r == {"undo_on_inactive_x": [1, 0], "after_activating_x": [0, 0]} else "FAIL",
      "Document.Undo on an inactive document undoes the active one; activate_document makes it safe", r)
