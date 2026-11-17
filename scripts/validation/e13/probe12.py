"""E13 probe 12: union cost at scale (Good News p2 group 33721 leaves): sequential vs tree WeldWith, read-only."""
import json, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path


def go():
    doc = open_mine(P)
    page = doc.Pages(2)
    layer = [l for l in page.Layers if l.Name == "Layer 1"][0]
    group = [layer.Shapes.Item(i) for i in range(1, layer.Shapes.Count + 1) if str(layer.Shapes.Item(i).StaticID) == "33721"][0]
    leaves = []

    def walk(g):
        for i in range(1, g.Shapes.Count + 1):
            c = g.Shapes.Item(i)
            (walk if c.Type == 7 else leaves.append)(c)
    walk(group)
    out = {"leaves": len(leaves), "types": sorted({s.Type for s in leaves})}
    t = time.perf_counter()
    curves = [s.DisplayCurve for s in leaves if s.Type != 6]
    out["displaycurve_s"] = round(time.perf_counter() - t, 2)

    def tree(items, budget):
        t0 = time.perf_counter()
        level = list(items)
        while len(level) > 1:
            nxt = []
            for i in range(0, len(level) - 1, 2):
                if time.perf_counter() - t0 > budget:
                    return None, time.perf_counter() - t0
                r = level[i].WeldWith(level[i + 1])
                nxt.append(r if r is not None else level[i])
            if len(level) % 2:
                nxt.append(level[-1])
            level = nxt
        return level[0], time.perf_counter() - t0
    for n in (250, 500, 1000, len(curves)):
        u, s = tree(curves[:n], 40)
        out[f"tree_{n}"] = [round(s, 2), None if u is None else u.SubPaths.Count]
        if u is None:
            break
    t0 = time.perf_counter(); acc = curves[0]; done = 0
    for c in curves[1:500]:
        acc = acc.WeldWith(c); done += 1
        if time.perf_counter() - t0 > 30:
            break
    out["seq_500"] = [round(time.perf_counter() - t0, 2), done]
    return out


if __name__ == "__main__":
    P = fresh_copy(r"C:\CorelMcpLab\runs\e13\samples\goodnews.cdr", "probe12.cdr")
    try:
        r = conn._com_thread.call(lambda: conn._execute(go, (), {}), timeout=300, queue_timeout=2.0, name="lab")
        print(json.dumps(r, default=str))
    finally:
        close_path(P)
