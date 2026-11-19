r"""Build inks.cdr: a fixture for get_ink_inventory edge cases (E03). Run from server/ (in-process COM).

    python ..\scripts\validation\e03\build_inks.py <out_dir>

Page 1 "Spots":   fountains (spot->spot, spot tints), mixed-run text (spot fill + spot outline, whitespace-only
                  spot run, CMYK cyan run), PowerClip (Trans. White inside, PANTONE 485 C clipped out), contour
                  with a CMYK magenta contour fill, hidden CMYK yellow object, zero-width K outline, off-page 7508 C,
                  50 % transparent 186 C; layer "Hidden Print" (hidden, printable: 116 C); layer "No Print"
                  (RGB red); locked layer "Locked" (354 C); shared master layer "Marks" (300 C cross, every page).
Page 2 "Process": RGB and grayscale bitmaps, a registration mark, a 300 C -> CMYK yellow fountain.
Page 3 "Names":   PANTONE 354 C, PANTONE 355 C and (when CorelDRAW allows renaming a copy) a spot named "354 C".
Prints a JSON map of names to StaticIDs and notes.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, ".")
from PIL import Image  # noqa: E402

from core.connection import get_connection  # noqa: E402

conn = get_connection()
OUT = Path(sys.argv[1])
PATH = str(OUT / "inks.cdr")
COATED = "PANTONE® solid coated"


def build():
    app = conn.app
    ids, notes = {}, []

    def spot(name, tint=100):
        return app.CreateSpotColorByName(COATED, name, tint)

    def rect(layer, name, x, y, w, h, fill=None, outline=None, width=0.0):
        shape = layer.CreateRectangle2(x, y, w, h)
        shape.Name = name
        if fill is None:
            shape.Fill.ApplyNoFill()
        else:
            shape.Fill.ApplyUniformFill(fill)
        if outline is None:
            shape.Outline.SetNoOutline()
        else:
            shape.Outline.Width = width
            shape.Outline.Color = outline
        ids[name] = str(shape.StaticID)
        return shape

    for mode, name in (("RGB", "rgb.png"), ("L", "gray.png")):
        image = Image.new(mode, (60, 40), (200, 30, 30) if mode == "RGB" else 90)
        image.save(OUT / name)
    doc = app.CreateDocument()
    doc.Unit = 1  # inches
    try:
        p1 = doc.ActivePage
        p1.Name = "Spots"
        art = p1.ActiveLayer
        art.Name = "Art"
        f = rect(art, "fountain_spot_spot", 0.5, 9.5, 2, 1)
        f.Fill.ApplyFountainFill(spot("PANTONE 186 C"), spot("PANTONE 300 C"))
        f = rect(art, "fountain_spot_tints", 3, 9.5, 2, 1)
        f.Fill.ApplyFountainFill(spot("PANTONE 186 C"), spot("PANTONE 186 C", 20))
        t = art.CreateArtisticText(0.5, 8.5, "AB  CD")
        t.Name = "text_mixed"
        t.Text.Story.Size = 36
        story = t.Text.Story
        story.Range(0, 2).Fill.ApplyUniformFill(spot("PANTONE 186 C"))
        first = story.Range(0, 2)
        first.Outline.Width = 0.01
        first.Outline.Color = spot("PANTONE 300 C")
        story.Range(2, 4).Fill.ApplyUniformFill(spot("PANTONE 116 C"))
        story.Range(4, 6).Fill.ApplyUniformFill(app.CreateCMYKColor(100, 0, 0, 0))
        ids["text_mixed"] = str(t.StaticID)
        container = art.CreateEllipse2(6.5, 9, 1, 1)
        container.Name = "clip_container"
        container.Fill.ApplyNoFill()
        container.Outline.Width = 0.02
        container.Outline.Color = spot("PANTONE 300 C")
        inside = rect(art, "clip_inside_white", 6.0, 8.5, 1, 1, spot("PANTONE Trans. White"))
        outside = rect(art, "clip_outside_485", 6.0, 6.0, 0.5, 0.5, spot("PANTONE 485 C"))
        rng = app.CreateShapeRange()
        rng.Add(inside)
        rng.Add(outside)
        rng.AddToPowerClip(container, 0)
        ids["clip_container"] = str(container.StaticID)
        control = rect(art, "contour_control", 0.5, 6, 1.5, 1, spot("PANTONE 300 C"))
        control.CreateContour(1, 0.1, 1, 0, None, app.CreateCMYKColor(0, 100, 0, 0), None)
        hidden = rect(art, "hidden_object_yellow", 3, 6, 1, 1, app.CreateCMYKColor(0, 0, 100, 0))
        hidden.Visible = False
        zero = rect(art, "zero_width_outline_k", 4.5, 6, 1, 1, None, app.CreateCMYKColor(0, 0, 0, 100), 0.01)
        zero.Outline.Width = 0  # CorelDRAW turns a zero-width outline into no outline: an invisible object
        notes.append(f"zero_width_outline_k: Outline.Type={zero.Outline.Type} Width={zero.Outline.Width}")
        rect(art, "off_page_7508", -3, 5, 1, 1, spot("PANTONE 7508 C"))
        transparent = rect(art, "transparent_186", 6, 4, 1, 1, spot("PANTONE 186 C"))
        transparent.Transparency.ApplyUniformTransparency(50)
        hidden_layer = p1.CreateLayer("Hidden Print")
        rect(hidden_layer, "hidden_print_116", 0.5, 3, 1, 1, spot("PANTONE 116 C"))
        hidden_layer.Visible = False
        no_print = p1.CreateLayer("No Print")
        rect(no_print, "no_print_rgb", 2, 3, 1, 1, app.CreateRGBColor(255, 0, 0))
        no_print.Printable = False
        locked = p1.CreateLayer("Locked")
        rect(locked, "locked_354", 3.5, 3, 1, 1, spot("PANTONE 354 C"))
        locked.Editable = False
        marks = doc.MasterPage.CreateLayer("Marks")
        cross = marks.CreateLineSegment(4, 0.5, 4.5, 0.5)
        cross.Name = "master_cross_300"
        cross.Outline.Width = 0.02
        cross.Outline.Color = spot("PANTONE 300 C")
        ids["master_cross_300"] = str(cross.StaticID)

        p2 = doc.AddPages(1)
        p2.Name = "Process"
        layer2 = next(layer for layer in p2.Layers if layer.Name == "Art")  # new pages copy page 1's layers
        for name, x in (("rgb.png", 0.5), ("gray.png", 3)):
            layer2.Activate()
            layer2.Import(str(OUT / name), 0, app.CreateStructImportOptions())
            imported = doc.SelectionRange
            shape = imported.Item(1)
            shape.SetPosition(x, 9)
            shape.Name = "bitmap_" + name.split(".")[0]
            ids[shape.Name] = str(shape.StaticID)
        mark = layer2.CreateEllipse2(4.25, 1, 0.25, 0.25)
        mark.Name = "registration_mark"
        mark.Fill.ApplyNoFill()
        mark.Outline.Width = 0.01
        mark.Outline.Color = app.CreateRegistrationColor()
        ids["registration_mark"] = str(mark.StaticID)
        f = rect(layer2, "fountain_spot_cmyk", 0.5, 6, 2, 1)
        f.Fill.ApplyFountainFill(spot("PANTONE 300 C"), app.CreateCMYKColor(0, 0, 100, 0))

        p3 = doc.AddPages(1)
        p3.Name = "Names"
        layer3 = next(layer for layer in p3.Layers if layer.Name == "Art")  # new pages copy page 1's layers
        rect(layer3, "spot_354", 0.5, 9, 1, 1, spot("PANTONE 354 C"))
        rect(layer3, "spot_355", 2, 9, 1, 1, spot("PANTONE 355 C"))
        try:
            variant = spot("PANTONE 354 C").GetCopy()
            variant.SetName("354 C")
            shape = rect(layer3, "spot_variant_354", 3.5, 9, 1, 1, variant)
            notes.append(f"variant spot: name={shape.Fill.UniformColor.Name!r} IsSpot={shape.Fill.UniformColor.IsSpot}")
        except Exception as exc:
            notes.append(f"variant spot not created: {exc}")
        notes.append("layers: " + "; ".join(f"p{page.Index} {layer.Name} vis={layer.Visible} prn={layer.Printable}"
                                             for page in doc.Pages for layer in page.Layers))
        doc.Pages(1).Activate()
        doc.SaveAs(PATH, app.CreateStructSaveAsOptions())
        doc.Dirty = False
        doc.Close()
        return {"path": PATH, "ids": ids, "notes": notes}
    except Exception:
        doc.Dirty = False
        doc.Close()
        return {"error": traceback.format_exc(), "ids": ids, "notes": notes}


result = conn.safe_call(build)
print(json.dumps(result["result"] if result["success"] else result, indent=1))
