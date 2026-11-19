"""E13 probe 8: DisplayCurve of text/groups, contour defaults, effect types and Separate for shadow/extrude/blend."""
import json, sys, traceback
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
import pythoncom
from lab import conn, open_mine, run, fresh_copy, close_path, methods
from core.targets import activate_document
from probe7 import true_area


def sid(s):
    return str(s.StaticID)


def go():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    out = {}
    tl, _ = conn.app._oleobj_.GetTypeInfo().GetContainingTypeLib()
    for i in range(tl.GetTypeInfoCount()):
        name = tl.GetDocumentation(i)[0]
        if name in ("cdrEffectType", "cdrShapeType"):
            ti = tl.GetTypeInfo(i); a = ti.GetTypeAttr()
            out[name] = {ti.GetNames(ti.GetVarDesc(j).memid)[0]: ti.GetVarDesc(j).value for j in range(a.cVars)}
    tmp = page.CreateLayer("e13 tmp")
    try:
        t = tmp.CreateArtisticText(1, 1, "OBO", 1033, 0, "Arial", 200)
        try:
            out["text_displaycurve"] = true_area(tmp.CreateCurve(t.DisplayCurve))
        except Exception as exc:
            out["text_displaycurve"] = "ERR " + str(exc)[:150]
        c = t.Duplicate(0, 0); c.ConvertToCurves(); out["text_curves"] = true_area(c)
        g1 = tmp.CreateRectangle2(5, 5, 1, 1); g2 = tmp.CreateRectangle2(7, 5, 1, 1)
        rg = conn.app.CreateShapeRange(); rg.Add(g1); rg.Add(g2); grp = rg.Group()
        try:
            dc = grp.DisplayCurve
            out["group_displaycurve"] = "None" if dc is None else true_area(tmp.CreateCurve(dc))
        except Exception as exc:
            out["group_displaycurve"] = "ERR " + str(exc)[:150]
        s = tmp.CreateRectangle2(10, 10, 1, 1)
        e = s.CreateContour(0, 0.1, 1, 0, None, None, None)
        k = e.Contour
        out["contour_defaults"] = {n: getattr(k, n) for n in ("CornerType", "EndCapType", "MiterLimit", "Steps", "Offset")
                                   if hasattr(k, n)}
        out["effect_members"] = [m for m in methods(e) if not m.startswith(("Get", "Add", "Query", "Release", "Invoke"))]
        # drop shadow
        s2 = tmp.CreateRectangle2(12, 12, 1, 1)
        for name, make in (("shadow", lambda x: x.CreateDropShadow(0, 50, 5, 0.1, -0.1)),
                           ("extrude", lambda x: x.CreateExtrude(0, 0, 0, 0.5, 0.5, 2)),):
            try:
                x = tmp.CreateRectangle2(12, 12, 1, 1)
                eff = make(x)
                sep = eff.Separate()
                out[name] = {"type": eff.Type, "sep": [(sid(sep.Item(i)), sep.Item(i).Type) for i in range(1, sep.Count + 1)],
                             "effects_after": x.Effects.Count}
            except Exception as exc:
                out[name] = "ERR " + str(exc)[:200]
        try:
            a = tmp.CreateRectangle2(14, 1, 1, 1); b = tmp.CreateEllipse2(17, 1.5, 0.5, 0.5)
            eff = a.CreateBlend(b, 5)
            sep = eff.Separate()
            out["blend"] = {"type": eff.Type, "sep": [(sid(sep.Item(i)), sep.Item(i).Type) for i in range(1, sep.Count + 1)]}
        except Exception as exc:
            out["blend"] = "ERR " + str(exc)[:200]
    except Exception:
        out["error"] = traceback.format_exc()
    return out


if __name__ == "__main__":
    P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "probe8.cdr")
    try:
        print(json.dumps(run(go), indent=1, default=str))
    finally:
        close_path(P)
