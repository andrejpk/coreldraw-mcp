"""T6b (retry): recolor PANTONE 186 C -> PANTONE Black C (existing donor) on a fresh Kings copy, fills/outlines/text; move reg mark; save."""
import sys, json, time, shutil; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import Session
D = r"C:\CorelMcpLab\runs\e01\mut\kings_mut2.cdr"; OUT = r"C:\CorelMcpLab\runs\e01\out\kings_recolored_regmoved.cdr"
SRC, DST = "PANTONE 186 C", "PANTONE Black C"
def j(x): return json.dumps(x, ensure_ascii=False, default=str)[:600]
def col(x, part): return ((x.get(part) or {}).get("color") or {}).get("name")
with Session(r"C:\CorelMcpLab\runs\e01\calls.jsonl") as s:
    s.task = "T6b recolor v2"; t0 = time.perf_counter()
    s.call("open_template", {"path": D})
    shapes = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["type", "fill", "outline", "bounds"]})["data"]["shapes"]
    pr = [x for x in shapes if x["layer"]["printable"]]
    donor = next(x["shape_id"] for x in pr if col(x, "fill") == DST and x["type"] != 6)
    fills = [x["shape_id"] for x in pr if col(x, "fill") == SRC and x["type"] != 6]
    outl = [x["shape_id"] for x in pr if col(x, "outline") == SRC]
    texts = []
    for x in pr:
        if x["type"] == 6:
            for run in (s.call("inspect_text_fill_runs", {"document_path": D, "page_index": 1, "shape_id": x["shape_id"]}).get("data") or {}).get("runs", []):
                if col(run, "fill") == SRC: texts.append((x["shape_id"], run["start"], run["end"]))
    print("donor", donor, "fills", fills, "outlines", outl, "text", texts)
    print("fills", j(s.call("copy_shape_fill", {"document_path": D, "page_index": 1, "source_shape_id": donor, "target_shape_ids": fills})))
    for sid, a, b in texts:
        r = s.call("copy_fill_to_text_range", {"document_path": D, "page_index": 1, "shape_id": sid, "start": a, "end": b, "source_shape_id": donor})
        print("text", sid, r.get("success"), r.get("error"))
    print("outlines: no tool can apply a spot outline; skipped", outl)
    after = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["type", "fill", "outline"]})["data"]["shapes"]
    print("remaining SRC refs:", [(x["shape_id"], p) for x in after if x["layer"]["printable"] for p in ("fill", "outline") if col(x, p) == SRC])
    s.task = "T6c regmark v2"
    s.call("set_page_layer_state", {"document_path": D, "page_index": 1, "layer_name": "Alignment Marks", "property_name": "editable", "value": True})
    art_bottom = min(x["bounds"]["bottom"] for x in shapes if x["layer"]["name"] == "Layer 1")
    print("move", j(s.call("set_shape_position_exact", {"document_path": D, "page_index": 1, "shape_id": "64", "anchor": "top_center", "unit": "inch", "y": art_bottom / 25.4 - 1.0})))
    s.call("set_page_layer_state", {"document_path": D, "page_index": 1, "layer_name": "Alignment Marks", "property_name": "editable", "value": False})
    v = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "layer_name": "Alignment Marks", "fields": ["bounds"]})["data"]["shapes"]
    m = next(x for x in v if x["shape_id"] == "64")["bounds"]; print("gap now in", round((art_bottom - m["bottom"] - m["height"]) / 25.4, 4))
    s.task = "T6d save"
    r = s.call("save_document", {"path": OUT}); print("saveas", r.get("success"), r.get("error"))
    r = s.call("save_document", {}, note="workaround"); print("save in place", r.get("success"))
    shutil.copyfile(D, OUT); print("seconds", round(time.perf_counter() - t0, 2))
