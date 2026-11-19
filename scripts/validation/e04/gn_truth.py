"""Good News p1 duplicate ground truth: candidates from inspect_shapes (bbox + colors, coarse), confirmed by direct
COM node reads with 0.001 mm tolerance (the per-shape direct walk of 16.8k shapes exceeds 15 min)."""
import sys
from collections import defaultdict
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run_long
from core.structure import resolve_shapes
from tools.inspection import inspect_shapes
from tools.find import find_shapes
from groundtruth import paint
path = WORK + r"\goodnews.cdr"
TOL = 0.001 / 25.4
shapes = [s for s in inspect_shapes(path, 1).data["shapes"] if s["layer"]["printable"] and s.get("type") not in (7, 9, 13, 16)
          and "effect_group_id" not in s]
buckets = defaultdict(list)
for s in shapes:
    b = s["bounds"]
    buckets[(s["type"], round(b["left"], 3), round(b["bottom"], 3), round(b["width"], 3), round(b["height"], 3))].append(s["shape_id"])
cands = [ids for ids in buckets.values() if len(ids) > 1]
print("coarse candidate groups", len(cands), "shapes", sum(map(len, cands)))
def go():
    from lab import open_mine
    doc = open_mine(path); page = doc.Pages(1)
    extras = {}
    for ids in cands:
        objs = resolve_shapes(doc, page, ids)
        info = []
        for sid, o in zip(ids, objs):
            nodes = sorted((n.PositionX, n.PositionY) for n in o.DisplayCurve.Nodes) if o.Type != 6 else []
            info.append((sid, (o.LeftX, o.BottomY, o.RightX, o.TopY), str(paint(o)), nodes))
        clusters = []
        for item in info:
            for cl in clusters:
                f = cl[0]
                if f[2] == item[2] and all(abs(a - b) <= TOL for a, b in zip(f[1], item[1])) and len(f[3]) == len(item[3]) \
                        and all(abs(a[0]-b[0]) <= TOL and abs(a[1]-b[1]) <= TOL for a, b in zip(f[3], item[3])):
                    cl.append(item); break
            else:
                clusters.append([item])
        for cl in clusters:
            if len(cl) > 1:
                for item in cl[:-1]:  # inspect order is top first
                    extras[item[0]] = cl[-1][0]
    return extras
extras = run_long(go)
found = {s["id"]: s["duplicate_of"] for s in find_shapes(path, pages=[1], preset="stacked_duplicates", limit=1000).data["shapes"]}
print("truth", len(extras), "find", len(found), "equal", extras == found)
print("only truth", {k: v for k, v in extras.items() if found.get(k) != v}); print("only find", {k: v for k, v in found.items() if extras.get(k) != v})
