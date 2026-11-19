"""E13 probe 4: area semantics of holed curves, SubPath members, contour corner enum with a large offset."""
import json, sys, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, WORK
from lab import methods, fresh_copy, close_path
from core.targets import activate_document


P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "probe4.cdr")


def go():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    out = {}
    tmp = page.CreateLayer("e13 tmp")
    try:
        doc.BeginCommandGroup("probe")
        try:
            big = tmp.CreateRectangle2(0, 0, 4, 4)
            hole = tmp.CreateRectangle2(1, 1, 2, 2)
            ring = hole.Trim(big, False, False)
            out["ring"] = {"display_area": ring.DisplayCurve.Area, "curve_area": ring.Curve.Area,
                           "subpaths": ring.Curve.SubPaths.Count}
            sp = ring.Curve.SubPaths.Item(1)
            out["subpath_members"] = [m for m in methods(sp) if not m.startswith(("Get", "Add", "Query", "Release", "Invoke"))]
            out["curve_members"] = [m for m in methods(ring.Curve) if not m.startswith(("Get", "Add", "Query", "Release", "Invoke"))]
            for i in (1, 2):
                s = ring.Curve.SubPaths.Item(i)
                d = {}
                for name in ("Area", "Closed", "Length", "IsClockwise", "BoundingBox"):
                    try:
                        v = getattr(s, name); d[name] = v if isinstance(v, (int, float, bool, str)) else "obj"
                    except Exception as exc:
                        d[name] = "ERR " + str(exc)[:60]
                out[f"sp{i}"] = d
            for corner in (0, 1, 2, 3, 4):
                s = tmp.CreateRectangle2(10, 10, 2, 2)
                e = s.CreateContour(1, 1.0, 1, 0, None, None, None, 0, 0, 0, corner, 15)
                g = e.Contour.ContourGroup.Shapes.Item(1)
                out[f"corner{corner}"] = round(g.DisplayCurve.Area, 4)
            s = tmp.CreateRectangle2(10, 10, 2, 2)
            e = s.CreateContour(1, 1.0, 1)
            out["default_corner"] = [e.Contour.CornerType, round(e.Contour.ContourGroup.Shapes.Item(1).DisplayCurve.Area, 4)]
            out["contour_members"] = [m for m in methods(e.Contour) if not m.startswith(("Get", "Add", "Query", "Release", "Invoke"))]
        finally:
            doc.EndCommandGroup()
        doc.Undo(1)
    except Exception:
        out["error"] = traceback.format_exc()
    finally:
        tmp.Delete()
        doc.Dirty = False
    return out


try:
    print(json.dumps(run(go), indent=1, default=str))
finally:
    close_path(P)
