"""T6a: combined underbase on screen_job copy, MCP only."""
import sys, json, time; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import RUN, Session
D = RUN + r"\mut\screen_job_mut.cdr"
OUT = RUN + r"\out\screen_job_combined_ub.cdr"
def j(x): return json.dumps(x, ensure_ascii=False, default=str)[:900]
with Session(RUN + r"\calls.jsonl") as s:
    s.task = "T6a underbase"; t0 = time.perf_counter()
    print("open", j(s.call("open_document", {"path": D})))
    inv = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["name", "type", "bounds", "fill", "overprint_fill"]})
    shapes = inv["data"]["shapes"]
    art = [x["shape_id"] for x in shapes if x["layer"]["name"].startswith("PANTONE ")]
    old_ub = [x["shape_id"] for x in shapes if x["layer"]["name"] == "White Underbase"]
    white_donor = old_ub[0]
    print("art", art, "old ub", old_ub)
    w = s.call("weld_copies_by_id", {"document_path": D, "page_index": 1, "shape_ids": art}); print("weld", j(w))
    wid = w["data"]["shape_id"] if w.get("success") and "shape_id" in w["data"] else (w.get("data") or {}).get("result_shape_id")
    c = s.call("apply_contour", {"document_path": D, "page_index": 1, "shape_id": wid, "direction": "inside", "offset": 0.75,
                                  "unit": "pt", "steps": 1, "fill_from_shape_id": white_donor}); print("contour", j(c))
    b = s.call("break_contour_apart", {"document_path": D, "page_index": 1, "shape_id": wid}); print("break", j(b))
    leaves = (b.get("data") or {}).get("result_leaf_shape_ids") or []
    d = s.call("delete_shape_by_id", {"document_path": D, "page_index": 1, "shape_id": wid}); print("delete welded", j(d))
    for leaf in leaves:
        print("op", j(s.call("set_shape_overprint", {"document_path": D, "page_index": 1, "shape_id": leaf, "part": "fill", "enabled": True})))
    for rid in (b.get("data") or {}).get("result_shape_ids") or []:
        print("assign", j(s.call("assign_to_layer", {"document_path": D, "shape_id": rid, "layer_name": "White Underbase"})))
    for u in old_ub:
        print("del old", u, j(s.call("delete_shape_by_id", {"document_path": D, "page_index": 1, "shape_id": u})))
    v = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "layer_name": "White Underbase"})
    print("verify", j(v))
    print("save", j(s.call("save_document", {"document_path": D, "save_as_path": OUT})))
    print("info", j(s.call("list_documents")))
    print("seconds", round(time.perf_counter() - t0, 2))
