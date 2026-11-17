r"""E13b: the new tools on the Kings and Knights sample copies through MCP (each edit undone and checked).

Usage (from server/, server running): python samples.py <out.json>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

W = "C:\\CorelMcpLab\\runs\\e13b\\work\\"
CASES = [("kings.cdr", 1, "Layer 1"), ("knights.cdr", 1, "Layer 1"), ("knights.cdr", 2, "Layer 1"),
         ("knights.cdr", 3, "Layer 1")]
BLACK = ["CMYK 0/0/0/100", "PANTONE Black C", "RVW-BK01A"]
out = []


def snapshot(path, page):
    r = call("inspect_shapes", document_path=path, page_index=page,
             fields=["name", "type", "fill", "outline", "bounds", "overprint_fill", "z_order"])
    return sorted(json.dumps([s["shape_id"], s["layer"]["name"], s.get("type"), s.get("fill"), s.get("outline"),
                              s.get("bounds"), s.get("overprint_fill"), s.get("z_order")], default=str)
                  for s in r["data"]["shapes"])


def record(label, r, extra=None):
    d = r.get("data") or {}
    keep = {k: d.get(k) for k in ("result_id", "layer", "area", "subpaths", "holes", "participant_count",
                                  "skipped_count", "offset", "verification_errors", "warnings", "exclude_unmatched",
                                  "result_ids", "new_ids", "refused")}
    item = {"case": label, "success": r.get("success"), "error": r.get("error"), "seconds": r.get("_seconds"),
            **{k: v for k, v in keep.items() if v is not None}, **(extra or {})}
    if isinstance(item.get("refused"), list):
        item["refused"] = item["refused"][:5]
    out.append(item)
    print(json.dumps(item, default=str)[:900])
    return d


for name, page, layer in CASES:
    D = W + name
    call("open_document", path=D)
    shapes = call("inspect_shapes", document_path=D, page_index=page, layer_name=layer, fields=["type"])["data"]["shapes"]
    top = [s["shape_id"] for s in shapes if not s.get("parent_id")]
    before = snapshot(D, page)
    tag = f"{name} p{page}"
    ids = list(top)
    for attempt in range(3):
        r = call("offset_silhouette", document_path=D, page_index=page, shape_ids=ids, offset=0.75, unit="pt",
                 exclude_colors=BLACK, color="PANTONE Trans. White", overprint_fill=True)
        refused = {x["id"] for x in (r.get("data") or {}).get("refused", [])}
        if r.get("success") or not refused or refused >= set(ids):
            break
        record(f"{tag} offset_silhouette refused (retry without them)", r)
        ids = [i for i in ids if i not in refused]
    d = record(f"{tag} offset_silhouette inside 0.75 pt, {len(ids)} inputs", r)
    if r.get("success"):
        call("undo", document_path=D)
        out[-1]["undo_restores"] = snapshot(D, page) == before
        print("  undo restores:", out[-1]["undo_restores"])
        r = call("offset_silhouette", document_path=D, page_index=page, shape_ids=ids, offset=2, unit="pt",
                 direction="outside", color="PANTONE Trans. White")
        record(f"{tag} offset_silhouette outside 2 pt (no exclusion)", r)
        if r.get("success"):
            call("undo", document_path=D)
    curves = [s["shape_id"] for s in shapes if not s.get("parent_id") and s["type"] == 3][:2]
    if len(curves) == 2:
        r = call("combine_shapes", document_path=D, page_index=page, shape_ids=curves, operation="weld",
                 preserve_order=False, keep_sources=True)
        record(f"{tag} combine_shapes weld copies {curves}", r)
        if r.get("success"):
            call("undo", document_path=D)
        r = call("duplicate_shapes", document_path=D, page_index=page, shape_ids=top[:5], dx=0.1, unit="in")
        record(f"{tag} duplicate_shapes x{len(top[:5])}", r)
        if r.get("success"):
            call("undo", document_path=D)
    texts = [s["shape_id"] for s in shapes if s["type"] == 6]
    if texts:
        r = call("convert_to_curves", document_path=D, page_index=page, shape_ids=texts)
        record(f"{tag} convert_to_curves {texts}", r)
        if r.get("success"):
            call("undo", document_path=D)
    out.append({"case": f"{tag} page unchanged after all undos", "success": snapshot(D, page) == before})
    print(out[-1])

for name in {c[0] for c in CASES}:
    call("close_document", document_path=W + name, discard_changes=True)
Path(sys.argv[1]).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
