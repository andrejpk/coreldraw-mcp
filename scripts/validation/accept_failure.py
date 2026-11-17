"""In-process failure-path checks: layer and hidden-printable proof restore, render_plates without Ghostscript, rollback of
geometry edits that fail part way (combine_shapes, offset_silhouette), and why edits activate their document (Undo)."""
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


def union_then_fail(app, doc, layer, participants, sources):
    original_union(app, doc, layer, participants, sources)  # copies and the temporary union shape exist now
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
