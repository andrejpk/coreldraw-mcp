"""T1: open each copy, enumerate pages/layers/flags, inventory every page via inspect_shapes (paged)."""
import sys, json, time; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import Session
W = r"C:\CorelMcpLab\runs\e01\work" + "\\"
files = sys.argv[1:]
with Session(r"C:\CorelMcpLab\runs\e01\calls.jsonl") as s:
    for f in files:
        path = W + f
        s.task = "T1 " + f[:12]
        t0 = time.perf_counter()
        o = s.call("open_template", {"path": path}); print("open", json.dumps(o, ensure_ascii=False)[:400])
        info = s.call("get_document_info"); d = info["data"]
        print("info", json.dumps(d, ensure_ascii=False)[:600])
        doc = d["document_path"]
        inv = {"doc": doc, "open": o, "info": d, "pages": {}}
        for p in range(1, d["pages"] + 1):
            L = s.call("get_page_layers", {"document_path": doc, "page_index": p})
            shapes, off, calls = [], 0, 0
            tp = time.perf_counter()
            while off is not None:
                r = s.call("inspect_shapes", {"document_path": doc, "page_index": p, "offset": off, "limit": 250})
                calls += 1
                if not r.get("success"):
                    print("FAIL", r.get("error")); break
                shapes += r["data"]["shapes"]; off = r["data"]["next_offset"]
            inv["pages"][p] = {"layers": L.get("data"), "shapes": shapes, "inspect_calls": calls,
                               "inspect_seconds": round(time.perf_counter() - tp, 2), "unit": r["data"].get("document_unit") if r.get("success") else None}
            print(f"page {p}: {len(shapes)} shapes, {calls} calls, {inv['pages'][p]['inspect_seconds']}s")
            print(" layers", json.dumps(L.get("data"), ensure_ascii=False)[:1500])
        inv["seconds"] = round(time.perf_counter() - t0, 2)
        json.dump(inv, open(r"C:\CorelMcpLab\runs\e01\inv\%s.json" % f[:12].strip(), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print("total", inv["seconds"])
