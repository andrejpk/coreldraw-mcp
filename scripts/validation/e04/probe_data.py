"""Data for choosing preset defaults: sizes, off-page/crossing counts on the 4 sample copies (in-process)."""
import sys
from collections import Counter

sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, conn, open_mine, run  # noqa: E402

from core.units import convert, unit_name  # noqa: E402
from tools.inks import _page_inventory  # noqa: E402

DOCS = ["kings.cdr", "knights.cdr", "jump.cdr", "goodnews.cdr", "artifacts.cdr"]


def go():
    for name in DOCS:
        doc = open_mine(WORK + "\\" + name)
        unit = unit_name(doc.Unit)
        for index in range(1, doc.Pages.Count + 1):
            page = doc.Pages(index)
            inv = _page_inventory(conn, doc, page, index, unit, True, 5, False, collect=True)
            pb = inv["page_bounds"]
            objs = [o for o in inv["objects"] if o["bounds"]]
            mm = [(convert(max(o["bounds"][2] - o["bounds"][0], o["bounds"][3] - o["bounds"][1]), unit, "mm"), o)
                  for o in objs if o["state"] == "prints"]
            hist = Counter()
            for size, _ in mm:
                for t in (0.001, 0.01, 0.05, 0.1, 0.2, 0.5, 1.0):
                    if size < t:
                        hist[t] += 1
                        break
            crossing = [o for o in objs if o["state"] == "prints" and not (
                o["bounds"][0] >= pb["left"] and o["bounds"][2] <= pb["right"] and o["bounds"][1] >= pb["bottom"]
                and o["bounds"][3] <= pb["top"])]
            off = [o for o in objs if o["state"] == "off_page"]
            print(name, index, "objects", len(objs), "size<mm", dict(sorted(hist.items())), "off", len(off),
                  "off inks", Counter(u[1] for o in off for u in o["uses"]).most_common(3), "crossing", len(crossing),
                  Counter(o["layer"] for o in crossing).most_common(4))


run(go)
