r"""E07 probe 2 (server stopped): layer stacking by pixels on probe\new.cdr; Layer.Import with StructImportOptions."""
import json, shutil, sys, traceback
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_inproc import gate  # noqa: E402
gate()
from core.connection import get_connection  # noqa: E402
from core.targets import activate_document, find_documents  # noqa: E402
from PIL import Image  # noqa: E402
RUN = Path(r"C:\CorelMcpLab\runs\e07\probe")
NEW = str(RUN / "new.cdr")
LAYERS = str(RUN / "layers2.cdr")
shutil.copyfile(r"C:\CorelMcpLab\templates\layers.cdr", LAYERS)
conn = get_connection()
out = {}


def t(name, fn):
    try:
        out[name] = fn()
    except Exception:
        out[name] = "ERROR " + traceback.format_exc().splitlines()[-1]
    print(name, "=>", json.dumps(out[name], default=str)[:900])


def bounds(s):
    return [round(v, 4) for v in (s.LeftX, s.BottomY, s.RightX, s.TopY)]


def png(doc, path):
    activate_document(conn.app, doc)
    f = doc.ExportBitmap(path, 802, 1, 4, 0, 0, 20, 20, 1, False, False, True, False, 0, None, None)
    f.Finish()
    return Image.open(path).convert("RGB")


def run():
    app = conn.app
    nd, = find_documents(app, NEW)
    pg = nd.Pages(1)
    t("new_layers", lambda: [(x.Name, bool(x.Master)) for x in pg.AllLayers])
    t("new_unit_page", lambda: (nd.Unit, pg.SizeWidth, pg.SizeHeight, pg.LeftX, pg.BottomY, pg.RightX, pg.TopY))

    def pixels():
        layers = {x.Name: x for x in pg.Layers}
        shapes = {}
        for x in pg.Shapes:
            shapes[str(x.StaticID)] = (x.Layer.Name, bounds(x))
        img = png(nd, str(RUN / "stack.png"))
        w, h = img.size
        # overlap of red (10..60) and blue (20..70) in mm: x=40mm, y=40mm from bottom-left
        px = img.getpixel((int(w * 40 / pg.SizeWidth), int(h - h * 40 / pg.SizeHeight)))
        return {"shapes": shapes, "size": (w, h), "overlap_pixel": px, "layers_order": list(layers)}
    t("pixels", pixels)
    nd.Dirty = False
    nd.Close()

    doc = app.OpenDocument(LAYERS)
    activate_document(app, doc)
    p2 = doc.Pages(2)
    art = [x for x in p2.Layers if x.Name == "Art"][0]

    def opts():
        o = app.CreateStructImportOptions()
        names = [n for n in dir(o) if not n.startswith("_")]
        return {"type": str(type(o)), "names": names[:60]}
    t("import_options", opts)

    def imp(path, ex=False):
        before = {str(s.StaticID) for l in p2.AllLayers for s in l.Shapes}
        o = app.CreateStructImportOptions()
        if ex:
            f = art.ImportEx(path, 0, o)
            f.Finish()
            ret = "importex"
        else:
            ret = art.Import(path, 0, o)
        after = [(str(s.StaticID), s.Type, s.Layer.Name, bounds(s)) for l in p2.AllLayers for s in l.Shapes
                 if str(s.StaticID) not in before]
        sel = nd_sel(doc)
        return {"ret": str(ret), "new": after, "selection": sel, "active_page": doc.ActivePage.Index,
                "p1_count": doc.Pages(1).Shapes.Count}

    def nd_sel(d):
        s = d.SelectionRange
        return [(str(s.Item(i).StaticID), s.Item(i).Layer.Name) for i in range(1, s.Count + 1)]
    t("import_svg", lambda: imp(str(RUN / "box.svg")))
    t("import_png_ex", lambda: imp(str(RUN / "green.png"), ex=True))
    t("import_png", lambda: imp(str(RUN / "green.png")))

    def undo():
        n = sum(l.Shapes.Count for l in p2.AllLayers)
        doc.BeginCommandGroup("imp")
        art.Import(str(RUN / "box.svg"), 0, app.CreateStructImportOptions())
        doc.EndCommandGroup()
        m = sum(l.Shapes.Count for l in p2.AllLayers)
        doc.Undo(1)
        return (n, m, sum(l.Shapes.Count for l in p2.AllLayers))
    t("import_undo", undo)
    t("import_missing", lambda: str(art.Import(str(RUN / "nope.svg"), 0, app.CreateStructImportOptions())))
    doc.Dirty = False
    doc.Close()
    return out


r = conn.safe_call(run)
print("RESULT", r["success"], r.get("error"))
(RUN / "probe2.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
