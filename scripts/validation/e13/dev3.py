"""E13 in-process timing: offset_silhouette on Good News page 2 groups (a copy under runs\e13\samples)."""
import json, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, close_path, fresh_copy
from tools import silhouette

P = fresh_copy(r"C:\CorelMcpLab\runs\e13\samples\goodnews.cdr", "dev3_gn.cdr")
try:
    run(lambda: open_mine(P) and None)
    for ids in (["35589", "35697"], ["33721", "35589", "35697"]):
        t = time.perf_counter()
        r = silhouette.offset_silhouette(P, 2, ids, 0.75, color="PANTONE Trans. White", overprint_fill=True,
                                         exclude_colors=["PANTONE Black C", "CMYK 0/0/0/100"])
        d = r.data or {}
        print(ids, round(time.perf_counter() - t, 2), r.success, r.error,
              json.dumps({k: d.get(k) for k in ("result_id", "participant_count", "skipped_count", "area", "subpaths",
                                                "offset", "warnings", "verification_errors")}, default=str)[:1200])
        print("   skipped reasons", {s["reason"] for s in d.get("skipped", [])}, d.get("skipped", [])[:3])
finally:
    close_path(P)
