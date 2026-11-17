import sys, json; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import Session
from audit_manifest import manifest
inv = json.load(open(r"C:\CorelMcpLab\runs\e01\inv\53210 - ME-M.json", encoding="utf-8")); doc = inv["doc"]
t2p = r"C:\CorelMcpLab\runs\e01\inv\53210 - ME-M.t2.json"; t2 = json.load(open(t2p, encoding="utf-8"))
with Session(r"C:\CorelMcpLab\runs\e01\calls.jsonl") as s:
    s.task = "T2 53210 - ME-M retry p2"
    s.call("open_template", {"path": doc}, note="re-activate after mismatch"); shapes = inv["pages"]["2"]["shapes"]; runs = {}
    for sh in shapes:
        if sh.get("type") == 6 and sh["layer"]["printable"]:
            r = s.call("inspect_text_fill_runs", {"document_path": doc, "page_index": 2, "shape_id": sh["shape_id"]})
            if r.get("success"): runs[sh["shape_id"]] = r["data"]["runs"]
            else: print("fail", sh["shape_id"], r.get("error"))
    txt = s.call("list_all_text_shapes", {"page_index": 2, "document_path": doc}); print("list p2", txt.get("success"), txt.get("_seconds"))
    m = manifest(shapes, runs); t2["pages"]["2"]["manifest"] = m; t2["pages"]["2"]["text_runs"] = runs
    print({k: (x["fill"], x["outline"], x.get("text", 0)) for k, x in m["spots"].items()}, {k: x["count"] for k, x in m["other"].items()})
json.dump(t2, open(t2p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
