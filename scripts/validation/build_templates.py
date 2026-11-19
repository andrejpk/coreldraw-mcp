"""Build the disposable CorelDRAW template library and its manifests.

Usage (from server/, with CorelDRAW running and no other COM client active):
    build_templates.py <out_dir> [--profiles <sample_profiles.json>] [--only name,name]

Builds small .cdr fixtures in <out_dir>, closes every document it creates, and writes
<out_dir>\\MANIFEST.json (pages, layers, named shapes with StaticIDs and intended properties)
and <out_dir>\\MANIFEST.md (human-readable, plus the sample profiles when --profiles is given).
Rerunning overwrites the templates; StaticIDs may change, so always read them from the manifest.
All measurements are in inches (doc.Unit = 1, cdrInch). Shapes are found by their Name.
"""
import argparse, datetime, json, ntpath, os, sys, time, traceback; sys.path.insert(0, ".")
from collections import Counter
from core.connection import get_connection
from core.structure import layer_view, walk_layer
from tools import inspection
conn = get_connection()

INCH = 1  # cdrInch (cdrMillimeter is 3)
SOLID = "PANTONE® solid coated"
CDR_CONTOUR_INSIDE, CDR_CONTOUR_OUTSIDE = 0, 1  # cdrContourDirection; 2 is to-center (verified by bounds)


# --------------------------------------------------------------------------- helpers
def spot(app, name, tint=100):
    c = app.CreateSpotColorByName(SOLID, name, tint)
    if c is None or not (c.Type == 1 or c.IsSpot) or c.Name != name:
        raise RuntimeError(f"Could not create spot {name}")
    return c


def white_spot(app, doc, tint=100):
    """'PANTONE Trans. White', the underbase spot the shop's real files use (in PANTONE solid coated)."""
    return spot(app, "PANTONE Trans. White", tint)


def cmyk(app, c, m, y, k):
    return app.CreateCMYKColor(c, m, y, k)


def color_desc(c):
    """Intended color in the same shape as inspect_shapes reports it."""
    t = c.Type
    if t == 20:
        return {"kind": "registration"}
    if t == 1 or c.IsSpot:
        return {"kind": "spot", "name": c.Name, "tint": c.Tint}
    if t == 2:
        return {"kind": "process", "model": "CMYK", "components": [c.CMYKCyan, c.CMYKMagenta, c.CMYKYellow, c.CMYKBlack]}
    if t == 5:
        return {"kind": "process", "model": "RGB", "components": [c.RGBRed, c.RGBGreen, c.RGBBlue]}
    return {"kind": "unknown", "type": t}


class Book:
    """Records named shapes and their intended properties for the manifest."""

    def __init__(self):
        self.items = []

    def add(self, shape, name, **intended):
        shape.Name = name
        self.items.append((shape, name, intended))
        return shape

    def rows(self):
        out = []
        for shape, name, intended in self.items:
            row = {"name": name, "intended": intended}
            for key, read in (("shape_id", lambda: str(shape.StaticID)), ("type", lambda: shape.Type),
                              ("layer", lambda: shape.Layer.Name), ("page", lambda: shape.Page.Index),
                              ("bounds", lambda: [round(v, 4) for v in (shape.LeftX, shape.BottomY,
                                                                        shape.SizeWidth, shape.SizeHeight)])):
                try:
                    row[key] = read()
                except Exception as exc:
                    row[key] = None
                    row.setdefault("unreadable", {})[key] = str(exc)[:120]
            out.append(row)
        return out


def fill(shape, color):
    if color is None:
        shape.Fill.ApplyNoFill()
    else:
        shape.Fill.ApplyUniformFill(color)
    return shape


def outline(shape, color=None, width=0.0):
    if color is None:
        shape.Outline.SetNoOutline()
    else:
        shape.Outline.Width = width
        shape.Outline.Color = color
    return shape


def rect(book, layer, name, x, y, w, h, fill_color, outline_color=None, width=0.0, **intended):
    r = layer.CreateRectangle2(x, y, w, h)
    fill(r, fill_color); outline(r, outline_color, width)
    meta = {"fill": color_desc(fill_color) if fill_color is not None else None,
            "outline": color_desc(outline_color) if outline_color is not None else None}
    if outline_color is not None:
        meta["outline_width"] = width
    meta.update(intended)
    return book.add(r, name, **meta)


def group(app, shapes):
    rng = app.CreateShapeRange()
    for s in shapes:
        rng.Add(s)
    return rng.Group()


def set_text_runs(app, text_shape, runs):
    """runs: list of (start, end, color) on the story (zero-based, end exclusive)."""
    story = text_shape.Text.Story
    for start, end, color in runs:
        story.Range(start, end).Fill.ApplyUniformFill(color)


def layer_rows(doc):
    pages = []
    for p in range(1, doc.Pages.Count + 1):
        page = doc.Pages(p)
        layers = []
        for i in range(1, page.AllLayers.Count + 1):
            layer = page.AllLayers.Item(i)
            special = None
            for attr, key in (("IsGuidesLayer", "guides"), ("IsDesktopLayer", "desktop"), ("IsGridLayer", "grid")):
                try:
                    if getattr(layer, attr):
                        special = key
                except Exception:
                    pass
            row = {"name": layer.Name, "master": bool(layer.Master), "visible": bool(layer.Visible),
                   "printable": bool(layer.Printable), "editable": bool(layer.Editable),
                   "top_level_shapes": layer.Shapes.Count}
            if special:
                row["special"] = special
            layers.append(row)
        pages.append({"index": p, "name": page.Name, "width_in": round(page.SizeWidth, 4),
                      "height_in": round(page.SizeHeight, 4), "layers": layers})
    return pages


# --------------------------------------------------------------------------- templates
def t_text(app, doc, book, notes):
    """Fill runs (spot + process + whitespace), artistic, paragraph, multi-line text."""
    page = doc.ActivePage
    L = page.ActiveLayer; L.Name = "Text"
    s186, s300, k100 = spot(app, "PANTONE 186 C"), spot(app, "PANTONE 300 C"), cmyk(app, 0, 0, 0, 100)
    t = L.CreateArtisticText(1, 9, "RED  BLACK"); t.Text.Story.Size = 36
    set_text_runs(app, t, [(0, 10, s186), (3, 5, s300), (5, 10, k100)])
    book.add(t, "text_runs", text="RED  BLACK", text_kind="artistic",
             runs=[{"range": [0, 3], "text": "RED", "fill": color_desc(s186)},
                   {"range": [3, 5], "text": "  ", "fill": color_desc(s300), "whitespace": True},
                   {"range": [5, 10], "text": "BLACK", "fill": color_desc(k100)}])
    s186_50 = spot(app, "PANTONE 186 C", 50)
    t = L.CreateArtisticText(1, 8, "TINT"); t.Text.Story.Size = 36
    set_text_runs(app, t, [(0, 4, s186), (2, 4, s186_50)])
    book.add(t, "text_tint_runs", text="TINT", text_kind="artistic",
             runs=[{"range": [0, 2], "text": "TI", "fill": color_desc(s186)},
                   {"range": [2, 4], "text": "NT", "fill": color_desc(s186_50)}])
    s354 = spot(app, "PANTONE 354 C")
    t = L.CreateArtisticText(1, 6.5, "Line one\rLine two\rLine three"); t.Text.Story.Size = 24
    t.Text.Story.Fill.ApplyUniformFill(s354)
    book.add(t, "text_multiline_artistic", text_kind="artistic", lines=3, fill=color_desc(s354))
    t = L.CreateParagraphText(1, 4.5, 5, 1.5,
                              "Paragraph text frame line one.\rSecond paragraph wraps across the frame width "
                              "so it takes more than one line.\rThird.")
    t.Text.Story.Size = 14
    t.Text.Story.Fill.ApplyUniformFill(s300)
    book.add(t, "text_paragraph", text_kind="paragraph", paragraphs=3, fill=color_desc(s300))
    t = L.CreateArtisticText(5, 9, "OUTLINE"); t.Text.Story.Size = 36
    t.Fill.ApplyUniformFill(s186); outline(t, k100, 0.01)
    book.add(t, "text_outlined", text_kind="artistic", fill=color_desc(s186), outline=color_desc(k100),
             outline_width=0.01)
    t = L.CreateArtisticText(5, 8, "RGB text"); t.Text.Story.Size = 24
    rgb = app.CreateRGBColor(0, 0, 255)
    t.Fill.ApplyUniformFill(rgb)
    book.add(t, "text_rgb", text_kind="artistic", fill=color_desc(rgb))


def t_structure(app, doc, book, notes):
    """Flat group, 4-level nested groups, PowerClip with contents, nested PowerClip."""
    page = doc.ActivePage
    L = page.ActiveLayer; L.Name = "Groups"
    s186, s300, s354 = spot(app, "PANTONE 186 C"), spot(app, "PANTONE 300 C"), spot(app, "PANTONE 354 C")
    a = rect(book, L, "grp_a", 1, 9, 1, 1, s186)
    b = rect(book, L, "grp_b", 2.5, 9, 1, 1, s300)
    book.add(group(app, [a, b]), "group_flat", children=["grp_a", "grp_b"], depth=1)
    leaf = rect(book, L, "deep_leaf", 1, 6, 0.5, 0.5, s354, depth=4)
    leaf2 = rect(book, L, "deep_leaf2", 1.75, 6, 0.5, 0.5, s186, depth=4)
    g4 = book.add(group(app, [leaf, leaf2]), "nest_l4", children=["deep_leaf", "deep_leaf2"], depth=4)
    e = L.CreateEllipse2(3, 6.25, 0.25); fill(e, s300); outline(e)
    book.add(e, "nest_l3_ellipse", fill=color_desc(s300), depth=3)
    g3 = book.add(group(app, [g4, e]), "nest_l3", children=["nest_l4", "nest_l3_ellipse"], depth=3)
    r = rect(book, L, "nest_l2_rect", 4, 6, 0.5, 0.5, s186, depth=2)
    g2 = book.add(group(app, [g3, r]), "nest_l2", children=["nest_l3", "nest_l2_rect"], depth=2)
    r = rect(book, L, "nest_l1_rect", 5, 6, 0.5, 0.5, s300, depth=1)
    book.add(group(app, [g2, r]), "nest_l1", children=["nest_l2", "nest_l1_rect"], depth=1,
             note="outermost; deep_leaf is 4 groups deep")

    P = page.CreateLayer("PowerClip")
    container = P.CreateEllipse2(2.5, 3, 1.5); fill(container, None); outline(container, cmyk(app, 0, 0, 0, 100), 0.02)
    c1 = rect(book, P, "clip_rect", 1, 2, 3, 1, s186, powerclip_content_of="clip_container")
    c2 = P.CreateArtisticText(1.5, 3.2, "CLIP"); c2.Text.Story.Size = 48; c2.Fill.ApplyUniformFill(s300)
    book.add(c2, "clip_text", fill=color_desc(s300), powerclip_content_of="clip_container")
    g1 = rect(book, P, "clip_grp_a", 2, 4, 0.4, 0.4, s354)
    g2_ = rect(book, P, "clip_grp_b", 2.6, 4, 0.4, 0.4, s354)
    cg = book.add(group(app, [g1, g2_]), "clip_group", powerclip_content_of="clip_container")
    rng = app.CreateShapeRange()
    for s in (c1, c2, cg):
        rng.Add(s)
    rng.AddToPowerClip(container, 0)
    book.add(container, "clip_container", fill=None, outline=color_desc(cmyk(app, 0, 0, 0, 100)),
             powerclip_contents=["clip_rect", "clip_text", "clip_group"], powerclip_recursive_count=5)
    # nested PowerClip: an inner container (with its own content) clipped inside an outer one
    outer = P.CreateRectangle2(5, 1.5, 2.5, 2.5); fill(outer, None); outline(outer, s186, 0.02)
    inner = P.CreateEllipse2(6.25, 2.75, 0.9); fill(inner, None); outline(inner, s300, 0.02)
    deep = rect(book, P, "clip_nested_content", 5.5, 2, 1.5, 1.5, s354, powerclip_content_of="clip_inner")
    deep.AddToPowerClip(inner, 0)
    book.add(inner, "clip_inner", fill=None, outline=color_desc(s300), powerclip_content_of="clip_outer",
             powerclip_contents=["clip_nested_content"])
    inner.AddToPowerClip(outer, 0)
    book.add(outer, "clip_outer", fill=None, outline=color_desc(s186), powerclip_contents=["clip_inner"])


def t_colors(app, doc, book, notes):
    """Spot tints, near-duplicate spots, white spot, CMYK, RGB, registration, outline/fill combinations."""
    page = doc.ActivePage
    L = page.ActiveLayer; L.Name = "Spots"
    for i, tint in enumerate((100, 60, 25, 5)):
        rect(book, L, f"spot_186_t{tint}", 1 + i * 1.2, 9, 1, 1, spot(app, "PANTONE 186 C", tint))
    rect(book, L, "near_354", 1, 7.5, 1, 1, spot(app, "PANTONE 354 C"), near_duplicate_of="near_355")
    rect(book, L, "near_355", 2.2, 7.5, 1, 1, spot(app, "PANTONE 355 C"), near_duplicate_of="near_354")
    rect(book, L, "near_354_t50", 3.4, 7.5, 1, 1, spot(app, "PANTONE 354 C", 50))
    w = white_spot(app, doc)
    notes.append(f"white spot used: {w.Name!r} (type {w.Type}, IsSpot {w.IsSpot})")
    rect(book, L, "white_spot", 4.6, 7.5, 1, 1, w, cmyk(app, 0, 0, 0, 100), 0.01)
    rect(book, L, "white_spot_t50", 5.8, 7.5, 1, 1, white_spot(app, doc, 50))
    P = page.CreateLayer("Process")
    rect(book, P, "cmyk_red", 1, 5.5, 1, 1, cmyk(app, 0, 100, 100, 0))
    rect(book, P, "cmyk_black", 2.2, 5.5, 1, 1, cmyk(app, 0, 0, 0, 100))
    rect(book, P, "cmyk_rich_black", 3.4, 5.5, 1, 1, cmyk(app, 60, 40, 40, 100))
    rect(book, P, "rgb_red", 4.6, 5.5, 1, 1, app.CreateRGBColor(255, 0, 0))
    rect(book, P, "rgb_blue", 5.8, 5.5, 1, 1, app.CreateRGBColor(0, 0, 255))
    rect(book, P, "registration_fill", 7, 5.5, 1, 1, app.CreateRegistrationColor())
    O = page.CreateLayer("Outlines")
    rect(book, O, "fill_spot_outline_cmyk", 1, 3.5, 1, 1, spot(app, "PANTONE 186 C"), cmyk(app, 0, 0, 0, 100), 0.03)
    rect(book, O, "fill_cmyk_outline_spot", 2.2, 3.5, 1, 1, cmyk(app, 0, 100, 0, 0), spot(app, "PANTONE 300 C"), 0.03)
    rect(book, O, "fill_spot_outline_other_spot", 3.4, 3.5, 1, 1, spot(app, "PANTONE 354 C"),
         spot(app, "PANTONE 186 C"), 0.05)
    rect(book, O, "outline_only_spot", 4.6, 3.5, 1, 1, None, spot(app, "PANTONE 354 C"), 0.05)
    rect(book, O, "outline_only_spot_tint", 5.8, 3.5, 1, 1, None, spot(app, "PANTONE 186 C", 40), 0.05)
    rect(book, O, "outline_only_registration", 7, 3.5, 1, 1, None, app.CreateRegistrationColor(), 0.01)
    rect(book, O, "outline_only_rgb", 1, 2, 1, 1, None, app.CreateRGBColor(0, 128, 0), 0.04)


def t_effects(app, doc, book, notes):
    """Spot contour, blend, drop shadow, fill/outline overprint flags."""
    page = doc.ActivePage
    L = page.ActiveLayer; L.Name = "Effects"
    s186, s300 = spot(app, "PANTONE 186 C"), spot(app, "PANTONE 300 C")
    src = rect(book, L, "contour_control", 1, 8, 2, 1.5, s300)
    eff = src.CreateContour(CDR_CONTOUR_OUTSIDE, 0.1, 1, 0, None, s186, None)
    notes.append(f"contour effect type {eff.Type}")
    src.Name = "contour_control"
    book.items[-1][2].update(effect="contour", contour_direction="outside", contour_offset=0.1, contour_steps=1,
                              contour_fill=color_desc(s186))
    try:
        ins = rect(book, L, "contour_inside_control", 4, 8, 2, 1.5, s186)
        ins.CreateContour(CDR_CONTOUR_INSIDE, 0.15, 2, 0, None, spot(app, "PANTONE 186 C", 40), None)
        book.items[-1][2].update(effect="contour", contour_direction="inside", contour_offset=0.15, contour_steps=2,
                                  contour_fill=color_desc(spot(app, "PANTONE 186 C", 40)))
    except Exception as exc:
        notes.append(f"inside contour failed: {exc}")
    # No blend: Shape.CreateBlend cannot be called through pywin32 late binding on CorelDRAW 2022
    # ("The Python instance can not be converted to a COM object", also with all 12 arguments).
    notes.append("blend not creatable through pywin32 (Shape.CreateBlend argument conversion fails)")
    try:
        s = rect(book, L, "shadow_source", 1, 3, 2, 1, s300)
        s.CreateDropShadow(0, 60, 5, 0.1, -0.1, cmyk(app, 0, 0, 0, 100))
        book.items[-1][2].update(effect="drop_shadow")
        notes.append("drop shadow created")
    except Exception as exc:
        notes.append(f"drop shadow failed: {exc}")
    O = page.CreateLayer("Overprint")
    k = cmyk(app, 0, 0, 0, 100)
    for name, of, oo in (("op_none", False, False), ("op_fill", True, False), ("op_outline", False, True),
                         ("op_both", True, True)):
        r = rect(book, O, name, 4 + ["op_none", "op_fill", "op_outline", "op_both"].index(name) * 1.1, 3, 1, 1,
                 s186, k, 0.04, overprint_fill=of, overprint_outline=oo)
        r.OverprintFill = of; r.OverprintOutline = oo


def t_layers(app, doc, book, notes):
    """Three pages, same-named layers with different flags, master layer, hidden-printable, locked."""
    k = cmyk(app, 0, 0, 0, 100)
    p1 = doc.Pages(1); p1.Name = "Front"
    p2 = doc.AddPages(1); p2.Name = "Back"
    p3 = doc.AddPages(1); p3.Name = "Sleeve"
    spec = {  # page -> [(layer, visible, printable, editable, color)]
        1: [("Art", True, True, True, spot(app, "PANTONE 186 C")),
            ("Ink A", True, True, True, spot(app, "PANTONE 300 C")),
            ("Halftone", False, True, True, spot(app, "PANTONE 354 C")),
            ("Locked", True, True, False, k)],
        2: [("Art", True, True, True, spot(app, "PANTONE 186 C")),
            ("Ink A", False, False, True, spot(app, "PANTONE 485 C"))],
        3: [("Ink A", True, False, True, spot(app, "PANTONE 300 C")),
            ("Ink B", True, True, True, spot(app, "PANTONE 116 C"))],
    }
    for p, layers in spec.items():
        page = doc.Pages(p)
        for i, (name, vis, prn, edit, color) in enumerate(layers):
            # page.ActiveLayer can be a master layer; take the page's own default layer explicitly
            layer = [x for x in page.Layers if x.Name == "Layer 1" and not x.Master][0] if i == 0 \
                else page.CreateLayer(name)
            layer.Name = name
            rect(book, layer, f"p{p}_{name.lower().replace(' ', '_')}", 1 + i * 1.5, 5, 1, 1, color,
                 layer_visible=vis, layer_printable=prn, layer_editable=edit)
            layer.Visible, layer.Printable = vis, prn
            layer.Editable = edit
    ml = doc.MasterPage.CreateLayer("Master Marks")
    rect(book, ml, "master_mark", 0.25, 0.25, 0.5, 0.5, None, app.CreateRegistrationColor(), 0.01, master=True)
    doc.Pages(1).Activate()


def t_artifacts(app, doc, book, notes):
    """Cleanup targets: degenerate curves, invisible shape, stacked duplicate, empty group, off-page, hairline."""
    page = doc.ActivePage
    L = page.ActiveLayer; L.Name = "Artifacts"
    s186 = spot(app, "PANTONE 186 C")
    z = L.CreateLineSegment(1, 9, 1, 9); outline(z, cmyk(app, 0, 0, 0, 100), 0.01)
    book.add(z, "zero_size_line", artifact="zero length line (two coincident nodes)")
    try:
        c = app.CreateCurve(doc)
        sp = c.CreateSubPath(2, 9)
        sp.AppendLineSegment(3, 9); sp.AppendLineSegment(4, 9); sp.Closed = True
        s = L.CreateCurve(c); fill(s, s186); outline(s)
        book.add(s, "zero_area_curve", artifact="closed curve with collinear nodes: width 2, height 0",
                 fill=color_desc(s186))
    except Exception as exc:
        notes.append(f"zero-area curve failed: {exc}")
    rect(book, L, "no_fill_no_outline", 1, 7, 1, 1, None, artifact="no fill and no outline (invisible)")
    orig = rect(book, L, "dup_original", 3, 7, 1, 1, s186, artifact="exact stacked duplicate pair")
    dup = orig.Duplicate(0, 0)
    book.add(dup, "dup_copy", fill=color_desc(s186), outline=None, artifact="exact copy on top of dup_original")
    try:
        a = L.CreateRectangle2(5, 7, 0.5, 0.5); b = L.CreateRectangle2(6, 7, 0.5, 0.5)
        g = group(app, [a, b]); sid = g.StaticID
        g.Shapes.Item(2).Delete(); g.Shapes.Item(1).Delete()
        survivors = [s for s in L.Shapes if s.StaticID == sid]
        if survivors:
            book.add(survivors[0], "empty_group", artifact="group with no children")
        else:
            notes.append("empty group not creatable: deleting the last child removed the group")
    except Exception as exc:
        notes.append(f"empty group failed: {exc}")
    rect(book, L, "off_page", -4, 5, 1.5, 1.5, s186, artifact="entirely off the page (left of x=0)")
    rect(book, L, "partly_off_page", page.SizeWidth - 0.5, 5, 1.5, 1.5, s186, artifact="straddles the right page edge")
    h = L.CreateRectangle2(1, 3, 2, 1); fill(h, None); outline(h, cmyk(app, 0, 0, 0, 100), 0.003)
    book.add(h, "hairline_cmyk", fill=None, outline=color_desc(cmyk(app, 0, 0, 0, 100)), outline_width=0.003,
             artifact="hairline (0.003 in) outline in process black")
    rect(book, L, "tiny_object", 4, 3, 0.002, 0.002, s186, artifact="0.002 in square speck")


def t_big_flat(app, doc, book, notes, target=4000):
    page = doc.ActivePage
    L = page.ActiveLayer; L.Name = "Flat"
    colors = [spot(app, "PANTONE 186 C"), spot(app, "PANTONE 300 C", 60), cmyk(app, 0, 0, 0, 100),
              app.CreateRGBColor(255, 0, 0), None, spot(app, "PANTONE 354 C"), None]
    for i, c in enumerate(colors):
        r = L.CreateRectangle2(0.2 + i * 0.15, 0.2, 0.1, 0.1)
        fill(r, c)
        if i % 3 == 0:
            outline(r)
        if i in (1, 5):
            r.OverprintFill = True
        if i == 2:
            r.OverprintOutline = True
        r.Name = f"seed_{i}"
    step = 0.15
    while L.Shapes.Count < target:
        need = target - L.Shapes.Count
        rng = L.Shapes.All()
        if rng.Count > need:
            rng = L.Shapes.Range(list(range(1, need + 1)))
        rng.Duplicate(0, step); step *= 2
        if step > 8:
            step = 0.15
    notes.append(f"{L.Shapes.Count} flat objects; names are seed_0..seed_6 repeated (duplicates keep names)")


def t_screen_job(app, doc, book, notes):
    """13x18 in screen-print job: 3 spot inks, choked white underbase with overprint, film titles, reg marks."""
    page = doc.ActivePage
    page.SetSize(13, 18)
    inks = {"PANTONE 186 C": spot(app, "PANTONE 186 C"), "PANTONE 300 C": spot(app, "PANTONE 300 C"),
            "PANTONE 116 C": spot(app, "PANTONE 116 C")}
    white = white_spot(app, doc)
    reg = app.CreateRegistrationColor()
    choke = 0.03
    # layers bottom to top
    H = page.ActiveLayer; H.Name = "Halftone Scale"
    U = page.CreateLayer("White Underbase")
    ink_layers = {name: page.CreateLayer(name) for name in inks}
    T = page.CreateLayer("Film Titles")
    R = page.CreateLayer("Registration")
    # artwork: three non-overlapping ink areas (inks never overlap each other, so overprint only affects white)
    art = [("art_circle", "PANTONE 186 C", "ellipse", (6.5, 12, 3.0)),
           ("art_band", "PANTONE 300 C", "rect", (3, 7.5, 7, 1.5)),
           ("art_block_left", "PANTONE 116 C", "rect", (3, 4.5, 2.5, 2)),
           ("art_block_right", "PANTONE 116 C", "rect", (7.5, 4.5, 2.5, 2))]
    for name, ink, kind, geo in art:
        layer = ink_layers[ink]
        if kind == "ellipse":
            cx, cy, r = geo
            s = layer.CreateEllipse2(cx, cy, r)
            u = U.CreateEllipse2(cx, cy, r - choke)
        else:
            x, y, w, h = geo
            s = layer.CreateRectangle2(x, y, w, h)
            u = U.CreateRectangle2(x + choke, y + choke, w - 2 * choke, h - 2 * choke)
        fill(s, inks[ink]); outline(s); s.OverprintFill = True
        book.add(s, name, fill=color_desc(inks[ink]), outline=None, overprint_fill=True, ink=ink)
        fill(u, white); outline(u); u.OverprintFill = True
        book.add(u, "ub_" + name.removeprefix("art_"), fill=color_desc(white), outline=None, overprint_fill=True,
                 choke_in=choke, underbase_for=name)
    # film titles in each ink (and white), along the bottom margin
    titles = [("title_white", "WHITE UNDERBASE", white)] + [
        ("title_" + n.split()[1].lower().replace(" ", ""), n, c) for n, c in inks.items()]
    for i, (name, text, color) in enumerate(titles):
        t = T.CreateArtisticText(1 + i * 3, 1.2, text); t.Text.Story.Size = 18
        t.Fill.ApplyUniformFill(color); outline(t)
        book.add(t, name, text=text, fill=color_desc(color), text_kind="artistic")
    # registration marks: circle + crosshair, registration color, grouped
    for name, (cx, cy) in (("reg_top", (6.5, 16.8)), ("reg_left", (0.8, 9)), ("reg_right", (12.2, 9))):
        c = R.CreateEllipse2(cx, cy, 0.25); fill(c, None); outline(c, reg, 0.01)
        h = R.CreateLineSegment(cx - 0.4, cy, cx + 0.4, cy); outline(h, reg, 0.01)
        v = R.CreateLineSegment(cx, cy - 0.4, cx, cy + 0.4); outline(v, reg, 0.01)
        for part, shape in (("circle", c), ("h", h), ("v", v)):
            book.add(shape, f"{name}_{part}", fill=None, outline=color_desc(reg), outline_width=0.01)
        book.add(group(app, [c, h, v]), name, mark="registration",
                 children=[f"{name}_circle", f"{name}_h", f"{name}_v"])
    # halftone scale: tint steps for each ink on a hidden-but-printable layer
    for row, (ink, color) in enumerate(inks.items()):
        for col, tint in enumerate(range(10, 101, 10)):
            s = H.CreateRectangle2(1.5 + col * 1.0, 15.5 - row * 0.7, 0.9, 0.5)
            c = spot(app, ink, tint)
            fill(s, c); outline(s)
            book.add(s, f"ht_{ink.split()[1]}_{tint}", fill=color_desc(c), layer_visible=False, layer_printable=True)
    H.Visible, H.Printable = False, True
    notes.append(f"underbase spot: {white.Name!r}; inks {list(inks)}; expected plates: "
                 f"{[white.Name] + list(inks)} (registration marks print on every plate)")


TEMPLATES = [
    ("text_runs", t_text, "Text with multiple fill runs (spot + process + whitespace), tints, artistic, paragraph, "
                          "multi-line, outlined and RGB text."),
    ("structure", t_structure, "Flat group, 4-level nested groups, PowerClip with rect/text/group contents, "
                               "nested PowerClip."),
    ("colors", t_colors, "Spot tints, near-duplicate spots (354 vs 355), white spot, CMYK, RGB, registration, "
                         "fill/outline color combinations and outline-only shapes."),
    ("effects", t_effects, "Spot contours (outside/inside), drop shadow, fill/outline overprint matrix (no blend: "
                         "Shape.CreateBlend fails through pywin32)."),
    ("layers", t_layers, "Three named pages, same-named layers with different flags, master layer, "
                         "hidden-but-printable and locked layers."),
    ("artifacts", t_artifacts, "Cleanup targets: zero-length line, zero-area curve, invisible shape, stacked "
                               "duplicate, off-page objects, hairline, speck (no empty group: CorelDRAW deletes a "
                               "group with its last child)."),
    ("screen_job", t_screen_job, "Realistic 13x18 in screen-print job: 3 spot inks, choked white underbase with "
                                 "overprint, film titles, registration marks, hidden-but-printable halftone scale."),
    ("big_flat", t_big_flat, "One flat layer of 4,000 rectangles with mixed spot/process/RGB/no fill and overprint."),
]


# --------------------------------------------------------------------------- build driver
def build_one(out_dir, name, fn, description):
    path = ntpath.join(out_dir, name + ".cdr")
    notes, book = [], Book()
    t0 = time.perf_counter()

    def make():
        app = conn.app
        doc = app.CreateDocument()
        try:
            doc.Unit = INCH
            fn(app, doc, book, notes)
            doc.Pages(1).Activate()
            doc.SaveAs(path, app.CreateStructSaveAsOptions())
            return {"file": name + ".cdr", "description": description, "unit": "inch",
                    "pages": layer_rows(doc), "shapes": book.rows(), "notes": notes}
        finally:
            doc.Dirty = False
            doc.Close()
    r = conn.safe_call(make)
    if not r["success"]:
        raise RuntimeError(f"{name}: {r['error']}")
    entry = r["result"]
    entry["build_seconds"] = round(time.perf_counter() - t0, 2)
    entry["sha256"] = sha256(path)
    return entry


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def big_inventory(out_dir):
    """Reopen big_flat.cdr and tally its inventory with batched reads (chunked under the 60 s limit)."""
    path = ntpath.join(out_dir, "big_flat.cdr")

    def doc():
        for i in range(1, conn.app.Documents.Count + 1):
            d = conn.app.Documents.Item(i)
            if d.FullFileName and ntpath.normcase(d.FullFileName) == ntpath.normcase(path):
                return d
        return conn.app.OpenDocument(path)

    def structure():
        layer = [x for x in doc().Pages(1).AllLayers if x.Name == "Flat"][0]
        return walk_layer(layer).entries
    entries = conn.safe_call(structure)["result"]
    tally = Counter()
    for start in range(0, len(entries), 1000):
        def chunk(start=start):
            layer = [x for x in doc().Pages(1).AllLayers if x.Name == "Flat"][0]
            recs = inspection._read_records(layer_view(layer), {}, entries[start:start + 1000],
                                            {"name", "fill", "outline", "overprint_fill", "overprint_outline"})
            return [(r["name"], json.dumps(r["fill"], sort_keys=True), r["outline"]["type"],
                     r["overprint_fill"], r["overprint_outline"]) for r in recs]
        res = conn.safe_call(chunk)
        if not res["success"]:
            raise RuntimeError(res["error"])
        tally.update(res["result"])

    def close():
        d = doc(); d.Dirty = False; d.Close()
    conn.safe_call(close)
    return [{"name": n, "fill": json.loads(f), "outline_type": o, "overprint_fill": of, "overprint_outline": oo,
             "count": c} for (n, f, o, of, oo), c in sorted(tally.items())]


# --------------------------------------------------------------------------- manifest
def color_text(c):
    if not c:
        return "none"
    if c["kind"] == "spot":
        return f"{c['name']} @{c['tint']}"
    if c["kind"] == "process":
        return f"{c['model']} {tuple(c['components'])}"
    return c["kind"]


def write_md(out_dir, manifest):
    lines = ["# CorelDRAW template library", "",
             f"Built {manifest['built']} by `scripts/validation/build_templates.py` (CorelDRAW {manifest['corel_version']}).",
             "Rebuild: `cd server; python ..\\scripts\\validation\\build_templates.py C:\\CorelMcpLab\\templates "
             "--profiles C:\\CorelMcpLab\\templates\\sample_profiles.json`.",
             "", "**Masters are read-only.** Copy a template into `C:\\CorelMcpLab\\runs\\<exp-id>\\` before opening it. "
             "StaticIDs change on every rebuild; read them from `MANIFEST.json` and find shapes by `Name`.",
             "Units are inches. Shape types: 1 rectangle, 2 ellipse, 3 curve, 6 text, 7 group, 13 contour group.", ""]
    lines += ["| Template | Pages | Named shapes | Purpose |", "| --- | --- | --- | --- |"]
    for t in manifest["templates"]:
        lines.append(f"| `{t['file']}` | {len(t['pages'])} | {len(t['shapes'])} | {t['description']} |")
    for t in manifest["templates"]:
        lines += ["", f"## {t['file']}", "", t["description"], ""]
        for p in t["pages"]:
            lines.append(f"- Page {p['index']} \"{p['name']}\" {p['width_in']} x {p['height_in']} in. Layers: " + "; ".join(
                f"`{x['name']}`{' (master)' if x['master'] else ''} vis={int(x['visible'])} prn={int(x['printable'])} "
                f"edit={int(x['editable'])} n={x['top_level_shapes']}"
                for x in p["layers"] if not x.get("special")))
        if t.get("expected_plates"):
            lines.append(f"- Expected plates: {', '.join(t['expected_plates'])}")
        if t.get("expected_proof_plates"):
            ep = t["expected_proof_plates"]
            lines.append(f"- Proof plates on page {ep['page']} ({ep['source']}):")
            lines += [f"  - {k}: {v}" for k, v in ep["plates"].items()]
            lines.append(f"  - Not in proof: {ep['not_in_proof']}")
        for n in t.get("notes", []):
            lines.append(f"- Note: {n}")
        if t["file"] == "big_flat.cdr":
            lines += ["", "| Seed name | Fill | Outline type | OP fill | OP outline | Count |", "| --- | --- | --- | --- | --- | --- |"]
            for row in t.get("inventory", []):
                fc = row["fill"].get("color") if row["fill"] else None
                lines.append(f"| {row['name']} | {color_text(fc) if fc else 'none'} | {row['outline_type']} | "
                             f"{row['overprint_fill']} | {row['overprint_outline']} | {row['count']} |")
        lines += ["", "| Name | ID | Pg | Layer | Type | Intended |", "| --- | --- | --- | --- | --- | --- |"]
        for s in t["shapes"]:
            it = dict(s["intended"])
            parts = []
            for key in ("fill", "outline"):
                if key in it:
                    parts.append(f"{key}: {color_text(it.pop(key))}")
            parts += [f"{k}: {v}" for k, v in it.items() if k != "runs"]
            if "runs" in s["intended"]:
                parts.append("runs: " + ", ".join(f"{r['text']!r}={color_text(r['fill'])}" for r in s["intended"]["runs"]))
            lines.append(f"| `{s['name']}` | {s.get('shape_id')} | {s.get('page')} | {s.get('layer')} | {s.get('type')} | "
                         f"{'; '.join(parts)} |")
    if manifest.get("samples"):
        lines += ["", "# Real sample copies (`C:\\CorelMcpLab\\samples`)", "",
                  "Byte-for-byte copies of production files (originals on Z:, never opened). Profiled read-only from "
                  "copies in a run folder with `scripts/validation/profile_cdr.py`. Copy before opening.", ""]
        for s in manifest["samples"]:
            sm = s.get("summary", {})
            lines += [f"## {s['file']}", "", f"- SHA-256 `{s.get('sha256')}`, {s.get('bytes')} bytes; open "
                      f"{s.get('open_seconds')} s, full profile {s.get('profile_seconds')} s",
                      f"- {len(s.get('pages', []))} pages; {sm.get('recursive_objects')} objects recursive; max group "
                      f"depth {sm.get('max_group_depth')}; text {sm.get('text')}; PowerClips {sm.get('powerclips')} "
                      f"({sm.get('powerclip_contents')} content objects); effects {sm.get('effects')}",
                      f"- Shape types: {sm.get('shape_types')}", f"- Fill types: {sm.get('fill_types')}"]
            spots = sm.get("spot_colors", {})
            lines.append("- Spots: " + "; ".join(f"{n} " + ", ".join(f"@{t}: " + "/".join(f"{r} {c}" for r, c in roles.items())
                                                                      for t, roles in tints.items())
                                                 for n, tints in spots.items()))
            lines.append(f"- CMYK: {[(c['cmyk'], c['role'], c['count']) for c in sm.get('cmyk_colors', [])][:12]}")
            lines.append(f"- RGB: {[(c['rgb'], c['role'], c['count']) for c in sm.get('rgb_colors', [])][:12]}")
            lines.append(f"- Registration: {sm.get('registration')}; unknown colors: {sm.get('unknown_colors')}; "
                         f"overprint: {sm.get('overprint')}")
            for p in s.get("pages", []):
                lines.append(f"- Page {p['index']} \"{p['name']}\" ({p['top_level']} top-level / {p['recursive']} recursive): "
                             + "; ".join(f"`{x['name']}`{' (master)' if x['master'] else ''} vis={int(x['visible'])} "
                                         f"prn={int(x['printable'])} edit={int(x['editable'])} n={x['top_level']}"
                                         + (f"/{x['recursive']} d{x['max_group_depth']}" if 'recursive' in x else "")
                                         for x in p["layers"] if not x.get("special")))
            lines.append("")
    with open(ntpath.join(out_dir, "MANIFEST.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--profiles")
    ap.add_argument("--only")
    args = ap.parse_args()
    out_dir = os.path.abspath(args.out_dir)
    if out_dir.lower().startswith("z:"):
        sys.exit("refusing to write to Z:")
    os.makedirs(out_dir, exist_ok=True)
    mpath = ntpath.join(out_dir, "MANIFEST.json")
    previous = {}
    if args.only and os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            previous = {t["file"]: t for t in json.load(fh)["templates"]}
    wanted = set(args.only.split(",")) if args.only else None
    templates = []
    for name, fn, desc in TEMPLATES:
        if wanted and name not in wanted:
            if name + ".cdr" in previous:
                templates.append(previous[name + ".cdr"])
            continue
        try:
            entry = build_one(out_dir, name, fn, desc)
            if name == "big_flat":
                entry["inventory"] = big_inventory(out_dir)
                entry["object_count"] = sum(r["count"] for r in entry["inventory"])
            if name == "screen_job":
                ub = [s for s in entry["shapes"] if s["name"].startswith("ub_")]
                white = ub[0]["intended"]["fill"]["name"] if ub else None
                entry["expected_plates"] = [white, "PANTONE 186 C", "PANTONE 300 C", "PANTONE 116 C"]
                entry["expected_proof_plates"] = {
                    "page": 1,
                    "source": "export_page_proof_pdf + render_separation_proof.py (Ghostscript tiffsep, 72 dpi), "
                              "observed 2026-11-17",
                    "plates": {
                        white: "choked underbase circle, band and two blocks (not knocked out by the inks), "
                               "'WHITE UNDERBASE' title, 3 registration marks",
                        "PANTONE 186 C": "art_circle, its title, registration marks",
                        "PANTONE 300 C": "art_band, its title, registration marks",
                        "PANTONE 116 C": "art_block_left/right, its title, registration marks",
                        "Cyan": "registration marks only", "Magenta": "registration marks only",
                        "Yellow": "registration marks only", "Black": "registration marks only",
                    },
                    "not_in_proof": "Halftone Scale layer (hidden but printable): Publish to PDF omits hidden layers",
                }
            templates.append(entry)
            print(f"built {name}: {len(entry['shapes'])} named shapes, {entry['build_seconds']} s")
        except Exception:
            print(f"FAILED {name}:\n{traceback.format_exc()}")
            templates.append({"file": name + ".cdr", "description": desc, "error": traceback.format_exc(),
                              "pages": [], "shapes": []})
    samples = []
    if args.profiles:
        with open(args.profiles, encoding="utf-8") as fh:
            samples = json.load(fh)
    version = conn.safe_call(lambda: conn.app.Version).get("result")
    manifest = {"built": datetime.datetime.now().isoformat(timespec="seconds"), "corel_version": version,
                "unit": "inch", "templates": templates, "samples": samples}
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
    write_md(out_dir, manifest)
    print("wrote", mpath)


if __name__ == "__main__":
    main()
