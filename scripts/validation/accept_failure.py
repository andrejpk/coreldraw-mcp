"""In-process failure-path checks: layer proof restores state when publishing fails; weld rolls back a partial weld."""
import json, sys
sys.path.insert(0, ".")
from core.connection import get_connection
from tools import geometry, proof
conn = get_connection()
FX = sys.argv[1].replace("/", "\\") + r"\fx"
DOC = FX + r"\accept.cdr"
IDS = json.loads(sys.argv[2])


def state():
    def run():
        doc = conn.app.ActiveDocument
        s = doc.PDFSettings
        return {"pdf": {n: getattr(s, n) for n in ("PublishRange", "PageRange", "ColorMode", "Overprints")},
                "dirty": doc.Dirty, "page": doc.ActivePage.Index, "unit": doc.Unit,
                "layers": [(l.Name, bool(l.Printable)) for p in (1, 2) for l in doc.Pages(p).AllLayers]}
    return conn.safe_call(run)["result"]


s0 = state()


def failing_publish(doc, page_index, destination):
    # Fail after the layers were switched, inside the proof attempt.
    printable = [l.Name for l in doc.Pages(page_index).AllLayers if l.Printable]
    raise RuntimeError(f"simulated publish failure; printable during attempt: {printable}")


original = proof._publish_page
proof._publish_page = failing_publish
try:
    r = proof.export_layer_proof_pdf(DOC, 1, "Ink B", FX + r"\never.pdf")
finally:
    proof._publish_page = original
print("PASS" if not r.success and "simulated" in r.error else "FAIL", "layer proof reports the failure:", r.error)
print("PASS" if state() == s0 else "FAIL", "layer flags, dirty flag, PDF settings restored after failure")


# The weld rollback depends on detecting a partial weld and on Undo(1) restoring the sources.
def partial():
    doc = conn.app.ActiveDocument
    layer = [l for l in doc.Pages(1).Layers if l.Name == "Weld"][0]
    find = lambda i: [x for x in layer.Shapes if str(x.StaticID) == IDS[i]][0]  # noqa: E731
    c1, c2, c3 = find("c1"), find("c2"), find("c3")
    doc.BeginCommandGroup("Weld consecutive shapes")
    result = c2.Weld(c1, False, False)
    doc.EndCommandGroup()
    result_id = str(result.StaticID)
    survivors = geometry._surviving(layer, [IDS["c1"], IDS["c2"], IDS["c3"]])
    doc.Undo(1)
    restored = geometry._surviving(layer, [IDS["c1"], IDS["c2"], IDS["c3"]])
    return {"after_partial": sorted(survivors), "after_undo": sorted(restored), "result": result_id}


r = conn.safe_call(partial); print(r); r = r["result"]
ok = r["after_partial"] == [IDS["c3"]] and r["after_undo"] == sorted([IDS["c1"], IDS["c2"], IDS["c3"]])
print("PASS" if ok else "FAIL", "rollback primitive: _surviving detects a partial weld and Undo(1) restores sources", r)
