"""Build mixed.cdr (groups, text, contour, registration, fountain) and compare batched vs direct inspection.

Usage: parity.py <fx dir> build|compare <cdr name> [layer]
"""
import sys, time, traceback; sys.path.insert(0, ".")
from core.connection import get_connection
from core.drawing import walk_shapes
from tools import inspection
conn = get_connection()
FX, MODE = sys.argv[1], sys.argv[2]
NAME = sys.argv[3] if len(sys.argv) > 3 else "mixed.cdr"
LAYER = sys.argv[4] if len(sys.argv) > 4 else None
PATH = FX + "\\" + NAME


def spot(app, name, tint=100):
    return app.CreateSpotColorByName("PANTONE® solid coated", name, tint)


def build():
    app = conn.app
    doc = app.CreateDocument(); doc.Unit = 3
    try:
        page = doc.ActivePage
        layer = page.ActiveLayer; layer.Name = "Art"
        a = layer.CreateRectangle2(10, 10, 30, 20); a.Fill.ApplyUniformFill(spot(app, "PANTONE 186 C", 40))
        a.Outline.Width = 0.5; a.Outline.Color = app.CreateCMYKColor(0, 100, 0, 0)
        b = layer.CreateEllipse2(60, 30, 10, 10); b.Fill.ApplyUniformFill(app.CreateRegistrationColor())
        c = layer.CreateLineSegment(80, 10, 80, 30); c.Outline.Width = 0  # zero-width bounds and outline
        d = layer.CreateArtisticText(10, 60, "Hi there"); d.Fill.ApplyUniformFill(app.CreateRGBColor(0, 0, 255))
        e = layer.CreateRectangle2(10, 100, 30, 30)
        e.Fill.ApplyFountainFill(app.CreateCMYKColor(0, 0, 0, 0), app.CreateCMYKColor(0, 0, 0, 100))
        inner = app.CreateShapeRange(); inner.Add(a); inner.Add(b)
        g1 = inner.Group()
        outer = app.CreateShapeRange(); outer.Add(g1); outer.Add(c)
        outer.Group()
        f = layer.CreateRectangle2(100, 100, 30, 30); f.Fill.ApplyUniformFill(spot(app, "PANTONE 300 C"))
        f.CreateContour(1, 2, 1, 0, None, None, None)  # inward contour
        layer2 = page.CreateLayer("Second")
        h = layer2.CreateRectangle2(0, 0, 5, 5); h.OverprintFill = True; h.Outline.SetNoOutline()
        doc.SaveAs(PATH, app.CreateStructSaveAsOptions())
        return {"saved": PATH, "shapes": page.Shapes.Count}
    except Exception:
        return traceback.format_exc()
    finally:
        doc.Close()


def activate():
    app = conn.app
    for i in range(1, app.Documents.Count + 1):
        d = app.Documents.Item(i)
        if d.FullFileName.lower() == PATH.lower():
            d.Activate(); return "activated"
    app.OpenDocument(PATH); return "opened"


def direct():
    """Legacy algorithm: walk_shapes + per-property COM reads."""
    doc = conn.app.ActiveDocument
    page = doc.Pages(1)
    out = []
    for layer in page.AllLayers:
        if LAYER and layer.Name != LAYER:
            continue
        info = inspection._layer_details(layer, layer.Name)
        for shape, parent in walk_shapes(layer.Shapes):
            out.append(inspection._inspect_shape(shape, parent, info))
    return out


def norm(records):
    return [{k: v for k, v in r.items()} for r in records]


if MODE == "build":
    print(conn.safe_call(build)); sys.exit()
print(conn.safe_call(activate))
t = time.perf_counter(); legacy = conn.safe_call(direct); t_legacy = time.perf_counter() - t
if not legacy["success"]:
    print("legacy failed", legacy); sys.exit(1)
t = time.perf_counter(); new = inspection.inspect_shapes(PATH, 1, LAYER); t_new = time.perf_counter() - t
if not new.success:
    print("new failed", new.error); sys.exit(1)
a, b = norm(legacy["result"]), norm(new.data["shapes"])
print(f"legacy {len(a)} shapes {t_legacy:.1f}s | batched {len(b)} shapes {t_new:.1f}s")
mismatch = [(x, y) for x, y in zip(a, b) if x != y]
print("identical:", a == b, "mismatches:", len(mismatch) + abs(len(a) - len(b)))
for x, y in mismatch[:3]:
    print(" legacy:", x); print(" batch: ", y)
if NAME == "mixed.cdr":
    for r in b:
        print(r)
# paging parity
pages, off = [], 0
while off is not None:
    r = inspection.inspect_shapes(PATH, 1, LAYER, off, 7)
    pages += r.data["shapes"]; off = r.data["next_offset"]
print("paged identical:", norm(pages) == b)
sub = inspection.inspect_shapes(PATH, 1, LAYER, 0, 5, ["fill", "type"])
print("fields subset sample:", sub.data["shapes"][:2])
