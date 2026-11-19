"""Compare legacy per-property reads (chunked under the 60 s bridge cap) with batched inspect_shapes on big_flat.cdr."""
import json, sys, time; sys.path.insert(0, ".")
from collections import Counter
from core.connection import get_connection
from tools import inspection
conn = get_connection()
FX = sys.argv[1]
PATH = FX + r"\big_flat.cdr"


def activate():
    app = conn.app
    for i in range(1, app.Documents.Count + 1):
        d = app.Documents.Item(i)
        if d.FullFileName.lower() == PATH.lower():
            d.Activate(); return "activated"
    app.OpenDocument(PATH); return "opened"


def legacy_chunk(lo, hi):
    layer = [l for l in conn.app.ActiveDocument.Pages(1).AllLayers if l.Name == "Flat"][0]
    info = inspection._layer_details(layer, "Flat")
    return [inspection._inspect_shape(layer.Shapes.Item(i), None, info) for i in range(lo, hi)]


def counts(records):
    c = Counter()
    for r in records:
        for part in ("fill", "outline"):
            col = (r.get(part) or {}).get("color")
            c[(part, (r.get(part) or {}).get("type"), json.dumps(col, sort_keys=True))] += 1
    return c


print(conn.safe_call(activate))
legacy_records, t0 = [], time.perf_counter()
n = conn.safe_call(lambda: [l for l in conn.app.ActiveDocument.Pages(1).AllLayers if l.Name == "Flat"][0].Shapes.Count)["result"]
for lo in range(1, n + 1, 100):
    r = conn.safe_call(legacy_chunk, lo, min(n + 1, lo + 100))
    if not r["success"]:
        print("legacy failed", r); sys.exit(1)
    legacy_records += r["result"]
t_legacy = time.perf_counter() - t0

t0 = time.perf_counter(); full = inspection.inspect_shapes(PATH, 1, "Flat"); t_full = time.perf_counter() - t0
assert full.success, full.error
t0 = time.perf_counter(); paged, off, calls = [], 0, 0
while off is not None:
    r = inspection.inspect_shapes(PATH, 1, "Flat", off, 250); calls += 1
    assert r.success, r.error
    paged += r.data["shapes"]; off = r.data["next_offset"]
t_paged = time.perf_counter() - t0
t0 = time.perf_counter(); narrow = inspection.inspect_shapes(PATH, 1, "Flat", fields=["fill", "outline"]); t_narrow = time.perf_counter() - t0

b = full.data["shapes"]
print(json.dumps({
    "shapes": [len(legacy_records), len(b), len(paged)],
    "legacy_direct_seconds": round(t_legacy, 1),
    "batched_full_one_call_seconds": round(t_full, 1),
    "batched_paged_250_seconds": round(t_paged, 1), "paged_calls": calls,
    "batched_fill_outline_only_seconds": round(t_narrow, 1),
    "records_identical": legacy_records == b, "paged_identical": paged == b,
    "ids_identical": [r["shape_id"] for r in legacy_records] == [r["shape_id"] for r in b],
    "unique_ids": len({r["shape_id"] for r in b}),
    "color_counts_identical": counts(legacy_records) == counts(b) == counts(narrow.data["shapes"]),
    "unreadable": sum(1 for r in b if r["unreadable"]),
}, indent=1))
for k, v in sorted(counts(b).items(), key=str):
    print(v, k)
