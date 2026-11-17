"""T6b: recolor PANTONE 186 C -> PANTONE 485 C on Kings copy (fills, outlines, text); move bottom reg mark; save as."""
import sys, json, time; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import RUN, Session
D = RUN + r"\mut\kings_mut.cdr"
OUT = RUN + r"\out\kings_recolored.cdr"
SRC, DST = "PANTONE 186 C", "485 C"
def j(x): return json.dumps(x, ensure_ascii=False, default=str)[:700]
with Session(RUN + r"\calls.jsonl") as s:
    s.task = "T6b recolor"; t0 = time.perf_counter()
    print("open", j(s.call("open_document", {"path": D})))
    shapes = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["type", "fill", "outline", "bounds"]})["data"]["shapes"]
    pr = [x for x in shapes if x["layer"]["printable"]]
    fills = [x["shape_id"] for x in pr if (x.get("fill") or {}).get("color", {}).get("name") == SRC and x["type"] != 6]
    outl = [x["shape_id"] for x in pr if (x.get("outline") or {}).get("color", {}).get("name") == SRC]
    texts = []
    for x in pr:
        if x["type"] == 6:
            r = s.call("inspect_text_fill_runs", {"document_path": D, "page_index": 1, "shape_id": x["shape_id"]})
            for run in (r.get("data") or {}).get("runs", []):
                if (run.get("fill") or {}).get("color", {}).get("name") == SRC:
                    texts.append((x["shape_id"], run["start"], run["end"]))
    print("targets fills", fills, "outlines", outl, "text runs", texts)
    # 1. no 'set spot fill on exact id' tool: try legacy set_fill_pantone on the first fill target
    r = s.call("set_fill_pantone", {"document_path": D, "shape_id": fills[0], "pantone_code": DST}); print("set_fill_pantone", j(r))
    chk = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["fill"]})["data"]["shapes"]
    donor = next(x for x in chk if x["shape_id"] == fills[0])
    print("donor fill after set_fill_pantone", j(donor.get("fill")))
    r = s.call("set_fill_pantone", {"document_path": D, "shape_id": fills[0], "pantone_code": "PANTONE 485 C"}); print("set_fill_pantone full name", j(r))
    chk = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["fill"]})["data"]["shapes"]
    donor = next(x for x in chk if x["shape_id"] == fills[0]); print("donor fill now", j(donor.get("fill")))
    if fills[1:]:
        print("copy_shape_fill", j(s.call("copy_shape_fill", {"document_path": D, "page_index": 1, "source_shape_id": fills[0], "target_shape_ids": fills[1:]})))
    for sid, a, b in texts:
        print("text", sid, j(s.call("copy_fill_to_text_range", {"document_path": D, "page_index": 1, "shape_id": sid, "start": a, "end": b, "source_shape_id": fills[0]})))
    # outlines: only set_outline (CMYK/RGB) exists
    for sid in outl:
        print("outline (no spot option)", sid, j(s.call("set_outline", {"document_path": D, "shape_id": sid, "width": 0.1764, "color": "100,0,0,0"})))
    after = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["type", "fill", "outline"]})["data"]["shapes"]
    left = [(x["shape_id"], p) for x in after if x["layer"]["printable"] for p in ("fill", "outline") if (x.get(p) or {}).get("color", {}).get("name") == SRC]
    print("remaining 186 C refs (shape-level):", left)
    s.task = "T6c regmark"
    L = s.call("get_page_layers", {"document_path": D, "page_index": 1})
    print("layers", [(l["name"], l["editable"]) for l in L["data"]["layers"] if l["name"] == "Alignment Marks"])
    print("unlock", j(s.call("set_page_layer_state", {"document_path": D, "page_index": 1, "layer_name": "Alignment Marks", "property_name": "editable", "value": True})))
    art_bottom = min(x["bounds"]["bottom"] for x in shapes if x["layer"]["name"] == "Layer 1")
    unit = next(d["unit"] for d in s.call("list_documents")["data"]["documents"] if d["document_path"].lower() == D.lower())
    print("art bottom", art_bottom, unit)  # bounds are in the document unit
    mv = s.call("set_shape_position_exact", {"document_path": D, "page_index": 1, "shape_id": "64", "anchor": "top_center",
                                             "unit": unit, "y": art_bottom - {"in": 1.0, "mm": 25.4}[unit]})
    print("move", j(mv))
    print("relock", j(s.call("set_page_layer_state", {"document_path": D, "page_index": 1, "layer_name": "Alignment Marks", "property_name": "editable", "value": False})))
    print("save", j(s.call("save_document", {"document_path": D, "save_as_path": OUT})))
    print("info", j(s.call("list_documents")))
    print("seconds", round(time.perf_counter() - t0, 2))
