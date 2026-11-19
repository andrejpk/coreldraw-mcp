"""E13 probe 5: curve-level union (AppendCurve + SelfWeld/RemoveOverlaps/WeldEx) and Curve.Contour vs shape ops."""
import json, sys, time, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path
from core.targets import activate_document

P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "probe5.cdr")


def sid(s):
    return str(s.StaticID)


def info(shape):
    q = ("array(@com.Curve.SubPaths.Count, @com.Curve.SubPaths.toarray().convert(array($item.Area, "
         "$item.PositionX, $item.PositionY, $item.SizeWidth, $item.SizeHeight)))")
    try:
        n, sps = shape.Evaluate(q)
        return {"n": n, "sum": round(sum(s[0] for s in sps), 5), "sps": [[round(v, 4) for v in s] for s in sps][:12]}
    except Exception as exc:
        return {"cql_error": str(exc)[:200], "area": shape.DisplayCurve.Area}


def go():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    out = {}
    tmp = page.CreateLayer("e13 tmp")
    try:
        t = tmp.CreateArtisticText(1, 1, "OBO", 1033, 0, "Arial", 72)
        t.ConvertToCurves()
        r1 = tmp.CreateRectangle2(1.2, 1.2, 0.5, 0.3)  # overlaps the first O's ring
        r2 = tmp.CreateRectangle2(1.0, 1.0, 0.2, 0.2)
        out["text"] = info(t)
        # shape weld reference
        a, b, c = t.Duplicate(0, 0), r1.Duplicate(0, 0), r2.Duplicate(0, 0)
        w = b.Weld(a, False, False); w = c.Weld(w, False, False)
        out["shape_weld"] = info(w)
        out["inside_cql"] = None
        try:
            out["inside_cql"] = w.Evaluate("@com.Curve.SubPaths.Item(2).IsPointInside(1.5, 1.5)")
        except Exception as exc:
            out["inside_cql"] = str(exc)[:150]
        # curve union
        for method in ("self", "remove", "weldex", "weldwith"):
            try:
                tt = time.perf_counter()
                cv = t.Curve.GetCopy() if hasattr(t.Curve, "GetCopy") else None
                cv = conn.app.CreateCurve(doc)
                cv.AppendCurve(t.Curve)
                if method in ("self", "remove"):
                    cv.AppendCurve(r1.DisplayCurve); cv.AppendCurve(r2.DisplayCurve)
                    getattr(cv, {"self": "SelfWeldClosedSubpaths", "remove": "RemoveOverlaps"}[method])()
                elif method == "weldex":
                    res = cv
                    for other in (r1.DisplayCurve, r2.DisplayCurve):
                        res = res.WeldEx(other, 0, 0, 0, 0)
                    cv = res
                else:
                    res = cv
                    for other in (r1.DisplayCurve, r2.DisplayCurve):
                        res = res.WeldWith(other)
                    cv = res
                s = tmp.CreateCurve(cv)
                out[method] = {**info(s), "s": round(time.perf_counter() - tt, 3)}
            except Exception as exc:
                out[method] = "ERR " + str(exc)[:200]
        # Curve.Contour
        try:
            cc = w.Curve.Contour(0.05, 0, 0, 4, 15)
            s = tmp.CreateCurve(cc)
            out["curve_contour"] = info(s)
            e = w.Duplicate(0, 0).CreateContour(0, 0.05, 1, 0, None, None, None)
            g = e.Contour.ContourGroup.Shapes.Item(1)
            out["effect_contour"] = info(g)
        except Exception as exc:
            out["curve_contour"] = "ERR " + traceback.format_exc()[-300:]
    except Exception:
        out["error"] = traceback.format_exc()
    return out


if __name__ == "__main__":
    P = fresh_copy(r"C:\CorelMcpLab	emplates\screen_job.cdr", "probe5.cdr")
    try:
        print(json.dumps(run(go), indent=1, default=str))
    finally:
        close_path(P)
