r"""E07 in-process API probe (server stopped; gate run first). Works only on copies in C:\CorelMcpLab\runs\e07\probe.

Usage (from server/): python ..\scripts\validation\e07\probe1.py
"""
import json
import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, ".")
from core.connection import get_connection  # noqa: E402
from core.targets import activate_document, find_documents  # noqa: E402

RUN = Path(r"C:\CorelMcpLab\runs\e07\probe")
shutil.rmtree(RUN, ignore_errors=True)
RUN.mkdir(parents=True)
LAYERS = str(RUN / "layers.cdr")
NEW = str(RUN / "new.cdr")
shutil.copyfile(r"C:\CorelMcpLab\templates\layers.cdr", LAYERS)
SVG = RUN / "box.svg"
SVG.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50" viewBox="0 0 100 50">'
               '<rect x="0" y="0" width="60" height="50" fill="#ff0000"/>'
               '<circle cx="80" cy="25" r="20" fill="#0000ff"/></svg>', encoding="utf-8")
from PIL import Image  # noqa: E402

Image.new("RGB", (40, 20), (0, 200, 0)).save(RUN / "green.png")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_inproc import gate  # noqa: E402

gate()
conn = get_connection()
out = {}


def t(name, fn):
    try:
        out[name] = fn()
    except Exception:
        out[name] = "ERROR " + traceback.format_exc().splitlines()[-1]
    print(name, "=>", json.dumps(out[name], default=str)[:600])


def bounds(s):
    return [round(v, 4) for v in (s.LeftX, s.BottomY, s.RightX, s.TopY)]


def run():
    app = conn.app
    docs_before = [d.FullFileName or d.Title for d in (app.Documents.Item(i) for i in range(1, app.Documents.Count + 1))]
    t("docs_before", lambda: docs_before)
    doc = app.OpenDocument(LAYERS)
    activate_document(app, doc)
    p1 = doc.Pages(1)
    t("alllayers_p1", lambda: [(l.Name, bool(l.Master), bool(l.IsSpecialLayer), l.Shapes.Count) for l in p1.AllLayers])
    t("layers_p1", lambda: [(l.Name, bool(l.Master)) for l in p1.Layers])
    t("masterpage_layers", lambda: [(l.Name, bool(l.Master), bool(l.IsSpecialLayer)) for l in doc.MasterPage.Layers])
    t("layer_color", lambda: [(l.Name, str(type(l.Color)), l.Color.RGBRed if hasattr(l.Color, "RGBRed") else None,
                               l.Color.Type, l.Color.Name) for l in p1.Layers])
    t("layer_color_hex", lambda: [(l.Name, l.Color.HexValue) for l in p1.Layers])
    t("active_layer", lambda: (doc.ActiveLayer.Name, p1.ActiveLayer.Name, doc.ActivePage.Index))
    t("dirty0", lambda: bool(doc.Dirty))

    def printable():
        layer = [l for l in p1.Layers if l.Name == "Art"][0]
        doc.Dirty = False
        layer.Printable = False
        d1 = bool(doc.Dirty)
        layer.Printable = True
        doc.Dirty = False
        layer.Visible = False
        d2 = bool(doc.Dirty)
        layer.Visible = True
        doc.Dirty = False
        layer.Editable = False
        d3 = bool(doc.Dirty)
        layer.Editable = True
        doc.Dirty = False
        layer.Name = "Art2"
        d4 = bool(doc.Dirty)
        layer.Name = "Art"
        return {"printable_dirty": d1, "visible_dirty": d2, "editable_dirty": d3, "rename_dirty": d4}
    t("dirty_flags", printable)

    def undo_layer_props():
        layer = [l for l in p1.Layers if l.Name == "Art"][0]
        doc.BeginCommandGroup("probe")
        layer.Printable = False
        layer.Name = "ArtX"
        doc.EndCommandGroup()
        a = (layer.Name, bool(layer.Printable))
        doc.Undo(1)
        b = (layer.Name, bool(layer.Printable))
        return {"after": a, "after_undo": b}
    t("undo_layer_props", undo_layer_props)

    def master_via_page():
        m = [l for l in p1.AllLayers if l.Master and l.Name == "Master Marks"][0]
        v = bool(m.Visible)
        m.Visible = not v
        same = [bool(l.Visible) for l in doc.Pages(2).AllLayers if l.Name == "Master Marks"]
        m.Visible = v
        return {"was": v, "page2_sees": same}
    t("master_toggle", master_via_page)

    # z-order and stacking
    t("zorder", lambda: [(l.Name, [(str(l.Shapes.Item(i).StaticID), l.Shapes.Item(i).ZOrder)
                                   for i in range(1, l.Shapes.Count + 1)]) for l in p1.Layers])
    art = [l for l in p1.Layers if l.Name == "Art"][0]
    ink = [l for l in p1.Layers if l.Name == "Ink A"][0]

    def stack():
        a = art.CreateRectangle2(1, 1, 1, 1)
        b = art.CreateRectangle2(1.5, 1.5, 1, 1)
        return [(str(art.Shapes.Item(i).StaticID), art.Shapes.Item(i).ZOrder) for i in range(1, art.Shapes.Count + 1)], \
            str(a.StaticID), str(b.StaticID)
    t("stack_after_create", stack)

    def move_to_layer():
        s = art.Shapes.Item(art.Shapes.Count)  # bottom of Art
        sid = str(s.StaticID)
        active = doc.ActiveLayer.Name
        s.MoveToLayer(ink)
        after = [str(ink.Shapes.Item(i).StaticID) for i in range(1, ink.Shapes.Count + 1)]
        s2 = art.Shapes.Item(1)
        sid2 = str(s2.StaticID)
        s2.MoveToLayer(ink)
        s2.OrderToBack()
        after2 = [str(ink.Shapes.Item(i).StaticID) for i in range(1, ink.Shapes.Count + 1)]
        return {"moved": sid, "ink_after": after, "moved2_to_back": sid2, "ink_after2": after2,
                "active_before": active, "active_after": doc.ActiveLayer.Name}
    t("move_to_layer", move_to_layer)

    def range_move():
        r = app.CreateShapeRange()
        ids = []
        for i in range(1, ink.Shapes.Count + 1):
            r.Add(ink.Shapes.Item(i))
            ids.append(str(ink.Shapes.Item(i).StaticID))
        r.MoveToLayer(art)
        return {"ink_order": ids, "art_after": [str(art.Shapes.Item(i).StaticID) for i in range(1, art.Shapes.Count + 1)]}
    t("range_move_to_layer", range_move)

    # transforms
    def transforms():
        rect = art.CreateRectangle2(2, 2, 2, 1)
        res = {"created": bounds(rect), "rot0": rect.RotationAngle}
        rect.RotateEx(30, 2, 2)
        res["rot_after"] = rect.RotationAngle
        res["rot_bounds"] = bounds(rect)
        rect.RotateEx(-30, 2, 2)
        res["back_bounds"] = bounds(rect)
        rect.StretchEx(2, 2, 2, 1.5, True)
        res["stretch_bounds"] = bounds(rect)
        rect.SetSizeEx(2, 2, 1, 1)
        res["setsizeex_bounds"] = bounds(rect)
        rect.Move(0.5, -0.25)
        res["move_bounds"] = bounds(rect)
        curve = art.CreateRectangle2(5, 5, 1, 1)
        curve.ConvertToCurves()
        curve.RotateEx(45, 5.5, 5.5)
        res["curve_rot"] = curve.RotationAngle
        ell = art.CreateEllipse2(3, 7, 1, 0.5)
        res["ellipse2"] = bounds(ell)
        line = art.CreateLineSegment(1, 9, 3, 9)
        res["line"] = bounds(line)
        try:
            line.StretchEx(1, 9, 2, 2, True)
            res["line_stretch"] = bounds(line)
        except Exception as exc:
            res["line_stretch"] = repr(exc)
        r = app.CreateShapeRange()
        r.Add(ell)
        r.Add(curve)
        g = r.Group()
        res["group"] = (str(g.StaticID), g.Type, bounds(g))
        g.RotateEx(90, g.CenterX, g.CenterY)
        res["group_rot"] = g.RotationAngle
        try:
            u = g.UngroupEx()
            res["ungroupex"] = (u.Count, [str(u.Item(i).StaticID) for i in range(1, u.Count + 1)])
        except Exception as exc:
            res["ungroupex"] = repr(exc)
        r2 = app.CreateShapeRange()
        r2.Add(ell)
        r2.Add(rect)
        r2.RotateEx(10, 3, 3)
        r2.StretchEx(3, 3, 1.1, 1.1, True)
        r2.Move(0.1, 0.1)
        r2.SetSizeEx(3, 3, 4, 4)
        res["range_ok"] = bounds(ell)
        return res
    t("transforms", transforms)

    def powerclip():
        cont = art.CreateEllipse2(4, 4, 1.5, 1.5)
        c1 = art.CreateRectangle2(3.5, 3.5, 1, 1)
        c2 = art.CreateRectangle2(3.8, 3.8, 1, 1)
        r = app.CreateShapeRange()
        r.Add(c1)
        r.Add(c2)
        ret = r.AddToPowerClip(cont, 0)
        res = {"ret": str(ret), "contents": [str(cont.PowerClip.Shapes.Item(i).StaticID)
                                            for i in range(1, cont.PowerClip.Shapes.Count + 1)],
               "ids": (str(cont.StaticID), str(c1.StaticID), str(c2.StaticID)), "c1_bounds_in": bounds(c1)}
        ex = cont.PowerClip.ExtractShapes()
        res["extracted"] = [str(ex.Item(i).StaticID) for i in range(1, ex.Count + 1)]
        res["layer_after_extract"] = [str(art.Shapes.Item(i).StaticID) for i in range(1, 5)]
        c1.AddToPowerClip(cont, 1)
        res["single_center"] = bounds(c1)
        return res
    t("powerclip", powerclip)

    def delete_range():
        a = art.CreateRectangle2(0.2, 0.2, 0.2, 0.2)
        b = art.CreateRectangle2(0.5, 0.2, 0.2, 0.2)
        r = app.CreateShapeRange()
        r.Add(a)
        r.Add(b)
        n = art.Shapes.Count
        r.Delete()
        return {"before": n, "after": art.Shapes.Count}
    t("delete_range", delete_range)

    # import on a non-active page
    def imports():
        p2 = doc.Pages(2)
        layer = [l for l in p2.Layers if l.Name == "Art"][0]
        res = {"active_page": doc.ActivePage.Index}
        n = layer.Shapes.Count
        try:
            ret = layer.Import(str(SVG))
            res["import_ret"] = str(ret)
        except Exception as exc:
            res["import_ret"] = repr(exc)
        res["svg_count"] = (n, layer.Shapes.Count, [(str(s.StaticID), s.Type, bounds(s)) for s in layer.Shapes])
        sel = doc.SelectionRange
        res["selection"] = [str(sel.Item(i).StaticID) for i in range(1, sel.Count + 1)]
        res["p1_art_count"] = art.Shapes.Count
        res["active_page_after"] = doc.ActivePage.Index
        n = layer.Shapes.Count
        try:
            f = layer.ImportEx(str(RUN / "green.png"), 0)
            res["importex_ret"] = str(f)
            f.Finish()
        except Exception as exc:
            res["importex_ret"] = repr(exc)
        res["png_count"] = (n, layer.Shapes.Count, [(str(s.StaticID), s.Type, bounds(s)) for s in layer.Shapes][:3])
        return res
    t("import", imports)
    t("unit", lambda: doc.Unit)
    doc.Dirty = False
    doc.Close()

    # Layer stacking by pixels: new document, two layers, overlapping red/blue.
    nd = app.CreateDocument()
    nd.SaveAs(NEW, app.CreateStructSaveAsOptions())
    try:
        pg = nd.ActivePage
        l1 = pg.ActiveLayer
        l2 = pg.CreateLayer("Second")
        red = l1.CreateRectangle2(10, 10, 50, 50)
        red.Fill.UniformColor.RGBAssign(255, 0, 0)
        blue = l2.CreateRectangle2(20, 20, 50, 50)
        blue.Fill.UniformColor.RGBAssign(0, 0, 255)
        out["new_layers"] = [(x.Name, bool(x.Master)) for x in pg.AllLayers]
        out["new_unit"] = nd.Unit
        nd.Save()
        return out
    finally:
        pass


r = conn.safe_call(run)
print("RESULT", r["success"], r.get("error"))
(RUN / "probe1.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
