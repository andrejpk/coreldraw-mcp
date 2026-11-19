"""E13 in-process dev check of offset_silhouette / combine_shapes on screen_job copies (no MCP server)."""
import json, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, fresh_copy, close_path
from tools import silhouette, geometry, effects, inspection

P = fresh_copy(r"C:\CorelMcpLab\templates\screen_job.cdr", "dev_sj.cdr")
try:
    run(lambda: open_mine(P) and None)
    t = time.perf_counter()
    r = silhouette.offset_silhouette(P, 1, ["4", "6", "8", "10"], 0.75, "pt", "inside",
                                     color="PANTONE Trans. White", target_layer="White Underbase",
                                     overprint_fill=True, name="combined underbase")
    print(round(time.perf_counter() - t, 2), r.success, r.error)
    print(json.dumps(r.data, default=str)[:3000])
finally:
    close_path(P)
