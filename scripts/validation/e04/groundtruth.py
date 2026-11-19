"""Independent COM ground truth for find_shapes presets (direct per-shape COM reads, no CQL, no cache).

    python groundtruth.py <doc.cdr> <page> [<doc.cdr> <page> ...]   (run from server/, run-folder copies only)

Duplicates: identical type, bounds (1e-6), fill, outline (type, width, color) and node coordinate set, on printable
layers. Off page: bounds entirely outside the page. Prints the extra copies (all but the bottom-most) and the
find_shapes result for comparison.
"""
import sys
from collections import defaultdict

sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, open_mine, run_long  # noqa: E402

from core.drawing import walk_shapes  # noqa: E402
from tools.find import find_shapes  # noqa: E402

CONTAINERS = {7, 10, 11, 13, 15, 16, 18, 22}


def color(c):
    t = c.Type
    if t == 20:
        return ("reg",)
    if t == 1 or c.IsSpot:
        return ("spot", c.Name, round(c.Tint, 3))
    if t == 2:
        return ("cmyk", c.CMYKCyan, c.CMYKMagenta, c.CMYKYellow, c.CMYKBlack)
    if t == 5:
        return ("rgb", c.RGBRed, c.RGBGreen, c.RGBBlue)
    return ("type", t)


def paint(s):
    if s.Type == 6:
        story = s.Text.Story
        return ("text", story.Text, story.Fill.Type, color(story.Fill.UniformColor) if story.Fill.Type == 1 else None)
    f = s.Fill
    fill = (f.Type, color(f.UniformColor) if f.Type == 1 else None)
    o = s.Outline
    line = (o.Type, round(o.Width, 9), color(o.Color)) if o.Type else (0,)
    return fill, line


def nodes(s):
    if s.Type == 6:
        return None
    curve = s.DisplayCurve
    return tuple(sorted((round(n.PositionX, 4), round(n.PositionY, 4)) for n in curve.Nodes))


def truth(name, page_index):
    def go():
        doc = open_mine(WORK + "\\" + name)
        page = doc.Pages(page_index)
        pb = (page.LeftX, page.BottomY, page.RightX, page.TopY)
        shapes = []
        for order, layer in enumerate(page.AllLayers):
            if not layer.Printable or layer.Shapes.Count == 0:
                continue
            for z, (s, _) in enumerate(walk_shapes(layer.Shapes)):
                if s.Type in CONTAINERS or s.Type == 9:
                    continue
                b = (s.LeftX, s.BottomY, s.RightX, s.TopY)
                shapes.append(((order, z), str(s.StaticID), s.Type, b, s))
        buckets = defaultdict(list)
        for item in shapes:
            buckets[(item[2], *(round(v, 4) for v in item[3]))].append(item)
        extras = {}
        for group in buckets.values():
            if len(group) < 2:
                continue
            exact = defaultdict(list)
            for item in group:
                exact[(tuple(round(v, 4) for v in item[3]), str(paint(item[4])), nodes(item[4]))].append(item)
            for copies in exact.values():
                if len(copies) > 1:
                    copies.sort()
                    for item in copies[:-1]:
                        extras[item[1]] = copies[-1][1]
        off = sorted((item[1] for item in shapes if item[3][2] < pb[0] or item[3][0] > pb[2] or item[3][3] < pb[1]
                      or item[3][1] > pb[3]), key=int)
        return extras, off, len(shapes)

    return run_long(go)


args = sys.argv[1:] if __name__ == "__main__" else []
for name, page in zip(args[::2], args[1::2]):
    extras, off, total = truth(name, int(page))
    dups = find_shapes(WORK + "\\" + name, pages=[int(page)], preset="stacked_duplicates", limit=1000).data["shapes"]
    offp = find_shapes(WORK + "\\" + name, pages=[int(page)], preset="off_page", limit=1000).data["shapes"]
    found = {s["id"]: s["duplicate_of"] for s in dups}
    print(f"{name} p{page}: {total} shapes; duplicates truth {len(extras)} find {len(found)} "
          f"equal {extras == found}; off-page truth {len(off)} find "
          f"{sum(1 for s in offp if s['reason'] == 'off_page')} equal "
          f"{off == sorted((s['id'] for s in offp if s['reason'] == 'off_page'), key=int)}")
    if extras != found:
        print("   only truth", {k: v for k, v in extras.items() if found.get(k) != v})
        print("   only find ", {k: v for k, v in found.items() if extras.get(k) != v})
