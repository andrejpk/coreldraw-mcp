"""E13 probe 3: boundary vs weld with holes (text), contour corner types, locked-layer copies, group copies."""
import json, sys, traceback
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
    tmp = page.CreateLayer("e13 tmp")
    locked = page.CreateLayer("e13 locked")
    try:
        doc.BeginCommandGroup("probe")
        try:
            t = tmp.CreateArtisticText(1, 1, "OBO", 1033, 0, "Arial", 72)
            tc = t.CopyToLayer(tmp); tc.ConvertToCurves()
            out["text_curve_area"] = tc.DisplayCurve.Area
            r1 = conn.app.CreateShapeRange(); r1.Add(tc)
            b = r1.CreateBoundary(0, 0, True, False)
            out["boundary_area"] = b.DisplayCurve.Area if b else None
            out["boundary_subpaths"] = b.Curve.SubPaths.Count if b else None
            out["curve_subpaths"] = tc.Curve.SubPaths.Count
            w2 = tc.Duplicate(0, 0); w3 = tmp.CreateRectangle2(100, 100, 1, 1)
            w = w2.Weld(w3, False, False)
            out["weld_area_minus_rect"] = w.DisplayCurve.Area - 1
            # contour corners
            for corner in (0, 1, 2, 3, 4):
                s = tmp.CreateRectangle2(5, 5, 2, 2)
                try:
                    e = s.CreateContour(1, 0.1, 1, 0, None, None, None, 0, 0, 0, corner, 15)
                    g = e.Contour.ContourGroup.Shapes.Item(1)
                    out[f"corner{corner}"] = {"read": e.Contour.CornerType, "area": g.DisplayCurve.Area,
                                             "bounds": [g.LeftX, g.BottomY, g.RightX, g.TopY]}
                except Exception as exc:
                    out[f"corner{corner}"] = str(exc)[:200]
            # locked layer source
            lr = locked.CreateRectangle2(1, 1, 1, 1)
            locked.Editable = False
            try:
                c = lr.CopyToLayer(tmp); out["copy_from_locked"] = [sid(c), c.Layer.Name]
            except Exception as exc:
                out["copy_from_locked"] = str(exc)[:200]
            try:
                d = lr.Duplicate(0, 0); out["dup_on_locked"] = [sid(d), d.Layer.Name]
            except Exception as exc:
                out["dup_on_locked"] = str(exc)[:200]
            try:
                c = tmp.CreateRectangle2(1, 1, 1, 1).CopyToLayer(locked); out["copy_to_locked"] = [sid(c), c.Layer.Name]
            except Exception as exc:
                out["copy_to_locked"] = str(exc)[:200]
            locked.Editable = True
            # group copy
            g1 = tmp.CreateRectangle2(1, 1, 1, 1); g2 = tmp.CreateRectangle2(3, 1, 1, 1)
            rg = conn.app.CreateShapeRange(); rg.Add(g1); rg.Add(g2)
            grp = rg.Group()
            child = grp.Shapes.Item(1)
            c = child.CopyToLayer(locked)
            out["child_copy"] = {"id": sid(c), "layer": c.Layer.Name, "parent_is_layer": c.ParentGroup is None}
            # effect-generated object copy
            s = tmp.CreateRectangle2(8, 8, 1, 1)
            e = s.CreateContour(1, 0.1, 2, 0, None, None, None)
            gen = e.Contour.ContourGroup.Shapes.Item(1)
            try:
                c = gen.CopyToLayer(tmp); out["effect_child_copy"] = [sid(c), c.Type]
            except Exception as exc:
                out["effect_child_copy"] = str(exc)[:200]
            try:
                gen.Duplicate(0, 0); out["effect_child_dup"] = "ok"
            except Exception as exc:
                out["effect_child_dup"] = str(exc)[:200]
            out["effect_group_copy"] = None
            try:
                c = e.Contour.ContourGroup.CopyToLayer(tmp); out["effect_group_copy"] = [sid(c), c.Type]
            except Exception as exc:
                out["effect_group_copy"] = str(exc)[:200]
        finally:
            doc.EndCommandGroup()
        doc.Undo(1)
    except Exception:
        out["error"] = traceback.format_exc()
    finally:
        locked.Editable = True
        tmp.Delete(); locked.Delete()
        doc.Dirty = False
    return out


print(json.dumps(run(go), indent=1, default=str))
