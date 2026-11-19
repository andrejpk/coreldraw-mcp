r"""E13b: E01 T6a (combined underbase, replace the old ones, verify, save-as) through MCP, then the plate check
(render_plates contain at several insets, and a seeded protrusion). Logged with audit_lib.

Usage (from server/, server running): python t6a.py <run dir>   e.g. C:\CorelMcpLab\runs\e13b
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_lib import Session  # noqa: E402

RUN = Path(sys.argv[1])
WORK = RUN / "t6a"
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True)
D = str(WORK / "screen_job.cdr")
OUT = str(WORK / "screen_job_combined_ub.cdr")
shutil.copyfile(r"C:\CorelMcpLab\templates\screen_job.cdr", D)
INKS = ["PANTONE 186 C", "PANTONE 300 C", "PANTONE 116 C"]
ART = [0, 1.5, 13, 18]  # above the film titles (in)
WHITE = "PANTONE Trans. White"


def j(x, n=700):
    return json.dumps(x, ensure_ascii=False, default=str)[:n]


def contain(inset):
    return {"plate": WHITE, "within": INKS, "region": ART, "inset": inset, "unit": "pt"}


summary = {}
with Session(str(RUN / "calls.jsonl")) as s:
    s.task = "T6a e13b"
    t0 = time.perf_counter()
    calls = 0

    def call(tool, args):
        global calls
        calls += 1
        r = s.call(tool, args)
        if not r.get("success"):
            print("FAILED", tool, j(r))
        return r

    call("open_document", {"path": D})
    inv = call("inspect_shapes", {"document_path": D, "page_index": 1, "fields": ["name", "type", "fill"]})
    shapes = inv["data"]["shapes"]
    art = [x["shape_id"] for x in shapes if x["layer"]["name"] in INKS]
    old_ub = [x["shape_id"] for x in shapes if x["layer"]["name"] == "White Underbase"]
    r = call("offset_silhouette", {"document_path": D, "page_index": 1, "shape_ids": art, "offset": 0.75, "unit": "pt",
                                   "direction": "inside", "exclude_colors": ["CMYK 0/0/0/100", "PANTONE Black C"],
                                   "color": WHITE, "target_layer": "White Underbase", "overprint_fill": True,
                                   "name": "underbase"})
    print("offset_silhouette", r["_seconds"] if "_seconds" in r else "", j({k: r["data"].get(k) for k in (
        "result_id", "layer", "area", "offset", "fill", "overprint", "participant_count", "warnings")}, 1200))
    result_id = r["data"]["result_id"]
    for u in old_ub:
        call("delete_shape_by_id", {"document_path": D, "page_index": 1, "shape_id": u})
    p = call("render_plates", {"document_path": D, "page_index": 1, "output_dir": str(WORK / "plates"), "dpi": 300,
                               "contain": [contain(0), contain(0.5), contain(1.0)]})
    checks = p["data"]["containment"]
    print("render", j({"plates": p["data"].get("rendered_plates"), "differences": p["data"].get("differences"),
                       "containment": [(c.get("inset"), c.get("protrusion_px")) for c in checks]}, 1500))
    sv = call("save_document", {"document_path": D, "save_as_path": OUT})
    seconds = round(time.perf_counter() - t0, 2)
    white = next(x for x in p["data"]["plates"] if x["plate"] == WHITE)
    summary["t6a"] = {
        "calls": calls, "seconds": seconds, "old_underbase_deleted": len(old_ub),
        "result_on_layer": r["data"]["layer"], "overprint": r["data"]["overprint"],
        "inset_min_side_pt": r["data"]["offset"]["achieved_min_side"],
        "inset_mean_pt": r["data"]["offset"]["achieved_mean"],
        "white_attributed": [x["id"] for x in white.get("attributed_ids", [])],
        "protrusion_px_by_inset_pt": {c["inset"]: c["protrusion_px"] for c in checks},
        "saved": sv.get("success"), "offset_silhouette_seconds": None,
    }

    # Seeded protrusion: a copy of the underbase moved 0.1 in right sticks out of the inks.
    s.task = "T6a e13b seeded"
    dup = s.call("duplicate_shapes", {"document_path": OUT, "page_index": 1, "shape_ids": [result_id], "dx": 0.1,
                                      "unit": "in"})
    p2 = s.call("render_plates", {"document_path": OUT, "page_index": 1, "output_dir": str(WORK / "plates_seeded"),
                                  "dpi": 300, "contain": [contain(0), contain(0.5)]})
    seeded = p2["data"]["containment"]
    summary["seeded"] = {"duplicate": dup["data"]["new_ids"],
                         "protrusion": [{k: c.get(k) for k in ("inset", "protrusion_px", "protrusion_bbox")}
                                        for c in seeded]}
    s.call("undo", {"document_path": OUT})
    s.call("close_document", {"document_path": OUT, "discard_changes": True})

print(json.dumps(summary, indent=1, default=str))
(RUN / "t6a_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
