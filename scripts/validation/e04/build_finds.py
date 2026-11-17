r"""Build finds.cdr: clean objects and seeded artifacts for find_shapes (E04+E11). Run from server/ (in-process COM).

    python ..\scripts\validation\e04\build_finds.py <out_dir>

Page 1 (8.5 x 11 in), layer "Art" (printable, visible):
  clean:   rect_186, ellipse_outline_300 (2 pt), line_1pt (horizontal line, zero height, 1 pt K outline),
           text_art ("ART"), group_logo (logo_part_1, logo_part_2 in 300 C, nested), tri_left / tri_mirror
           (mirror images with the same bounds), same_bounds_186 / same_bounds_300 (same rectangle, different
           inks), clip_box (PowerClip container) with clip_inside (inside), reg_mark (registration crosshair
           ellipse away from the art), label_reg (registration text) beside label_ink (300 C text)
  seeded:  reg_on_art (registration fill over rect_186), trans_white_outline (186 fill, Trans. White 1 pt outline),
           blank_text ("   "), hidden_186 (Visible = False), clip_outside (PowerClip content outside its
           container), dup_text_a / dup_text_b (stacked copies, b on top), dup_tri (copy of tri_left, on top),
           speck (0.002 in square), dots (group of 25 squares of 0.002 in: fine detail), thin_outline (0.1 pt),
           no_paint (no fill, no outline), flat_fill (filled closed curve with zero height), point_line (zero length)
Layer "Hidden Print" (hidden, printable): hidden_print_300.
Layer "No Print" (non-printing): np_no_paint (no fill, no outline), np_point_line (zero length).
Layer "Locked" (locked): locked_186.
Page 2, layer "Art": edge_186 (crosses the right page edge), off_186 (entirely off page).
Prints JSON: path, ids by name (PowerClip contents get new IDs when the file is reopened: match by name),
expected {preset: {name: reason}}.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, ".")
from core.connection import get_connection  # noqa: E402

conn = get_connection()
PATH = str(Path(sys.argv[1]) / "finds.cdr")
COATED = "PANTONE® solid coated"

EXPECTED = {
    "zero_size": {"point_line": "zero_size", "flat_fill": "zero_area"},
    "invisible": {"no_paint": "no_fill_no_outline", "hidden_186": "hidden_object", "clip_outside": "clipped_out",
                  "flat_fill": "ink_without_area", "point_line": "ink_without_area",
                  "blank_text": "ink_without_area"},
    "stacked_duplicates": {"dup_text_b": "stacked_duplicate", "dup_tri": "stacked_duplicate"},
    "off_page": {"edge_186": "crosses_page_edge", "off_186": "off_page"},
    "hairline": {"thin_outline": "hairline"},
    "empty_text": {"blank_text": "whitespace_text"},
    "tiny": {"speck": "tiny", **{f"dot_{k}": "tiny_in_cluster" for k in range(25)}},
    "hidden_printable_content": {"hidden_print_300": "hidden_printable_layer"},
    "registration_on_art": {"reg_on_art": "registration_on_art"},
}


def build():
    app = conn.app
    ids = {}

    def spot(name, tint=100):
        return app.CreateSpotColorByName(COATED, name, tint)

    def paint(shape, name, fill=None, outline=None, width=0.0):
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

    def rect(layer, name, x, y, w, h, fill=None, outline=None, width=0.0):
        return paint(layer.CreateRectangle2(x, y, w, h), name, fill, outline, width)

    def triangle(layer, name, points, fill):
        curve = app.CreateCurve(layer.Page.Parent.Parent)
        path = curve.CreateSubPath(*points[0])
        for x, y in points[1:]:
            path.AppendLineSegment(x, y)
        path.Closed = True
        return paint(layer.CreateCurve(curve), name, fill)

    def text(layer, name, x, y, content, fill=None):
        shape = layer.CreateArtisticText(x, y, content)
        shape.Name = name
        if fill is not None:
            shape.Text.Story.Fill.ApplyUniformFill(fill)
        ids[name] = str(shape.StaticID)
        return shape

    doc = app.CreateDocument()
    doc.Unit = 1  # inches
    try:
        p1 = doc.ActivePage
        p1.SetSize(8.5, 11)
        art = p1.ActiveLayer
        art.Name = "Art"
        k = app.CreateCMYKColor(0, 0, 0, 100)
        rect(art, "rect_186", 1, 8, 2, 2, spot("PANTONE 186 C"))
        paint(art.CreateEllipse2(5, 9, 1, 1), "ellipse_outline_300", None, spot("PANTONE 300 C"), 2 / 72)
        paint(art.CreateLineSegment(1, 7, 3, 7), "line_1pt", None, k, 1 / 72)
        text(art, "text_art", 4, 7, "ART", spot("PANTONE 186 C"))
        first = rect(art, "logo_part_1", 6, 6.5, 0.5, 0.5, spot("PANTONE 300 C"))
        second = rect(art, "logo_part_2", 6.6, 6.5, 0.5, 0.5, spot("PANTONE 300 C"))
        inner = app.CreateShapeRange()
        inner.Add(first)
        inner.Add(second)
        inner_group = inner.Group()
        inner_group.Name = "logo_inner"
        third = rect(art, "logo_base", 6, 6, 1.1, 0.4, spot("PANTONE 186 C"))
        outer = app.CreateShapeRange()
        outer.Add(inner_group)
        outer.Add(third)
        group = outer.Group()
        group.Name = "group_logo"
        ids["group_logo"], ids["logo_inner"] = str(group.StaticID), str(inner_group.StaticID)
        triangle(art, "tri_left", [(1, 5), (2, 5), (1, 6)], spot("PANTONE 300 C"))
        triangle(art, "tri_mirror", [(1, 5), (2, 5), (2, 6)], spot("PANTONE 300 C"))
        rect(art, "same_bounds_300", 3, 5, 1, 1, spot("PANTONE 300 C"))
        rect(art, "same_bounds_186", 3, 5, 1, 1, spot("PANTONE 186 C"))
        container = rect(art, "clip_box", 5, 4, 1.5, 1.5, None, spot("PANTONE 300 C"), 0.01)
        inside = rect(art, "clip_inside", 5.2, 4.2, 0.5, 0.5, spot("PANTONE 186 C"))
        outside = rect(art, "clip_outside", 7.5, 1.5, 0.3, 0.3, spot("PANTONE 186 C"))
        clip = app.CreateShapeRange()
        clip.Add(inside)
        clip.Add(outside)
        clip.AddToPowerClip(container, 0)
        for index in range(1, container.PowerClip.Shapes.Count + 1):  # PowerClip contents get new StaticIDs
            content = container.PowerClip.Shapes.Item(index)
            ids[content.Name] = str(content.StaticID)
        mark = paint(art.CreateEllipse2(0.4, 0.4, 0.15, 0.15), "reg_mark", None, app.CreateRegistrationColor(), 0.01)
        mark.Name = "reg_mark"
        text(art, "label_ink", 3, 0.5, "INK 300", spot("PANTONE 300 C"))
        text(art, "label_reg", 3.3, 0.5, "REG LABEL", app.CreateRegistrationColor())
        rect(art, "reg_on_art", 2.5, 9.5, 1, 1, app.CreateRegistrationColor())
        rect(art, "trans_white_outline", 4, 9.5, 0.5, 0.5, spot("PANTONE 186 C"), spot("PANTONE Trans. White"), 1 / 72)
        text(art, "blank_text", 6, 8, "   ", spot("PANTONE 186 C"))
        hidden = rect(art, "hidden_186", 7, 9.5, 0.5, 0.5, spot("PANTONE 186 C"))
        hidden.Visible = False
        text(art, "dup_text_a", 1, 3, "DUP", spot("PANTONE 186 C"))
        text(art, "dup_text_b", 1, 3, "DUP", spot("PANTONE 186 C"))
        triangle(art, "dup_tri", [(1, 5), (2, 5), (1, 6)], spot("PANTONE 300 C"))
        rect(art, "speck", 4, 3, 0.002, 0.002, spot("PANTONE 186 C"))
        dots = app.CreateShapeRange()
        for index in range(25):
            dots.Add(rect(art, f"dot_{index}", 5 + (index % 5) * 0.01, 2 + (index // 5) * 0.01, 0.002, 0.002,
                          spot("PANTONE 300 C")))
        dot_group = dots.Group()
        dot_group.Name = "dots"
        ids["dots"] = str(dot_group.StaticID)
        rect(art, "thin_outline", 6.5, 2.5, 1, 0.5, None, k, 0.1 / 72)
        rect(art, "no_paint", 2, 2, 0.5, 0.5)
        triangle(art, "flat_fill", [(3, 1.5), (4, 1.5), (3.5, 1.5)], spot("PANTONE 186 C"))
        paint(art.CreateLineSegment(1, 1.5, 1, 1.5), "point_line", None, k, 0.01)
        hidden_layer = p1.CreateLayer("Hidden Print")
        rect(hidden_layer, "hidden_print_300", 1, 0.8, 0.5, 0.5, spot("PANTONE 300 C"))
        hidden_layer.Visible = False
        no_print = p1.CreateLayer("No Print")
        rect(no_print, "np_no_paint", 7, 7, 0.5, 0.5)
        paint(no_print.CreateLineSegment(7, 6.5, 7, 6.5), "np_point_line", None, k, 0.01)
        no_print.Printable = False
        locked = p1.CreateLayer("Locked")
        rect(locked, "locked_186", 7, 4, 0.5, 0.5, spot("PANTONE 186 C"))
        locked.Editable = False
        p2 = doc.AddPages(1)
        art2 = next(layer for layer in p2.Layers if layer.Name == "Art")
        rect(art2, "edge_186", 8, 5, 1, 1, spot("PANTONE 186 C"))
        rect(art2, "off_186", -3, 5, 1, 1, spot("PANTONE 186 C"))
        rect(art2, "p2_clean", 2, 2, 1, 1, spot("PANTONE 186 C"))
        doc.Pages(1).Activate()
        doc.SaveAs(PATH, app.CreateStructSaveAsOptions())
        doc.Dirty = False
        doc.Close()
        return {"path": PATH, "ids": ids, "expected": EXPECTED}
    except Exception:
        doc.Dirty = False
        doc.Close()
        return {"error": traceback.format_exc(), "ids": ids}


result = conn.safe_call(build)
print(json.dumps(result["result"] if result["success"] else result, indent=1))
