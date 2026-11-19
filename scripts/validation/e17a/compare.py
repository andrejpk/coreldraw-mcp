"""Compare bench outputs: compare.py <before dir> <after dir>."""
import json, os, sys

A, B = sys.argv[1], sys.argv[2]
for name in sorted(os.listdir(A)):
    if not name.endswith(".json") or name in ("summary.json",):
        continue
    pa, pb = os.path.join(A, name), os.path.join(B, name)
    if not os.path.exists(pb):
        print(f"{name}: missing in after")
        continue
    a, b = json.load(open(pa, encoding="utf-8")), json.load(open(pb, encoding="utf-8"))
    same = a == b
    detail = ""
    if isinstance(a, list) and isinstance(b, list):
        detail = f"{len(a)} vs {len(b)} records"
        if not same:
            diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
            detail += f"; first differing index {diffs[:3]}"
            if diffs:
                i = diffs[0]
                detail += f"\n  before {json.dumps(a[i])[:400]}\n  after  {json.dumps(b[i])[:400]}"
    elif isinstance(a, dict) and isinstance(b, dict):
        bad = [k for k in a if a.get(k) != b.get(k)]
        detail = f"{len(a)} keys; differing {bad[:5]}"
        for k in bad[:2]:
            detail += f"\n  {k} before {json.dumps(a[k])[:300]}\n  {k} after  {json.dumps(b.get(k))[:300]}"
    print(f"{name}: {'IDENTICAL' if same else 'DIFFERENT'} ({detail})")
