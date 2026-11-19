"""E13 probe 7: effect contour vs Curve.Contour on realistic text (holes) at 0.75 pt, and the degenerate case."""
import json, sys, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path
from core.targets import activate_document
from probe5 import info


def true_area(shape):
    """Holes subtract: signed by nesting depth using bbox containment (rough, for probes)."""
    n, sps = shape.Evaluate("array(@com.Curve.SubPaths.Count, @com.Curve.SubPaths.toarray().convert(array($item.Area, "
                            "$item.PositionX, $item.PositionY, $item.SizeWidth, $item.SizeHeight)))")
    total = 0
    for i, (a, x, y, w, h) in enumerate(sps):
        depth = sum(1 for j, (a2, x2, y2, w2, h2) in enumerate(sps) if j != i and x2 <= x and x + w <= x2 + w2
                    and y2 - h2 <= y - h and y <= y2 and a2 > a)
        total += a if depth % 2 == 0 else -a
    return round(total, 5), n


def go():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    out = {}
    tmp = page.CreateLayer("e13 tmp")
    try:
        for label, size, d in (("big", 200, 0.75 / 72), ("degenerate", 72, 0.05)):
            t = tmp.CreateArtisticText(1, 1, "OBO 8", 1033, 0, "Arial", size)
            t.ConvertToCurves()
            res = {"base": true_area(t), "perimeter": t.Curve.Length}
            e = t.Duplicate(0, 0).CreateContour(0, d, 1, 0, None, None, None)
            res["effect"] = true_area(e.Contour.ContourGroup.Shapes.Item(1))
            res["curve"] = true_area(tmp.CreateCurve(t.Curve.Contour(d, 0, 0, 4, 15)))
            res["expected_area"] = round(res["base"][0] - res["perimeter"] * d, 5)
            out[label] = res
    except Exception:
        out["error"] = traceback.format_exc()
    return out


if __name__ == "__main__":
    P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "probe7.cdr")
    try:
        print(json.dumps(run(go), indent=1, default=str))
    finally:
        close_path(P)
