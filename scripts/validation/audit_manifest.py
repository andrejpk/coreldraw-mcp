"""Client-side film manifest and artifact finder over inspect_shapes inventories (E01 audit).

The server has no per-page plate/ink tool, so an agent must page inspect_shapes and compute this itself.
This module does that computation from inventories saved by audit_e01.py, so its output can be compared with
what the server's own color tools report.

    manifest(inv_page) -> {spots: {name: {fill, outline, text, ids}}, process: [...], registration, rgb, ...}
    artifacts(inv_page) -> {zero_size, invisible, duplicates, near_spots, trans_white, hidden_printable}
"""
import re
from collections import defaultdict

GUIDELINE, DIMENSION, GROUP, TEXT = 9, 14, 7, 6


def _printable(s):
    return s["layer"]["printable"]


def _paints(s):
    """Yield (part, color) for painted parts. Outline counts only with a nonzero width."""
    f = s.get("fill") or {}
    if f.get("type") == 1 and f.get("color"):
        yield "fill", f["color"]
    o = s.get("outline") or {}
    if o.get("type") == 1 and o.get("color"):
        yield ("outline" if (o.get("width") or 0) > 0 else "outline_zero_width"), o["color"]
    elif o.get("type") not in (None, 0):
        yield "outline", {"kind": "unknown", "type": o.get("type")}
    if f.get("type") not in (None, 0, 1):
        yield "fill", {"kind": "nonuniform", "type": f.get("type")}


def color_key(c):
    if c["kind"] == "spot":
        return f'spot:{c["name"]}'
    if c["kind"] == "process":
        return f'{c["model"]}:{tuple(c["components"])}'
    return c["kind"]


def manifest(shapes, text_runs=None):
    """Inks used by printable content on one page. text_runs maps shape_id -> runs from inspect_text_fill_runs."""
    spots = defaultdict(lambda: {"fill": 0, "outline": 0, "outline_zero_width": 0, "text": 0, "tints": set(),
                                 "ids": []})
    other = defaultdict(lambda: {"count": 0, "ids": [], "parts": set(), "layers": set()})
    hidden_printable = defaultdict(int)
    for s in shapes:
        if not _printable(s) or s.get("type") in (GUIDELINE,):
            continue
        if not s["layer"]["visible"]:
            hidden_printable[s["layer"]["name"]] += 1
        paints = list(_paints(s))
        if s.get("type") == TEXT and text_runs and s["shape_id"] in text_runs:
            paints = [p for p in paints if p[0] != "fill"] + [("text", r["fill"]["color"]) for r in
                                                              text_runs[s["shape_id"]] if r.get("fill", {}).get("color")]
        for part, c in paints:
            if c["kind"] == "spot":
                e = spots[c["name"]]
                e[part] = e.get(part, 0) + 1
                e["tints"].add(c.get("tint"))
                e["ids"].append(s["shape_id"])
            else:
                k = color_key(c)
                e = other[k]
                e["count"] += 1
                e["ids"].append(s["shape_id"])
                e["parts"].add(part)
                e["layers"].add(s["layer"]["name"])
    return {"spots": _plain(spots), "other": _plain(other), "hidden_printable_layers": dict(hidden_printable)}


def _plain(d):
    return {k: {kk: (sorted(vv, key=str) if isinstance(vv, set) else vv) for kk, vv in v.items()} for k, v in d.items()}


def _spot_number(name):
    m = re.search(r"(\d{3,4})", name)
    return int(m.group(1)) if m else None


def artifacts(shapes, page_w=None, page_h=None):
    out = {"zero_size": [], "invisible": [], "duplicates": [], "near_spots": [], "trans_white": [],
           "hairline": [], "off_page": [], "hidden_printable": []}
    seen = defaultdict(list)
    spots_on_page = set()
    for s in shapes:
        if s.get("type") in (GROUP, GUIDELINE):
            continue
        b = s.get("bounds") or {}
        f, o = s.get("fill") or {}, s.get("outline") or {}
        if b and (b.get("width", 1) < 0.01 or b.get("height", 1) < 0.01):
            out["zero_size"].append((s["shape_id"], s["layer"]["name"], b))
        if f.get("type") == 0 and o.get("type") == 0:
            out["invisible"].append((s["shape_id"], s["layer"]["name"]))
        if o.get("type") == 1 and 0 < (o.get("width") or 0) < 0.1:
            out["hairline"].append((s["shape_id"], s["layer"]["name"], o.get("width")))
        for part, c in _paints(s):
            if c["kind"] == "spot":
                if _printable(s):
                    spots_on_page.add(c["name"])
                if "trans" in c["name"].lower() and "white" in c["name"].lower():
                    out["trans_white"].append((s["shape_id"], s["layer"]["name"], part, s.get("type")))
        if b:
            key = (s["layer"]["name"], s.get("type"), round(b["left"], 2), round(b["bottom"], 2),
                   round(b["width"], 2), round(b["height"], 2), str(f.get("color")), str(o.get("color")))
            seen[key].append(s["shape_id"])
            if page_w and (b["left"] + b["width"] < 0 or b["left"] > page_w or b["bottom"] > page_h
                           or b["bottom"] + b["height"] < 0) and _printable(s):
                out["off_page"].append((s["shape_id"], s["layer"]["name"]))
        if _printable(s) and not s["layer"]["visible"]:
            out["hidden_printable"].append((s["shape_id"], s["layer"]["name"]))
    out["duplicates"] = [ids for ids in seen.values() if len(ids) > 1]
    nums = sorted((n, name) for name in spots_on_page if (n := _spot_number(name)) is not None)
    out["near_spots"] = [(a[1], b[1]) for a, b in zip(nums, nums[1:]) if 0 < b[0] - a[0] <= 2]
    out["hidden_printable"] = {k: v for k, v in _count(out["hidden_printable"]).items()}
    return out


def _count(pairs):
    c = defaultdict(int)
    for _, layer in pairs:
        c[layer] += 1
    return c
