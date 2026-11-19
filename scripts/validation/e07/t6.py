r"""E07: E01 T6c (move the bottom registration mark 1 in below the art, Kings copy) and E13b T6a (combined
underbase replacing the old ones, screen_job copy) redone with the E07 tools through MCP, logged with audit_lib.

Usage (server running, gate passed): python t6.py <run dir>   e.g. C:\CorelMcpLab\runs\e07
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_lib import Session  # noqa: E402

RUN = Path(sys.argv[1])
WORK = RUN / "t6"
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True)
KINGS = str(WORK / "kings.cdr")
SJ = str(WORK / "screen_job.cdr")
SJ_OUT = str(WORK / "screen_job_combined_ub.cdr")
shutil.copyfile(r"C:\CorelMcpLab\samples\51019 - Kings High School 2021 Shirt Order - SCREENS.cdr", KINGS)
shutil.copyfile(r"C:\CorelMcpLab\templates\screen_job.cdr", SJ)
INKS = ["PANTONE 186 C", "PANTONE 300 C", "PANTONE 116 C"]
summary = {}


def j(x, n=600):
    return json.dumps(x, ensure_ascii=False, default=str)[:n]


with Session(str(RUN / "calls.jsonl")) as s:
    calls = []

    def call(tool, args, counted=True):
        r = s.call(tool, args)
        calls.append((s.task, tool, counted, r.get("success")))
        if not r.get("success"):
            print("FAILED", tool, j(r))
        return r

    # T6c: discovery (not counted in E01 either: its art bottom came from an earlier inventory)
    s.task = "T6c e07 discovery"
    call("open_document", {"path": KINGS}, counted=False)
    shapes = call("inspect_shapes", {"document_path": KINGS, "page_index": 1, "fields": ["bounds"]},
                  counted=False)["data"]["shapes"]
    art_bottom = min(x["bounds"]["bottom"] for x in shapes if x["layer"]["name"] == "Layer 1")
    marks = [x for x in shapes if x["layer"]["name"] == "Alignment Marks"]
    mark = min(marks, key=lambda x: x["bounds"]["bottom"])
    unit = next(d["unit"] for d in s.call("list_documents")["data"]["documents"] if d["document_path"].lower() == KINGS.lower())
    print("art bottom", art_bottom, unit, "mark", mark["shape_id"], mark["bounds"], "locked", mark["layer"].get("locked"))

    s.task = "T6c e07"
    t0 = time.perf_counter()
    call("set_layer_properties", {"document_path": KINGS, "page_index": 1, "layer_name": "Alignment Marks",
                                  "editable": True})
    moved = call("transform_shapes", {"document_path": KINGS, "page_index": 1, "shape_ids": [mark["shape_id"]],
                                      "move_to": {"y": art_bottom - (1.0 if unit == "in" else 25.4)},
                                      "anchor": "top_center", "unit": unit})
    relock = call("set_layer_properties", {"document_path": KINGS, "page_index": 1, "layer_name": "Alignment Marks",
                                           "editable": False})
    seconds = round(time.perf_counter() - t0, 2)
    bounds = moved["data"]["shapes"][0]["bounds"]
    gap = (art_bottom - bounds[3]) / (1.0 if unit == "in" else 25.4)
    summary["t6c"] = {"calls": sum(1 for t, _, c, _ in calls if t == "T6c e07" and c), "seconds": seconds,
                      "failed": sum(1 for t, _, c, ok in calls if t == "T6c e07" and not ok),
                      "mark": mark["shape_id"], "gap_in": round(gap, 6), "mark_top": bounds[3],
                      "relocked": relock["data"]["layer"]["locked"], "dirty": relock["data"]["dirty"],
                      "bounds_before": moved["data"]["shapes"][0]["bounds_before"], "bounds": bounds}
    call("close_document", {"document_path": KINGS, "discard_changes": True}, counted=False)

    # T6a: E13b's 9 calls, with one delete_shapes instead of four delete_shape_by_id.
    s.task = "T6a e07"
    t0 = time.perf_counter()
    call("open_document", {"path": SJ})
    inv = call("inspect_shapes", {"document_path": SJ, "page_index": 1, "fields": ["name", "type", "fill"]})
    shapes = inv["data"]["shapes"]
    art = [x["shape_id"] for x in shapes if x["layer"]["name"] in INKS]
    old_ub = [x["shape_id"] for x in shapes if x["layer"]["name"] == "White Underbase"]
    r = call("offset_silhouette", {"document_path": SJ, "page_index": 1, "shape_ids": art, "offset": 0.75,
                                   "unit": "pt", "direction": "inside",
                                   "exclude_colors": ["CMYK 0/0/0/100", "PANTONE Black C"],
                                   "color": "PANTONE Trans. White", "target_layer": "White Underbase",
                                   "overprint_fill": True, "name": "underbase"})
    deleted = call("delete_shapes", {"document_path": SJ, "page_index": 1, "shape_ids": old_ub})
    contain = [{"plate": "PANTONE Trans. White", "within": INKS, "region": [0, 1.5, 13, 18], "inset": inset,
                "unit": "pt"} for inset in (0, 0.5, 1.0)]
    p = call("render_plates", {"document_path": SJ, "page_index": 1, "output_dir": str(WORK / "plates"), "dpi": 300,
                               "contain": contain})
    sv = call("save_document", {"document_path": SJ, "save_as_path": SJ_OUT})
    seconds = round(time.perf_counter() - t0, 2)
    t6a = [c for c in calls if c[0] == "T6a e07"]
    summary["t6a"] = {"calls": len(t6a), "failed": sum(1 for c in t6a if not c[3]), "seconds": seconds,
                      "old_underbases_deleted": deleted["data"]["deleted_ids"], "delete_seconds": deleted.get("_seconds"),
                      "result_layer": r["data"]["layer"], "inset_mean_pt": r["data"]["offset"]["achieved_mean"],
                      "protrusion_px_by_inset_pt": {c["inset"]: c["protrusion_px"] for c in p["data"]["containment"]},
                      "saved": sv.get("success")}
    call("close_document", {"document_path": SJ_OUT, "discard_changes": True}, counted=False)

timings = {}
for line in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines():
    rec = json.loads(line)
    if rec["task"] in ("T6c e07", "T6a e07"):
        timings.setdefault(rec["task"], []).append((rec["tool"], rec["seconds"]))
summary["per_call"] = timings
print(json.dumps(summary, indent=1, default=str))
(RUN / "t6_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
