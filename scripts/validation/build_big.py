"""Build a flat 4,452-object single-layer fixture with mixed native colors by doubling a ShapeRange."""
import sys, time, traceback; sys.path.insert(0, ".")
from core.connection import get_connection
conn = get_connection()
FX = sys.argv[1]
N = 4452


def build():
    app = conn.app
    doc = app.CreateDocument(); doc.Unit = 3
    t = time.perf_counter()
    try:
        layer = doc.ActivePage.ActiveLayer; layer.Name = "Flat"
        colors = [app.CreateSpotColorByName("PANTONE® solid coated", "PANTONE 186 C", 100),
                  app.CreateSpotColorByName("PANTONE® solid coated", "PANTONE 300 C", 60),
                  app.CreateCMYKColor(0, 0, 0, 100), app.CreateRGBColor(255, 0, 0), None,
                  app.CreateSpotColorByName("PANTONE® solid coated", "PANTONE 354 C", 100), None]
        for i, c in enumerate(colors):
            r = layer.CreateRectangle2(i * 3, 0, 2, 2)
            if c is None: r.Fill.ApplyNoFill()
            else: r.Fill.ApplyUniformFill(c)
            if i % 3 == 0: r.Outline.SetNoOutline()
            if i in (1, 5): r.OverprintFill = True
            if i == 2: r.OverprintOutline = True
        step = 3
        while layer.Shapes.Count < N:
            need = N - layer.Shapes.Count
            rng = layer.Shapes.All()
            if rng.Count > need:
                rng = layer.Shapes.Range(list(range(1, need + 1)))
            rng.Duplicate(0, step); step *= 2
        doc.SaveAs(FX + r"\big_flat.cdr", app.CreateStructSaveAsOptions())
        return {"count": layer.Shapes.Count, "seconds": time.perf_counter() - t}
    except Exception:
        return traceback.format_exc()
    finally:
        doc.Dirty = False; doc.Close()


print(conn.safe_call(build))
