"""E13 probe 2 on a screen_job copy: CopyToLayer, CreateBoundary, ConvertToObject, ConvertToCurves IDs, contour/separate."""
import json, sys, time, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, WORK
from core.targets import activate_document


def sid(s):
    return str(s.StaticID)


def go():
    doc = open_mine(WORK + r"\screen_job.cdr")
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    out = {}
    layers = {l.Name: l for l in page.Layers}
    find = {sid(s): s for l in page.Layers for s in l.Shapes}
    tmp = page.CreateLayer("e13 tmp")
    try:
        doc.BeginCommandGroup("probe")
        try:
            art = [find[i] for i in ("4", "6", "8", "10")]
            # CopyToLayer
            c = art[0].CopyToLayer(tmp)
            out["copy_to_layer"] = {"id": sid(c), "layer": c.Layer.Name, "src_layer": art[0].Layer.Name, "type": c.Type}
            rng = conn.app.CreateShapeRange()
            for a in art:
                rng.Add(a)
            t = time.perf_counter()
            b = rng.CreateBoundary(0, 0, True, False)
            out["boundary"] = {"id": sid(b), "type": b.Type, "layer": b.Layer.Name, "area": b.DisplayCurve.Area,
                               "bounds": [b.LeftX, b.BottomY, b.RightX, b.TopY], "s": time.perf_counter() - t,
                               "fill": b.Fill.Type, "outline": b.Outline.Type}
            # weld for comparison
            cps = [a.CopyToLayer(tmp) for a in art]
            r = cps[0]
            for x in cps[1:]:
                r = x.Weld(r, False, False)
            out["weld"] = {"id": sid(r), "area": r.DisplayCurve.Area, "bounds": [r.LeftX, r.BottomY, r.RightX, r.TopY],
                           "layer": r.Layer.Name}
            # outline: stroked line
            line = tmp.CreateLineSegment(1, 1, 3, 1)
            line.Outline.Width = 0.1
            out["line_area"] = line.DisplayCurve.Area
            o = line.Outline.ConvertToObject()
            out["outline_obj"] = {"ret": str(type(o)), "id": sid(o) if o is not None else None,
                                  "line_outline_after": line.Outline.Type,
                                  "area": o.DisplayCurve.Area if o is not None else None,
                                  "bounds": [o.LeftX, o.BottomY, o.RightX, o.TopY] if o is not None else None,
                                  "fill": o.Fill.Type}
            # boundary with a stroked line
            line2 = tmp.CreateLineSegment(1, 2, 3, 2)
            line2.Outline.Width = 0.1
            rr = conn.app.CreateShapeRange(); rr.Add(line2)
            try:
                b2 = rr.CreateBoundary(0, 0, True, False)
                out["boundary_line"] = {"area": b2.DisplayCurve.Area, "bounds": [b2.LeftX, b2.BottomY, b2.RightX, b2.TopY]}
            except Exception as exc:
                out["boundary_line"] = str(exc)
            # text to curves id
            texts = [s for s in layers["Film Titles"].Shapes if s.Type == 6]
            tc = texts[0].CopyToLayer(tmp)
            before = sid(tc)
            tc.ConvertToCurves()
            out["text_curves"] = {"before": before, "after": sid(tc), "type": tc.Type}
            # contour on weld, separate
            eff = r.CreateContour(0, 0.75 / 72, 1, 0, None, None, None)
            grp = eff.Contour.ContourGroup
            out["contour"] = {"effect_type": eff.Type, "group": sid(grp), "group_type": grp.Type,
                              "children": [(sid(x), x.Type) for x in grp.Shapes],
                              "corner": eff.Contour.CornerType if hasattr(eff.Contour, "CornerType") else None}
            sep = eff.Separate()
            out["separate"] = [(sid(sep.Item(i)), sep.Item(i).Type) for i in range(1, sep.Count + 1)]
            out["after_sep_effects"] = r.Effects.Count
        finally:
            doc.EndCommandGroup()
        n_tmp = tmp.Shapes.Count
        doc.Undo(1)
        out["undo"] = {"tmp_before_undo": n_tmp, "tmp_after_undo": tmp.Shapes.Count}
    except Exception:
        out["error"] = traceback.format_exc()
    finally:
        tmp.Delete()
        doc.Dirty = False
    return out


print(json.dumps(run(go), indent=1, default=str))
