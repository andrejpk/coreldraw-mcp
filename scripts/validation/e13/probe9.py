"""E13 probe 9: Separate on the effects.cdr drop shadow and contours (what comes out, IDs, undo)."""
import json, sys, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path
from core.targets import activate_document


def sid(s):
    return str(s.StaticID)


def tree(s, depth=0):
    d = {"id": sid(s), "type": s.Type}
    if s.Type in (7, 10, 11, 13, 15, 16, 18, 22):
        d["kids"] = [tree(s.Shapes.Item(i), depth + 1) for i in range(1, s.Shapes.Count + 1)]
    return d


def go():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    out = {}
    layer = [l for l in page.Layers if l.Name == "Effects"][0]
    out["before"] = [tree(s) for s in layer.Shapes]
    shapes = {sid(s): s for s in layer.Shapes}
    try:
        doc.BeginCommandGroup("probe sep")
        try:
            for control in ("11", "7"):
                s = shapes[control]
                effs = [s.Effects.Item(i) for i in range(1, s.Effects.Count + 1)]
                out[f"effects_{control}"] = [e.Type for e in effs]
                sep = effs[0].Separate()
                out[f"sep_{control}"] = [tree(sep.Item(i)) for i in range(1, sep.Count + 1)]
        finally:
            doc.EndCommandGroup()
        out["after"] = [tree(s) for s in layer.Shapes]
        doc.Undo(1)
        out["undone"] = [tree(s) for s in layer.Shapes]
    except Exception:
        out["error"] = traceback.format_exc()
    return out


if __name__ == "__main__":
    P = fresh_copy(r"C:\CorelMcpLab\templates\effects.cdr", "probe9.cdr")
    try:
        print(json.dumps(run(go), default=str))
    finally:
        close_path(P)
