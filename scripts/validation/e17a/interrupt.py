"""Timeout facts on real CorelDRAW: what happens to a read past its limit (in-process, short limits)."""
import sys, time, json; sys.path.insert(0, ".")
from core import connection
from core.drawing import walk_shapes
from core.targets import resolve_document
from tools import inspection
conn = connection.get_connection()
GN = r"C:\CorelMcpLab\runs\e17a\s\53210 - ME-MI Good News Club - SCREENS.cdr"
worker = conn._com_thread
out = {}

def many_com_calls():  # the old list_all_text_shapes pattern: one COM read per object
    doc, _ = resolve_document(conn.app, GN)
    n = 0
    for shape, _ in walk_shapes(doc.Pages(1).Shapes):
        shape.Type; n += 1
    return n

def one_long_com_call():  # one Evaluate that runs ~3 s inside CorelDRAW (paths repeated per property)
    doc, _ = resolve_document(conn.app, GN)
    layer = [l for l in doc.Pages(1).AllLayers if l.Name == "Layer 1"][0]
    anchor = layer.Shapes.Item(1)
    q = []
    for k in range(5000, 5250):
        q += inspection._record_query(inspection._query_path((1, k)), None)[0]
    t = time.perf_counter(); anchor.Evaluate("array(" + ", ".join(q) + ")"); return time.perf_counter() - t

for name, func, limit in (("many_com_calls", many_com_calls, 3.0), ("one_long_com_call", one_long_com_call, 1.0)):
    t0 = time.perf_counter()
    try:
        worker.call(lambda f=func: conn._execute(f, (), {}, read_only=True), timeout=limit, read_only=True, name=name)
        res = "finished"
    except connection._BridgeError as e:
        res = f"{e.state}: {e}"
    returned = time.perf_counter() - t0
    while worker.running() is not None:
        time.sleep(0.01)
    free = time.perf_counter() - t0
    probe = conn.probe()
    out[name] = {"limit_s": limit, "caller_returned_s": round(returned, 2), "worker_free_s": round(free, 2),
                 "result": res[:200], "next_probe": probe["state"] if not probe["success"] else "ready"}
# a mutating operation past its limit is left running
def slow_edit():
    time.sleep(2.5); return "done"
t0 = time.perf_counter()
try:
    worker.call(lambda: conn._execute(slow_edit, (), {}), timeout=1.0, name="slow_edit")
except connection._BridgeError as e:
    res = str(e)
during = conn.probe()
while worker.running() is not None: time.sleep(0.01)
out["mutating"] = {"result": res[:200], "status_during": during.get("error"), "worker_free_s": round(time.perf_counter() - t0, 2)}
print(json.dumps(out, indent=1))
