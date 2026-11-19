r"""E07: list_layers and delete_shapes / transform_shapes timings on a Good News copy (MCP, then undo)."""
import json, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402
assert subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "e13b" / "gate.py")]).returncode == 0
W = Path(r"C:\CorelMcpLab\runs\e07\gn"); shutil.rmtree(W, ignore_errors=True); W.mkdir(parents=True)
D = str(W / "gn.cdr")
shutil.copyfile(r"C:\CorelMcpLab\samples\53210 - ME-MI Good News Club - SCREENS.cdr", D)
out = {}
r = call("open_document", path=D); out["open"] = r["_seconds"]
r = call("list_layers", document_path=D); out["list_layers_all_pages"] = (r["_seconds"], r["success"], len(r["data"]["pages"]) if r["success"] else r.get("error"))
p1 = r["data"]["pages"][0]["layers"] if r["success"] else []
out["p1_layers"] = [(x["name"], x["objects"], x["objects_total"], x["printable"], x["locked"]) for x in p1 if not x.get("special")]
r = call("list_layers", document_path=D); out["list_layers_cached"] = r["_seconds"]
f = call("find_shapes", document_path=D, pages=[1], preset="tiny", limit=300)
ids = [s["id"] for s in f["data"]["shapes"]] if f["success"] else []
out["tiny_found"] = (len(ids), f["_seconds"], f.get("error"))
r = call("delete_shapes", document_path=D, page_index=1, shape_ids=ids)
out["delete_shapes"] = (len(ids), r["_seconds"], r["success"], r.get("error"), (r.get("data") or {}).get("objects_left_on_page"))
u = call("undo", document_path=D); out["undo_delete"] = u["_seconds"]
editable = {x[0] for x in out["p1_layers"] if not x[4]}; tops = [s["id"] for s in call("find_shapes", document_path=D, pages=[1], filters={"depth": [0, 0]}, limit=200)["data"]["shapes"] if s["layer"] in editable]
out["editable_tops"] = len(tops)
r = call("transform_shapes", document_path=D, page_index=1, shape_ids=tops[:20], move_by={"dx": 0.1}, unit="in")
out["transform_20_top"] = (r["_seconds"], r["success"], r.get("error"))
call("undo", document_path=D)
print(json.dumps(out, indent=1, default=str))
(W / "gn_summary.json").write_text(json.dumps(out, indent=1, default=str))
call("close_document", document_path=D, discard_changes=True)
