import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine
from tools import find as F
from core.structure import page_layers
from core.units import unit_name
from core.connection import get_connection
conn = get_connection()
name, page_index, ids = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
def go():
    doc = open_mine(WORK + "\\" + name); page = doc.Pages(page_index)
    q = F._parse("stacked_duplicates", None, None, None, None, unit_name(doc.Unit))
    pb = (page.LeftX, page.BottomY, page.RightX, page.TopY)
    shapes = []
    for v in F._scope_views(page_layers(doc, page), None, q):
        F._scan_layer(conn, q, page_index, pb, v, shapes)
    out = []
    for s in shapes:
        if s.id in ids:
            rows = F._safe_read(s.view, [s.entry], F._NODES)
            out.append((s.id, s.type, s.bounds, F._paint_key(s), s.entry.effect, s.entry.parent_id, rows[0][0] if rows[0] else None, str(rows[0])[:150]))
    return out
for row in run(go): print(row)
