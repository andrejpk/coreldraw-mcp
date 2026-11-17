"""T4: underbase choke/overprint check on screen_job; bottom registration distance on all docs (from inventory + MCP)."""
import sys, json; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import Session
MM_PER_IN = 25.4
inv = json.load(open(r"C:\CorelMcpLab\runs\e01\inv\screen_job.c.json", encoding="utf-8"))
doc = inv["doc"]; sh = {s["shape_id"]: s for s in inv["pages"]["1"]["shapes"]}
byname = {s["name"]: s for s in sh.values() if s.get("name")}
with Session(r"C:\CorelMcpLab\runs\e01\calls.jsonl") as s:
    s.task = "T4 underbase"
    s.call("open_template", {"path": doc}, note="re-activate")
    so = s.call("get_simulate_overprints", {"document_path": doc}); print("simulate", so.get("data"))
    so2 = s.call("set_simulate_overprints", {"document_path": doc, "enabled": True}); print("set sim", so2.get("data"), so2.get("error"))
    # The agent cannot know which underbase belongs to which art except by geometry: pair by bbox containment
    ubs = [x for x in sh.values() if x["layer"]["name"] == "White Underbase"]
    arts = [x for x in sh.values() if x["layer"]["name"].startswith("PANTONE")]
    for u in ubs:
        ub = u["bounds"]
        best = None
        for a in arts:
            ab = a["bounds"]
            insets = {"left": ub["left"] - ab["left"], "bottom": ub["bottom"] - ab["bottom"],
                      "right": (ab["left"] + ab["width"]) - (ub["left"] + ub["width"]),
                      "top": (ab["bottom"] + ab["height"]) - (ub["bottom"] + ub["height"])}
            if all(v > -0.01 for v in insets.values()) and (best is None or sum(insets.values()) < sum(best[1].values())):
                best = (a, insets)
        op = s.call("get_shape_overprint", {"document_path": doc, "page_index": 1, "shape_id": u["shape_id"]})
        print(u["name"], "->", best and best[0]["name"], {k: round(v / MM_PER_IN, 4) for k, v in (best[1] if best else {}).items()},
              "fill_op", u.get("overprint_fill"), "tool", (op.get("data") or {}).get("overprint_fill", op.get("data")), "art_op", best and best[0].get("overprint_fill"))
    # bottom registration distance
    regs = [x for x in sh.values() if x["layer"]["name"] == "Registration" and x["parent_id"] is None]
    art_union = [x["bounds"] for x in arts + ubs]
    art_bottom = min(b["bottom"] for b in art_union)
    for r in regs:
        b = r["bounds"]
        print("reg", r["name"], "top", round((b["bottom"] + b["height"]) / MM_PER_IN, 3), "bottom", round(b["bottom"] / MM_PER_IN, 3),
              "gap below art (in)", round((art_bottom - (b["bottom"] + b["height"])) / MM_PER_IN, 3))
    print("art bottom (in)", round(art_bottom / MM_PER_IN, 3))
