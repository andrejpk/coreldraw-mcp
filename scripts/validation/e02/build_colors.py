r"""Build e02_colors.cdr: a fixture for the color-editing tools (E02+E05). Run from server/ (in-process COM).

    python ..\scripts\validation\e02\build_colors.py <out_dir>

Page 1, inches. Layer "Art": spot_fill (186 C fill + 186 C 0.5 pt outline), tint60 (186 C at 60 %), cmyk_red
(CMYK 0/100/100/0 fill, CMYK 0/99/100/1 near-red outline 1 pt), donor (300 C fill, 300 C 2 pt outline), rgb
(RGB 0/0/255), fountain (186 C -> 300 C linear), text_mixed ("AB  CD EF": "AB" 186 C, "  " 300 C, "CD" 186 C
with a 186 C outline, " EF" CMYK cyan), text_uniform ("186 ALL" in 186 C), contour_control (300 C fill,
outside contour with a 186 C contour fill), grouped (a group of two 186 C rectangles).
Layer "Locked" (locked): locked_186 (186 C). Master layer "Marks": mark_186 (186 C).
Prints a JSON map of names to StaticIDs.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, ".")
from core.connection import get_connection  # noqa: E402

conn = get_connection()
OUT = Path(sys.argv[1])
PATH = str(OUT / "e02_colors.cdr")
COATED = "PANTONE® solid coated"


def build():
    app = conn.app
    ids = {}

    def spot(name, tint=100):
        return app.CreateSpotColorByName(COATED, name, tint)

    def rect(layer, name, x, y, fill=None, outline=None, width=0.0, w=0.8, h=0.8):
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

    doc = app.CreateDocument()
    doc.Unit = 1  # inches
    try:
        page = doc.ActivePage
        art = page.ActiveLayer
        art.Name = "Art"
        rect(art, "spot_fill", 0.5, 9, spot("PANTONE 186 C"), spot("PANTONE 186 C"), 0.5 / 72)
        rect(art, "tint60", 1.5, 9, spot("PANTONE 186 C", 60))
        rect(art, "cmyk_red", 2.5, 9, app.CreateCMYKColor(0, 100, 100, 0), app.CreateCMYKColor(0, 99, 100, 1), 1 / 72)
        rect(art, "donor", 3.5, 9, spot("PANTONE 300 C"), spot("PANTONE 300 C"), 2 / 72)
        rect(art, "rgb", 4.5, 9, app.CreateRGBColor(0, 0, 255))
        fountain = rect(art, "fountain", 5.5, 9)
        fountain.Fill.ApplyFountainFill(spot("PANTONE 186 C"), spot("PANTONE 300 C"), 1, 0.0)
        text = art.CreateArtisticText(0.5, 7.5, "AB  CD EF")
        text.Name = "text_mixed"
        story = text.Text.Story
        story.Fill.ApplyUniformFill(spot("PANTONE 186 C"))
        story.Range(2, 4).Fill.ApplyUniformFill(spot("PANTONE 300 C"))
        outlined = story.Range(4, 6)
        outlined.Outline.Width = 0.5 / 72
        outlined.Outline.Color = spot("PANTONE 186 C")
        story.Range(6, 9).Fill.ApplyUniformFill(app.CreateCMYKColor(100, 0, 0, 0))
        ids["text_mixed"] = str(text.StaticID)
        uniform = art.CreateArtisticText(4.0, 7.5, "186 ALL")
        uniform.Name = "text_uniform"
        uniform.Text.Story.Fill.ApplyUniformFill(spot("PANTONE 186 C"))
        ids["text_uniform"] = str(uniform.StaticID)
        control = rect(art, "contour_control", 1.0, 4.5, spot("PANTONE 300 C"))
        # Dynamic pywin32 requires explicit nulls for the optional Color dispatch arguments.
        control.CreateContour(1, 0.1, 1, 0, None, spot("PANTONE 186 C"), None)
        first = rect(art, "group_a", 4.0, 4.5, spot("PANTONE 186 C"))
        second = rect(art, "group_b", 5.0, 4.5, spot("PANTONE 186 C"))
        range_ = app.CreateShapeRange()
        range_.Add(first)
        range_.Add(second)
        group = range_.Group()
        group.Name = "grouped"
        ids["grouped"] = str(group.StaticID)
        locked = page.CreateLayer("Locked")
        rect(locked, "locked_186", 0.5, 2, spot("PANTONE 186 C"))
        locked.Editable = False
        marks = doc.MasterPage.CreateLayer("Marks")
        rect(marks, "mark_186", 7.0, 0.5, spot("PANTONE 186 C"), w=0.3, h=0.3)
        doc.SaveAs(PATH, app.CreateStructSaveAsOptions())
        doc.Dirty = False
        doc.Close()
        return ids
    except Exception:
        doc.Dirty = False
        doc.Close()
        return traceback.format_exc()


result = conn.safe_call(build)
print(json.dumps(result["result"]) if result["success"] and isinstance(result["result"], dict) else result)
