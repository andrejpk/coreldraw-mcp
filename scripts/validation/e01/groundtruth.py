"""Read-only ground truth after the MCP audit (in-process COM, server stopped)."""
import ntpath, sys, json; sys.path.insert(0, ".")
from core.connection import get_connection
conn = get_connection()
RUN = ntpath.normcase(__import__("os").environ.get("AUDIT_RUN", r"C:\CorelMcpLab\runs\e01")) + "\\"
def find(doc, sid):
    from core.drawing import walk_shapes
    for p in range(1, doc.Pages.Count + 1):
        for L in doc.Pages(p).AllLayers:
            for s, _ in walk_shapes(L.Shapes):
                if str(s.StaticID) == sid: return s
def col(c):
    try: return c.Name if c.IsSpot else f"type{c.Type} {c.Name}"
    except Exception as e: return str(e)
def run():
    app = conn.app; out = {"docs": []}
    for i in range(1, app.Documents.Count + 1):
        d = app.Documents.Item(i); full = d.FullFileName or "untitled"
        mine = ntpath.normcase(full).startswith(RUN)
        out["docs"].append({"path": full if mine else "(not mine)", "mine": mine, "dirty": bool(d.Dirty) if mine else None, "unit": d.Unit if mine else None})
        if not mine: continue
        if full.endswith("screen_job_mut.cdr"):
            s = find(d, "65"); out["ub"] = {"layer": s.Layer.Name, "fill": col(s.Fill.UniformColor), "op_fill": bool(s.OverprintFill), "z_layer_index": s.Layer.Index}
            out["ub_old_gone"] = [find(d, x) is None for x in ("5", "7", "9", "11", "64")]
        if full.endswith("kings_mut2.cdr"):
            out["kings2"] = {sid: {"fill": col(find(d, sid).Fill.UniformColor) if find(d, sid).Fill.Type == 1 else None,
                                   "outline": col(find(d, sid).Outline.Color) if find(d, sid).Outline.Type else None} for sid in ("121", "135", "138")}
            m = find(d, "64")
            out["kings2"]["mark64_top_in"] = app.ConvertUnits(m.TopY, d.Unit, 1)
            t = find(d, "77").Text.Story
            out["kings2"]["text77"] = [(t.Characters.Item(k).Text, col(t.Characters.Item(k).Fill.UniformColor)) for k in (1, 10, 23)]
    return out
print(json.dumps(conn.safe_call(run), indent=1, default=str))
