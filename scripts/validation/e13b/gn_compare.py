r"""E13b: the batched union (offset_silhouette, offset 0) against sequential Shape.Weld (combine_shapes weld on copies)
for the same Good News p2 fill-only curves; both through MCP, both undone. Usage: gn_compare.py <n>"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

D = "C:\\CorelMcpLab\\runs\\e13b\\work\\goodnews.cdr"
pool = json.loads(Path(r"C:\CorelMcpLab\runs\e13b\gn_fill.json").read_text())
call("open_document", path=D)
shapes = call("inspect_shapes", document_path=D, page_index=2, fields=["type", "fill", "outline"])["data"]["shapes"]
refused = set(pool.get("refused_from_pool", {}))
ids = [x["shape_id"] for x in shapes if x["type"] in (1, 2, 3, 4) and (x.get("fill") or {}).get("type")
       and not (x.get("outline") or {}).get("type") and not x.get("effect_group_id") and x["shape_id"] not in refused]
ids = ids[:int(sys.argv[1])]
a = call("offset_silhouette", document_path=D, page_index=2, shape_ids=ids, offset=0, color="PANTONE Trans. White")
call("undo", document_path=D)
b = call("combine_shapes", document_path=D, page_index=2, shape_ids=ids, operation="weld", preserve_order=False,
         keep_sources=True)
call("undo", document_path=D)
sa = a["data"]
sb = b["data"]["results"][0] if b.get("success") else {}
print(json.dumps({"n": len(ids), "silhouette": {k: sa.get(k) for k in ("area", "subpaths", "holes", "bounds")},
                  "silhouette_seconds": a["_seconds"],
                  "sequential_weld": {k: sb.get(k) for k in ("area", "subpaths", "holes", "bounds")},
                  "weld_seconds": b.get("_seconds"), "weld_error": b.get("error")}, indent=1))
