"""Build accept.cdr: disposable fixture for the 2026-11-17 production follow-up acceptance tests.

Page 1  layer "Text":     artistic "AB  CD" in PANTONE 186 C except the two spaces in PANTONE 300 C;
                          donor rectangle PANTONE 186 C (tint 100).
        layer "Weld":     top (300) / c1 / c2 / c3 (186, overlapping) / tint60 (186 at 60%) / bottom (300)
        layer "Ink B":    PANTONE 300 C rectangle with fill overprint
        layer "Hidden C": hidden but printable, PANTONE 354 C
Page 2  layer "Ink B":    PANTONE 485 C (same layer name on another page)
Prints a JSON map of names to StaticIDs.
"""
import json, sys, traceback; sys.path.insert(0, ".")
from core.connection import get_connection
conn = get_connection()
PATH = sys.argv[1] + r"\accept.cdr"


def spot(app, name, tint=100):
    return app.CreateSpotColorByName("PANTONE® solid coated", name, tint)


def rect(app, layer, name, x, y, w, h, color, overprint=False):
    r = layer.CreateRectangle2(x, y, w, h); r.Name = name
    r.Fill.ApplyUniformFill(color); r.Outline.SetNoOutline(); r.OverprintFill = overprint
    return r


def build():
    app = conn.app
    doc = app.CreateDocument(); doc.Unit = 3
    ids = {}
    try:
        p1 = doc.ActivePage
        text_layer = p1.ActiveLayer; text_layer.Name = "Text"
        t = text_layer.CreateArtisticText(20, 200, "AB  CD"); t.Name = "label"
        t.Text.Story.Fill.ApplyUniformFill(spot(app, "PANTONE 186 C"))
        t.Text.Story.Range(2, 4).Fill.ApplyUniformFill(spot(app, "PANTONE 300 C"))
        ids["text"] = t.StaticID
        ids["donor"] = rect(app, text_layer, "donor", 120, 200, 10, 10, spot(app, "PANTONE 186 C")).StaticID
        weld = p1.CreateLayer("Weld")
        # created bottom-up: the last created is on top
        for name, x, color in (("bottom", 0, spot(app, "PANTONE 300 C")), ("tint60", 70, spot(app, "PANTONE 186 C", 60)),
                               ("c3", 40, spot(app, "PANTONE 186 C")), ("c2", 25, spot(app, "PANTONE 186 C")),
                               ("c1", 10, spot(app, "PANTONE 186 C")), ("top", 5, spot(app, "PANTONE 300 C"))):
            ids[name] = rect(app, weld, name, x, 100, 20, 20, color).StaticID
        ink_b = p1.CreateLayer("Ink B")
        ids["b"] = rect(app, ink_b, "b", 30, 30, 40, 40, spot(app, "PANTONE 300 C"), overprint=True).StaticID
        hidden = p1.CreateLayer("Hidden C")
        ids["c"] = rect(app, hidden, "c", 60, 30, 40, 40, spot(app, "PANTONE 354 C")).StaticID
        hidden.Visible = False
        p2 = doc.AddPages(1)
        p2.ActiveLayer.Name = "Ink B"
        ids["b2"] = rect(app, p2.ActiveLayer, "b2", 10, 10, 40, 40, spot(app, "PANTONE 485 C")).StaticID
        doc.Pages(1).Activate()
        doc.SaveAs(PATH, app.CreateStructSaveAsOptions())
        return {k: str(v) for k, v in ids.items()}
    except Exception:
        doc.Dirty = False; doc.Close()
        return traceback.format_exc()


r = conn.safe_call(build)
print(json.dumps(r["result"]) if r["success"] and isinstance(r["result"], dict) else r)
