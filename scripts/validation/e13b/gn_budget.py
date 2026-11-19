r"""E13b: offset_silhouette over more outlined Good News specks than fit in the time budget: the call must stop,
undo itself and leave page 2 unchanged, all inside the 60 s bridge limit (MCP). Usage: gn_budget.py <n>"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

D = "C:\\CorelMcpLab\\runs\\e13b\\work\\goodnews.cdr"
call("open_document", path=D)


def snapshot():
    r = call("inspect_shapes", document_path=D, page_index=2, fields=["type", "fill", "outline", "bounds", "z_order"])
    return r["data"]["shapes"]


before = snapshot()
pool = [x["shape_id"] for x in before if x.get("parent_id") and x["type"] == 3 and (x.get("outline") or {}).get("type")
        and (x.get("fill") or {}).get("type") and abs((x["outline"].get("width") or 0) - 0.006944881889763779) < 1e-9]
ids = pool[:int(sys.argv[1])]
r = call("offset_silhouette", document_path=D, page_index=2, shape_ids=ids, offset=0.75, unit="pt",
         color="PANTONE Trans. White")
after = snapshot()
status = call("get_coreldraw_status")
print(json.dumps({"n": len(ids), "success": r.get("success"), "error": r.get("error"), "seconds": r.get("_seconds"),
                  "page_unchanged": json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True),
                  "status": (status.get("data") or {}).get("connected"),
                  "dirty": [d for d in call("list_documents")["data"]["documents"]
                            if d["document_path"] == D][0].get("dirty")}, indent=1))
