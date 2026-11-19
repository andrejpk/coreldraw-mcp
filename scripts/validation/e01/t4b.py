"""Bottom registration gap on sample docs from saved inventories (no MCP)."""
import json, glob
for f in glob.glob(RUN + r"\inv\5*.json") + glob.glob(RUN + r"\inv\K*.json"):
    if f.endswith(".t2.json"): continue
    inv = json.load(open(f, encoding="utf-8"))
    per_in = {"in": 1.0, "mm": 25.4}[next(iter(inv["pages"].values())).get("unit") or "mm"]
    for p, page in inv["pages"].items():
        shapes = page["shapes"]
        marks = [s for s in shapes if s["layer"]["name"] == "Alignment Marks" and s["type"] != 7]
        art = [s["bounds"] for s in shapes if s["layer"]["name"] == "Layer 1" and s["layer"]["printable"] and s["type"] != 7]
        if not marks or not art: print(f[-25:], p, "no marks/art", len(marks), len(art)); continue
        ab = min(b["bottom"] for b in art)
        low = min(marks, key=lambda s: s["bounds"]["bottom"])
        gap = ab - (low["bounds"]["bottom"] + low["bounds"]["height"])
        print(f[-25:], "page", p, "bottom mark", low["shape_id"], "gap below art %.3f in" % (gap / per_in))
