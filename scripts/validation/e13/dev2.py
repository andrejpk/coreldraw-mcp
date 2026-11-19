"""E13 in-process dev: exclusions, text/outlines/groups/layers, keep_sources=False, combine ops, failures."""
import json, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path
from core.targets import activate_document
from tools import silhouette, geometry, effects, inspection, colors

P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "dev2_sj.cdr")


def show(label, r, keys=None, n=1500):
    d = r.data or {}
    if keys:
        d = {k: d.get(k) for k in keys}
    print(f"== {label}: {r.success} {r.error}\n   " + json.dumps(d, default=str)[:n])


def setup():
    doc = open_mine(P)
    activate_document(conn.app, doc)
    page = doc.Pages(1)
    layers = {l.Name: l for l in page.Layers}
    app = conn.app
    k = layers["PANTONE 186 C"].CreateRectangle2(6.0, 9.0, 1.0, 3.0)  # black block over the circle
    k.Fill.ApplyUniformFill(app.CreateCMYKColor(0, 0, 0, 100)); k.Outline.SetNoOutline(); k.Name = "black_k"
    ln = layers["PANTONE 300 C"].CreateLineSegment(2.0, 3.0, 11.0, 3.0)  # outline-only line
    ln.Outline.Width = 0.1; ln.Outline.Color = app.CreateSpotColorByName("PANTONE® solid coated", "PANTONE 300 C", 100)
    t = layers["PANTONE 116 C"].CreateArtisticText(3.0, 16.0, "OBO", 1033, 0, "Arial", 72)
    t.Fill.ApplyUniformFill(app.CreateSpotColorByName("PANTONE® solid coated", "PANTONE 116 C", 100))
    a = layers["PANTONE 116 C"].CreateRectangle2(1.0, 1.0, 0.5, 0.5); b = layers["PANTONE 116 C"].CreateRectangle2(1.2, 1.2, 0.5, 0.5)
    rg = app.CreateShapeRange(); rg.Add(a); rg.Add(b); g = rg.Group()
    doc.Dirty = False
    return {"k": str(k.StaticID), "line": str(ln.StaticID), "text": str(t.StaticID), "group": str(g.StaticID)}


try:
    ids = run(setup)
    print(ids)
    art = ["4", "6", "8", "10"]
    r = silhouette.offset_silhouette(P, 1, art + [ids["k"]], 0.75, color="PANTONE Trans. White",
                                     target_layer="White Underbase", overprint_fill=True,
                                     exclude_colors=["CMYK 0/0/0/100", "PANTONE Black C"])
    show("black excluded", r, ["result_id", "area", "silhouette", "offset", "skipped", "participant_count"])
    r2 = silhouette.offset_silhouette(P, 1, art + [ids["k"]], 0.75, color="PANTONE Trans. White",
                                      target_layer="White Underbase")
    show("black included", r2, ["result_id", "area", "silhouette", "skipped"])
    t = time.perf_counter()
    r = silhouette.offset_silhouette(P, 1, [ids["line"], ids["text"], ids["group"], "4"], 1.0, direction="outside",
                                     color="PANTONE 186 C", target_layer="PANTONE 186 C", name="spread")
    show(f"text+outline+group+layers outside ({time.perf_counter() - t:.2f}s)", r,
         ["result_id", "layer", "area", "holes", "subpaths", "silhouette", "offset", "participants", "warnings"])
    # failures
    r = silhouette.offset_silhouette(P, 1, ["6"], 0.5, target_layer="No Such Layer")
    show("bad layer", r)
    r = silhouette.offset_silhouette(P, 1, ["6"], 50, unit="in")
    show("offset too big", r)
    r = silhouette.offset_silhouette(P, 1, ["6", "99999"], 0.5)
    show("missing id", r)
    # combine
    r = geometry.combine_shapes(P, 1, ["8", "10"], "weld", keep_sources=True, preserve_order=False,
                                target_layer="White Underbase")
    show("weld copies -> layer", r, ["result_ids", "results", "sources"])
    r = geometry.combine_shapes(P, 1, ["8", "10"], "intersect", keep_sources=True)
    show("intersect", r)
    r = geometry.combine_shapes(P, 1, ["6", "4"], "trim", keep_sources=True)
    show("trim keep", r, ["result_ids", "results"])
    r = geometry.combine_shapes(P, 1, [ids["group"]], "break_apart")
    show("break group refused", r)
    r = geometry.combine_shapes(P, 1, ["8", "10"], "combine", keep_sources=True)
    show("combine keep", r, ["result_ids", "results"])
    r = geometry.duplicate_shapes(P, 1, ["4", ids["group"]], dx=0.5, dy=-0.25, unit="in")
    show("duplicate", r)
    r = geometry.convert_to_curves(P, 1, [ids["text"], "4", "6"])
    show("convert", r)
    r = effects.apply_contour(P, 1, ["8"], "outside", 2, "pt", steps=2, fill="PANTONE 186 C")
    show("apply_contour", r)
    r2 = inspection.inspect_shapes(P, 1, layer_name="PANTONE 116 C", fields=["effects"])
    print("   inspect effects:", [s.get("effect_details") for s in r2.data["shapes"] if s.get("effect_details")])
    grp = r.data["shapes"][0]["contour"]["group_id"] if r.success else None
    r = effects.separate_effect(P, 1, [grp] if grp else ["8"])
    show("separate via group id", r)
    r = silhouette.offset_silhouette(P, 1, ["6"], 0.5, keep_sources=False, color="PANTONE Trans. White")
    show("keep_sources=False", r, ["result_id", "layer", "sources_kept", "area"])
finally:
    close_path(P)
