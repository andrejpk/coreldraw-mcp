import sys, time
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
import pythoncom
from core.structure import page_layers, _page_state
def go():
    t=time.perf_counter(); doc = open_mine(WORK + r"\goodnews.cdr"); print("open", round(time.perf_counter()-t,2))
    page = doc.Pages(1)
    views, s = timed(page_layers, doc, page)
    print("walk", s, [(v.name, v.ordinal, len(v.structure.entries), len(v.structure.groups)) for v in views])
    st = _page_state(page)
    a = st.anchor
    for v in views:
        if not v.structure.entries: continue
        o = v.ordinal
        for label, q in {
            "layer count": f"@com.Page.AllLayers.Item({o}).Shapes.toarray().count()",
            "layer filter": f"@com.Page.AllLayers.Item({o}).Shapes.toarray().filter($item.StaticID > 0).count()",
        }.items():
            r, sec = timed(a.Evaluate, q); print(v.name, label, r, sec)
        for path, sid, size, clips in v.structure.groups[:3]:
            from core.structure import query_path
            p = query_path(path, o)
            for label, q in {"grp count": f"{p}.Shapes.count()", "grp filter": f"{p}.Shapes.toarray().filter($item.StaticID > 0).count()",
                             "grp convert ids": f"{p}.Shapes.toarray().convert($item.StaticID).count()",
                             "grp filter HAS": f"{p}.Shapes.toarray().filter($item.Type = 7 or $item.PowerClip.Shapes.Count > 0).count()",
                             "grp Count prop": f"{p}.Shapes.Count"}.items():
                r, sec = timed(a.Evaluate, q); print("  ", sid, size, label, r, sec)
    t=time.perf_counter(); n = st.shapes.FindShapes(pythoncom.Empty, 0, True).Count; print("FindShapes", n, round(time.perf_counter()-t,3))
run(go)
