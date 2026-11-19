"""Show how two shapes differ (COM direct): bounds, paint, node sets. Usage: pairdiff.py doc page id1 id2 [...]"""
import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, open_mine, run
from groundtruth import paint, nodes  # noqa
from core.structure import resolve_shapes
name, page = sys.argv[1], int(sys.argv[2]); ids = sys.argv[3:]
def go():
    doc = open_mine(WORK + "\\" + name)
    out = []
    for a, b in zip(ids[::2], ids[1::2]):
        sa, sb = resolve_shapes(doc, doc.Pages(page), [a, b])
        na, nb = nodes(sa), nodes(sb)
        diff = None
        if na and nb and len(na) == len(nb):
            diff = max(max(abs(x[0]-y[0]), abs(x[1]-y[1])) for x, y in zip(na, nb))
        out.append((a, b, sa.Type, [round(sa.LeftX - sb.LeftX, 7), round(sa.TopY - sb.TopY, 7)], paint(sa) == paint(sb),
                    paint(sa), paint(sb) if paint(sa) != paint(sb) else "", len(na or ()), len(nb or ()), diff))
    return out
for row in run(go): print(row)
