"""Print layers (top-level counts, types, inks) of a run-folder copy through MCP. Usage: layers_of.py <doc>"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

D = sys.argv[1]
assert D.lower().startswith("c:\\corelmcplab\\runs\\e13b\\"), D
info = call("open_document", path=D)
for page in info["data"]["pages"]:
    r = call("inspect_shapes", document_path=D, page_index=page["index"], fields=["type", "fill", "name"])
    top = collections.defaultdict(list)
    for s in r["data"]["shapes"]:
        if not s.get("parent_id"):
            top[(s["layer"]["name"], s["layer"].get("printable"), s["layer"].get("visible"))].append(
                (s["shape_id"], s["type"], (s.get("fill") or {}).get("color", {}).get("name")))
    print("page", page["index"], page.get("name"), "objects", len(r["data"]["shapes"]))
    for key, items in top.items():
        print("  ", key, len(items), items[:8])
