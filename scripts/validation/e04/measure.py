r"""E04 measurements through MCP (server on 8765): every preset on every page of the sample copies and artifacts.cdr,
some filter queries on Good News p1, paging from the result cache, and the native FindShapes comparison note.

    python measure.py <run dir> [docs...]     -> <run dir>\measure.json
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

RUN = Path(sys.argv[1])
DOCS = sys.argv[2:] or ["artifacts.cdr", "kings.cdr", "knights.cdr", "jump.cdr", "goodnews.cdr"]
PRESETS = ["zero_size", "invisible", "stacked_duplicates", "off_page", "hairline", "empty_text", "tiny",
           "hidden_printable_content", "registration_on_art"]
out = {"presets": [], "queries": []}
for name in DOCS:
    path = str(RUN / "work" / name)
    opened = call("open_document", path=path, activate=False)
    assert opened["success"], opened
    pages = len(opened["data"]["pages"]) if "pages" in opened["data"] else opened["data"].get("page_count", 1)
    for page in range(1, pages + 1):
        for preset in PRESETS:
            r = call("find_shapes", document_path=path, pages=[page], preset=preset, limit=20)
            row = {"doc": name, "page": page, "preset": preset, "seconds": r.get("_seconds"),
                   "success": r.get("success"), "count": r["data"]["count"] if r.get("success") else None,
                   "counts": r["data"]["counts"] if r.get("success") else r.get("error"),
                   "bytes": len(json.dumps(r))}
            out["presets"].append(row)
            print(json.dumps(row), flush=True)

GN = str(RUN / "work" / "goodnews.cdr")
for label, args in (("color 354 C fill", {"filters": {"color": "PANTONE 354 C", "color_part": "fill"}}),
                    ("name glob", {"filters": {"name": "*"}}),
                    ("type text", {"filters": {"type": "text"}}),
                    ("cql outline > 0.01", {"cql": "$item.Outline.Width > 0.01"}),
                    ("size < 0.01 mm", {"filters": {"size": [None, 0.01]}, "unit": "mm"}),
                    ("all shapes", {})):
    r = call("find_shapes", document_path=GN, pages=[1], limit=100, **args)
    row = {"query": label, "seconds": r.get("_seconds"), "count": r["data"]["count"] if r.get("success") else
           r.get("error"), "bytes": len(json.dumps(r))}
    out["queries"].append(row)
    print(json.dumps(row), flush=True)
t = time.perf_counter()
first = call("find_shapes", document_path=GN, pages=[1], preset="tiny", limit=100)
offset, calls = first["data"]["next_offset"], 1
while offset is not None and calls < 5:
    r = call("find_shapes", document_path=GN, pages=[1], preset="tiny", limit=100, offset=offset)
    offset, calls = r["data"]["next_offset"], calls + 1
    out.setdefault("paging", []).append(r["_seconds"])
out["paging_first"] = first["_seconds"]
print("paging", first["_seconds"], out.get("paging"), flush=True)
(RUN / "measure.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
