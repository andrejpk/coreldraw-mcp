"""E13 probe 10: union cost on Good News p2 (a 200+ leaf group): DisplayCurve, WeldWith sequential vs tree, Contour."""
import json, sys, time, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path


def go():
    doc = open_mine(P)
    page = doc.Pages(2)
    out = {}
    layer = [l for l in page.Layers if l.Name == "Layer 1"][0]
    tops = [layer.Shapes.Item(i) for i in range(1, layer.Shapes.Count + 1)]
    out["tops"] = [(str(s.StaticID), s.Type, s.Shapes.Count if s.Type == 7 else 0) for s in tops]
    # pick the largest group; flatten leaves
    group = max((s for s in tops if s.Type == 7), key=lambda s: s.Shapes.Count)
    leaves = []

    def walk(g):
        for i in range(1, g.Shapes.Count + 1):
            c = g.Shapes.Item(i)
            if c.Type == 7:
                walk(c)
            else:
                leaves.append(c)
    t = time.perf_counter(); walk(group); out["walk_s"] = round(time.perf_counter() - t, 2)
    out["group"] = str(group.StaticID); out["leaves"] = len(leaves)
    out["types"] = sorted({s.Type for s in leaves})
    leaves = [s for s in leaves if s.Type != 6][:300]
    t = time.perf_counter()
    curves = []
    for s in leaves:
        try:
            curves.append(s.DisplayCurve)
        except Exception:
            pass
    out["displaycurve_s"] = round(time.perf_counter() - t, 2); out["curves"] = len(curves)

    def fresh(c):
        x = conn.app.CreateCurve(doc); x.AppendCurve(c); return x
    t = time.perf_counter()
    acc = fresh(curves[0])
    for c in curves[1:]:
        r = acc.WeldWith(c)
        acc = r if r is not None else acc
    out["seq_s"] = round(time.perf_counter() - t, 2); out["seq_area"] = acc.Area; out["seq_sp"] = acc.SubPaths.Count
    out["inplace_returns_self"] = None
    t = time.perf_counter()
    level = [fresh(c) for c in curves]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level) - 1, 2):
            r = level[i].WeldWith(level[i + 1])
            nxt.append(r if r is not None else level[i])
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    out["tree_s"] = round(time.perf_counter() - t, 2); out["tree_area"] = level[0].Area; out["tree_sp"] = level[0].SubPaths.Count
    t = time.perf_counter()
    c2 = level[0].Contour(0.75 / 72, 0, 2, 4, 15)
    out["contour_s"] = round(time.perf_counter() - t, 2); out["contour_sp"] = c2.SubPaths.Count if c2 else None
    # in-place semantics
    a = fresh(curves[0]); b = fresh(curves[1]); before = a.Area
    r = a.WeldWith(b)
    out["weldwith"] = {"ret_none": r is None, "a_area_before": before, "a_area_after": a.Area,
                       "ret_area": r.Area if r is not None else None}
    return out


if __name__ == "__main__":
    P = r"C:\CorelMcpLab\runs\e13\samples\goodnews.cdr"
    try:
        print(json.dumps(run(go), default=str))
    finally:
        close_path(P)
