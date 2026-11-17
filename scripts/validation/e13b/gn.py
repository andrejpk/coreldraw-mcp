r"""E13b: offset_silhouette scaling on the Good News copy, page 2 (MCP). Escalates the participant count while the
projected time stays well under the 60 s bridge limit; every result is undone and the page compared.

Usage (from server/, server running, only lab documents open): python gn.py <out.json> [fill]
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

D = "C:\\CorelMcpLab\\runs\\e13b\\work\\goodnews.cdr"
PAGE = 2
out = {"runs": []}
call("open_document", path=D)
shapes = call("inspect_shapes", document_path=D, page_index=PAGE, fields=["type", "fill", "outline"])["data"]["shapes"]
FILL_ONLY = len(sys.argv) > 2 and sys.argv[2] == "fill"  # pool: fill-only curves anywhere on the page
ids_before = sorted(s["shape_id"] for s in shapes)
children = collections.defaultdict(list)
for s in shapes:
    children[s.get("parent_id")].append(s)


def leaves(shape_id):
    for child in children.get(shape_id, []):
        if child["type"] == 7:
            yield from leaves(child["shape_id"])
        else:
            yield child


tops = [(s["shape_id"], s["type"], sum(1 for _ in leaves(s["shape_id"])) if s["type"] == 7 else 1)
        for s in children[None]]
out["top_level"] = sorted(tops, key=lambda t: -t[2])[:10]
print("largest top-level", out["top_level"])
big = out["top_level"][0][0]
pool = [x["shape_id"] for x in leaves(big) if x["type"] in (1, 2, 3, 4)]
if FILL_ONLY:
    pool = [x["shape_id"] for x in shapes if x["type"] in (1, 2, 3, 4) and (x.get("fill") or {}).get("type")
            and not (x.get("outline") or {}).get("type") and not x.get("effect_group_id")]
if FILL_ONLY:  # drop what the tool refuses (PowerClip contents, effect-generated objects)
    probe = call("offset_silhouette", document_path=D, page_index=PAGE, shape_ids=pool, offset=0.75, unit="pt",
                 color="PANTONE Trans. White")
    refused = {x["id"]: x["reason"] for x in (probe.get("data") or {}).get("refused", [])}
    out["refused_from_pool"] = refused
    pool = [i for i in pool if i not in refused]
    if probe.get("success"):
        call("undo", document_path=D)
print("leaf pool", len(pool))
for n in (25, 50, 100, 200, 400, 800, 1863):
    if n > len(pool):
        break
    r = call("offset_silhouette", document_path=D, page_index=PAGE, shape_ids=pool[:n], offset=0.75, unit="pt",
             color="PANTONE Trans. White")
    d = r.get("data") or {}
    run = {"inputs": n, "success": r.get("success"), "error": r.get("error"), "seconds": r.get("_seconds"),
           "stages": d.get("seconds"), "participants": d.get("participant_count"), "skipped": d.get("skipped_count"),
           "area": d.get("area"), "subpaths": d.get("subpaths"), "offset": d.get("offset"),
           "verification_errors": d.get("verification_errors"), "warnings": d.get("warnings")}
    if r.get("success"):
        call("undo", document_path=D)
        now = sorted(s["shape_id"] for s in call("inspect_shapes", document_path=D, page_index=PAGE,
                                                 fields=["type"])["data"]["shapes"])
        run["undo_restores_ids"] = now == ids_before
    out["runs"].append(run)
    print(json.dumps(run, default=str)[:700])
    if not r.get("success") or (r.get("_seconds") or 99) * 2.3 > 45:
        print("stopping escalation")
        break
call("close_document", document_path=D, discard_changes=True)
Path(sys.argv[1]).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
