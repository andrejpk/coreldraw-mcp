"""E13 baseline: E01 T6a (combined underbase) with the pre-E13 tools on a screen_job copy, MCP only.

Usage: python t6a_before.py <copy.cdr> <log.jsonl>
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_lib import Session

D, LOG = sys.argv[1], sys.argv[2]


def j(x):
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


with Session(LOG) as s:
    s.task = "T6a before"; t0 = time.perf_counter()
    print("open", j(s.call("open_document", {"path": D})))
    inv = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["name", "type", "fill"]})
    shapes = inv["data"]["shapes"]
    art = [x["shape_id"] for x in shapes if x["layer"]["name"].startswith("PANTONE ")]
    old_ub = [x["shape_id"] for x in shapes if x["layer"]["name"] == "White Underbase" and x["type"] != 6]
    print("art", art, "old ub", old_ub)
    w = s.call("weld_copies_by_id", {"document_path": D, "page_index": 1, "shape_ids": art}); print("weld", j(w))
    wid = w["data"]["result_shape_id"]
    c = s.call("apply_contour", {"document_path": D, "page_index": 1, "shape_id": wid, "direction": "inside",
                                 "offset": 0.75, "unit": "pt", "steps": 1, "fill_from_shape_id": old_ub[0]})
    print("contour", j(c))
    b = s.call("break_contour_apart", {"document_path": D, "page_index": 1, "shape_id": wid}); print("break", j(b))
    leaves = b["data"]["result_leaf_shape_ids"]
    print("delete welded", j(s.call("delete_shape_by_id", {"document_path": D, "page_index": 1, "shape_id": wid})))
    print("op", j(s.call("set_overprint", {"document_path": D, "page_index": 1, "shape_ids": leaves, "fill": True})))
    for rid in b["data"]["result_shape_ids"]:
        print("assign", j(s.call("assign_to_layer", {"document_path": D, "shape_id": rid, "layer_name": "White Underbase"})))
    for u in old_ub:
        print("del old", u, j(s.call("delete_shape_by_id", {"document_path": D, "page_index": 1, "shape_id": u})))
    v = s.call("inspect_shapes", {"document_path": D, "page_index": 1, "layer_name": "White Underbase"})
    print("verify", j([(x["shape_id"], x.get("fill"), x.get("overprint_fill")) for x in v["data"]["shapes"]]))
    print("seconds", round(time.perf_counter() - t0, 2))
