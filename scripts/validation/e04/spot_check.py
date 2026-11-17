"""In-process spot checks of preset hits: print records (and parents) for chosen doc/page/preset."""
import json
import sys
from collections import Counter

sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK  # noqa: E402

from tools.find import find_shapes  # noqa: E402

for spec in sys.argv[1:]:
    name, page, preset = spec.split(":")
    r = find_shapes(WORK + "\\" + name, pages=[int(page)], preset=preset, limit=1000)
    assert r.success, r.error
    shapes = r.data["shapes"]
    print(name, page, preset, r.data["count"], r.data["counts"])
    print("   layers", Counter(s["layer"] for s in shapes).most_common(5))
    print("   parents", Counter(s.get("parent_id") for s in shapes).most_common(5))
    for s in shapes[:8]:
        print("   ", json.dumps(s, ensure_ascii=False)[:220])
