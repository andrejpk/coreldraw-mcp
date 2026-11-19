"""In-process profile of the new read path: prof_after.py (run from server/, no MCP server running)."""
import json, sys, time
sys.path.insert(0, ".")
from core import structure
from core.connection import get_connection
from core.targets import find_documents, resolve_document
from tools import inspection

conn = get_connection()
S = r"C:\CorelMcpLab\runs\e17a\s"
GN = S + r"\53210 - ME-MI Good News Club - SCREENS.cdr"
BIG = S + r"\big_flat.cdr"


def ensure(path):
    def run():
        if not find_documents(conn.app, path):
            conn.app.OpenDocument(path)
    conn.safe_call(run)


def profile(path, page_index):
    def run():
        out = {}
        doc, _ = resolve_document(conn.app, path)
        page = doc.Pages(page_index)
        structure.invalidate()
        t = time.perf_counter()
        views = structure.page_layers(doc, page)
        out["walk_all_layers_s"] = round(time.perf_counter() - t, 3)
        out['entries'] = [(v.name, len(v.structure.entries)) for v in views if v.structure.entries]
        t = time.perf_counter()
        structure.page_layers(doc, page)
        out["cached_page_layers_s"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter(); structure.page_layers(doc, page); out['cached2_s'] = round(time.perf_counter() - t, 3)
        biggest = max(views, key=lambda v: len(v.structure.entries))
        entries = biggest.structure.entries
        info = biggest.details()
        for start in (0, len(entries) // 2, max(0, len(entries) - 250)):
            t = time.perf_counter()
            inspection._read_records(biggest, info, entries[start:start + 250], None)
            out[f"records250@{start}_s"] = round(time.perf_counter() - t, 3)
        return out
    r = conn.read_call(run)
    return r["result"] if r["success"] else r


for path in (GN, BIG):
    ensure(path)
for path, page in ((GN, 1), (GN, 2), (BIG, 1)):
    print(path.rsplit("\\", 1)[1], page, json.dumps(profile(path, page), indent=1))
    sys.stdout.flush()
for path, page in ((GN, 1), (BIG, 1)):
    structure.invalidate()
    t = time.perf_counter()
    r = inspection.inspect_shapes(path, page)
    print("full one-call inventory", path.rsplit("\\", 1)[1], page, round(time.perf_counter() - t, 2), "s",
          len(r.data["shapes"]) if r.success else r.error, r.data.get("next_offset") if r.success else None)
print(structure.stats())
