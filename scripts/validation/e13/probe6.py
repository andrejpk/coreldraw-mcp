"""E13 probe 6: contour (effect and Curve.Contour) on multi-subpath curves with holes and disjoint parts."""
import json, sys, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path
from core.targets import activate_document
from probe5 import info  # noqa

P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "probe6.cdr")


def go():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    out = {}
    tmp = page.CreateLayer("e13 tmp")
    try:
        big = tmp.CreateRectangle2(0, 0, 4, 4)
        hole = tmp.CreateRectangle2(1, 1, 2, 2)
        ring = hole.Trim(big, False, False)
        other = tmp.CreateRectangle2(6, 0, 2, 2)
        shape = other.Weld(ring, False, False)  # disjoint: ring + square
        out["base"] = info(shape)
        for direction in (0, 1):
            d = shape.Duplicate(0, 0)
            e = d.CreateContour(direction, 0.25, 1, 0, None, None, None)
            grp = e.Contour.ContourGroup
            out[f"effect_{direction}"] = {"children": grp.Shapes.Count,
                                          "kids": [info(grp.Shapes.Item(i)) for i in range(1, grp.Shapes.Count + 1)]}
            for args in ((0.25, direction, 0, 4, 15),):
                try:
                    cv = shape.Curve.Contour(*args)
                    out[f"curve_{direction}"] = info(tmp.CreateCurve(cv))
                except Exception as exc:
                    out[f"curve_{direction}"] = "ERR " + str(exc)[:200]
    except Exception:
        out["error"] = traceback.format_exc()
    return out


if __name__ == "__main__":
    try:
        print(json.dumps(run(go), indent=1, default=str))
    finally:
        close_path(P)
