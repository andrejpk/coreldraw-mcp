"""E17a benchmark through MCP. Usage: bench.py <label> [wedge]  (run with the server on 8765)."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from audit_lib import Session
LABEL = sys.argv[1]
WEDGE = "wedge" in sys.argv[2:]
SKIP_GN = "nogn" in sys.argv[2:]
OUT = rf"C:\CorelMcpLab\runs\e17a\bench\{LABEL}"
os.makedirs(OUT, exist_ok=True)
S = r"C:\CorelMcpLab\runs\e17a\s"
GN = S + r"\53210 - ME-MI Good News Club - SCREENS.cdr"
DOCS = {"structure": S + r"\structure.cdr", "effects": S + r"\effects.cdr", "big_flat": S + r"\big_flat.cdr", "gn": GN}
summary = {}


def save(name, obj):
    with open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, default=str)


def inventory(s, key, page, limit=250):
    shapes, off, calls, t0, nbytes = [], 0, 0, time.perf_counter(), 0
    while off is not None:
        args = {"document_path": DOCS[key], "page_index": page, "offset": off}
        if limit:
            args["limit"] = limit
        r = s.call("inspect_shapes", args)
        calls += 1
        nbytes += len(json.dumps(r, default=str))
        if not r.get("success"):
            return {"error": r}, None
        d = r["data"]
        shapes += d["shapes"]
        off = d["next_offset"] if limit or d.get("next_offset") is not None else None
    secs = round(time.perf_counter() - t0, 2)
    return {"calls": calls, "seconds": secs, "shapes": len(shapes), "bytes": nbytes}, shapes


with Session(os.path.join(OUT, "calls.jsonl")) as s:
    s.task = "open"
    for k, p in DOCS.items():
        r = s.call("open_document", {"path": p, "activate": False})
        assert r.get("success"), r
    for key, page in [("structure", 1), ("effects", 1), ("big_flat", 1)] + ([] if SKIP_GN else [("gn", 2), ("gn", 1)]):
        s.task = f"inventory {key} p{page}"
        stat, shapes = inventory(s, key, page)
        summary[f"{key}_p{page}_paged250"] = stat
        if shapes is not None:
            save(f"{key}_p{page}", shapes)
        print(key, page, stat, flush=True)
    s.task = "big_flat one call"
    stat, shapes = inventory(s, "big_flat", 1, limit=None)
    summary["big_flat_p1_onecall"] = stat
    if shapes is not None:
        save("big_flat_p1_onecall", shapes)
    print("big_flat one call", stat, flush=True)
    if not SKIP_GN:
        texts = []
        for page in (1, 2):
            try:
                inv = json.load(open(os.path.join(OUT, f"gn_p{page}.json"), encoding="utf-8"))
            except FileNotFoundError:
                continue
            texts += [(page, r["shape_id"]) for r in inv if r.get("type") == 6]
        s.task = "text runs"
        runs, times = {}, []
        for page, sid in texts[:6]:
            t = time.perf_counter()
            r = s.call("inspect_text_fill_runs", {"document_path": GN, "page_index": page, "shape_id": sid})
            times.append(round(time.perf_counter() - t, 2))
            runs[sid] = r.get("data") if r.get("success") else r
        summary["gn_text_runs_seconds"] = times
        save("gn_text_runs", runs)
        print("text runs", times, flush=True)
        s.task = "overprint"
        times = []
        for page, sid in texts[:3]:
            t = time.perf_counter()
            r = s.call("get_shape_overprint", {"document_path": GN, "page_index": page, "shape_id": sid})
            times.append(round(time.perf_counter() - t, 2))
        summary["gn_overprint_seconds"] = times
        print("overprint", times, flush=True)
    if WEDGE:
        s.task = "wedge"
        t = time.perf_counter()
        r = s.call("list_all_text_shapes", {"page_index": 1, "document_path": GN})
        first = round(time.perf_counter() - t, 2)
        summary["list_all_text_shapes"] = {"seconds": first, "success": r.get("success"), "error": r.get("error"),
                                           "total": (r.get("data") or {}).get("total")}
        if r.get("success"):
            save("gn_text_list", r["data"])
        polls = []
        while True:
            t1 = time.perf_counter()
            st = s.call("get_coreldraw_status")
            polls.append({"t": round(time.perf_counter() - t, 1), "success": st.get("success"),
                          "error": st.get("error"), "data": st.get("data")})
            if st.get("success"):
                break
            time.sleep(2)
        summary["wedge_polls"] = polls
        summary["ready_after_seconds"] = polls[-1]["t"]
        print("wedge", summary["list_all_text_shapes"], "ready after", polls[-1]["t"], polls[:2], flush=True)
save("summary", summary)
print(json.dumps(summary, indent=1, default=str))
