"""In-process: presets on finds.cdr vs the builder's expectations (quick loop before the MCP acceptance run)."""
import json, sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import RUN, run, open_mine
from tools.find import find_shapes
F = json.load(open(RUN + r"\fx\finds.json"))
path = F["path"]
run(lambda: open_mine(path).Pages.Count)
name = {v: k for k, v in F["ids"].items()}
flag = set()
for preset, exp in F["expected"].items():
    r = find_shapes(path, preset=preset, limit=1000)
    got = {s.get("name", s["id"]): s["reason"] for s in r.data["shapes"]} if r.success else r.error
    flag |= set(got)
    ok = got == exp
    print("OK  " if ok else "BAD ", preset, "" if ok else {"missing": {k: v for k, v in exp.items() if got.get(k) != v}, "extra": {k: v for k, v in got.items() if k not in exp}})
print("clean flagged:", flag - {k for e in F["expected"].values() for k in e})
