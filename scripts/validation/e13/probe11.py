"""E13 probe 11: is an empty command group an undo step? (Undo(1) after an empty group)."""
import json, sys
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path
from core.targets import activate_document


def go():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    layer = [l for l in doc.Pages(1).Layers if l.Name == "PANTONE 186 C"][0]
    n0 = layer.Shapes.Count
    layer.CreateRectangle2(1, 1, 1, 1)  # a previous user edit
    n1 = layer.Shapes.Count
    doc.BeginCommandGroup("empty")
    doc.EndCommandGroup()
    doc.Undo(1)
    n2 = layer.Shapes.Count
    doc.BeginCommandGroup("failing")
    try:
        layer.Shapes.Item(1).Weld(None, False, False)
    except Exception as exc:
        err = str(exc)[:80]
    doc.EndCommandGroup()
    doc.Undo(1)
    n3 = layer.Shapes.Count
    doc.Undo(1)
    n4 = layer.Shapes.Count
    return {"n4_one_more_undo": n4, "n0": n0, "after_user_edit": n1, "after_empty_group_undo": n2, "after_failed_group_undo": n3, "err": err}


if __name__ == "__main__":
    P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "probe11.cdr")
    try:
        print(json.dumps(run(go), default=str))
    finally:
        close_path(P)
