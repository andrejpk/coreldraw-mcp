"""Profile .cdr copies read-only: pages, layers, object counts, depth, shape types, colors, text, effects, PowerClips.

Usage (from server/): profile_cdr.py <out.json> <run-folder> <file.cdr> [...]
Every file must lie under <run-folder>. Each document is opened (or matched if already open by path), read
with batched Shape.Evaluate queries, never saved, and closed with Dirty=False only if this script opened it.
"""
import json, ntpath, os, sys, time, traceback; sys.path.insert(0, ".")
from collections import Counter
from core.connection import get_connection
from tools import inspection
conn = get_connection()

SHAPE_TYPES = {0: "none", 1: "rectangle", 2: "ellipse", 3: "curve", 4: "polygon", 5: "bitmap", 6: "text",
               7: "group", 8: "selection", 9: "guideline", 10: "blend_group", 11: "extrude_group", 12: "ole",
               13: "contour_group", 14: "linear_dimension", 15: "bevel_group", 16: "drop_shadow_group",
               17: "3d_object", 18: "artistic_media_group", 19: "connector", 20: "mesh_fill", 21: "custom",
               22: "custom_effect_group", 23: "symbol", 24: "html_form", 25: "html_active", 26: "perfect_shape",
               27: "eps"}
FILL_TYPES = {0: "none", 1: "uniform", 2: "fountain", 7: "pattern", 9: "texture", 10: "postscript", 11: "hatch"}
CHUNK = 400  # records per bridge call (the bridge cancels one call after 60 s)


def norm(p):
    return ntpath.normcase(ntpath.normpath(p))


def find_doc(app, path):
    for i in range(1, app.Documents.Count + 1):
        d = app.Documents.Item(i)
        if d.FullFileName and norm(d.FullFileName) == norm(path):
            return d
    return None


def layer_flags(layer):
    info = {"name": layer.Name, "master": bool(layer.Master), "visible": bool(layer.Visible),
            "printable": bool(layer.Printable), "editable": bool(layer.Editable), "top_level": layer.Shapes.Count}
    for attr, key in (("IsGuidesLayer", "guides"), ("IsDesktopLayer", "desktop"), ("IsGridLayer", "grid")):
        try:
            if getattr(layer, attr):
                info["special"] = key
        except Exception:
            pass
    return info


class Profile:
    def __init__(self):
        self.types, self.fills, self.effects = Counter(), Counter(), Counter()
        self.spot, self.cmyk, self.rgb, self.reg, self.unknown = Counter(), Counter(), Counter(), Counter(), Counter()
        self.overprint = Counter()
        self.text = Counter()
        self.powerclips, self.powerclip_contents = 0, 0
        self.max_depth, self.recursive, self.unreadable = 0, 0, 0

    def color(self, c, role):
        kind = c.get("kind")
        if kind == "spot":
            self.spot[(c["name"], c["tint"], role)] += 1
        elif kind == "registration":
            self.reg[role] += 1
        elif kind == "process" and c["model"] == "CMYK":
            self.cmyk[(tuple(c["components"]), role)] += 1
        elif kind == "process":
            self.rgb[(tuple(c["components"]), role)] += 1
        else:
            self.unknown[(c.get("type"), role)] += 1

    def record(self, r, depth):
        self.recursive += 1
        self.max_depth = max(self.max_depth, depth)
        self.types[SHAPE_TYPES.get(r.get("type"), str(r.get("type")))] += 1
        if r.get("unreadable"):
            self.unreadable += 1
        fill = r.get("fill")
        if fill:
            self.fills[FILL_TYPES.get(fill["type"], str(fill["type"]))] += 1
            if fill["type"] == 1:
                self.color(fill["color"], "fill")
        outline = r.get("outline")
        if outline and outline["type"] != 0:
            self.color(outline["color"], "outline")
        for key in ("overprint_fill", "overprint_outline"):
            if r.get(key):
                self.overprint[key] += 1
        for e in r.get("effects") or []:
            self.effects[str(e)] += 1

    def summary(self):
        def spots():
            out = {}
            for (name, tint, role), n in sorted(self.spot.items()):
                out.setdefault(name, {}).setdefault(str(tint), {})[role] = n
            return out
        return {
            "recursive_objects": self.recursive, "max_group_depth": self.max_depth,
            "shape_types": dict(self.types.most_common()), "fill_types": dict(self.fills.most_common()),
            "spot_colors": spots(),
            "cmyk_colors": [{"cmyk": list(k), "role": role, "count": n} for (k, role), n in self.cmyk.most_common()],
            "rgb_colors": [{"rgb": list(k), "role": role, "count": n} for (k, role), n in self.rgb.most_common()],
            "registration": dict(self.reg), "unknown_colors": {f"{t}/{r}": n for (t, r), n in self.unknown.items()},
            "overprint": dict(self.overprint), "effects": dict(self.effects), "text": dict(self.text),
            "powerclips": self.powerclips, "powerclip_contents": self.powerclip_contents,
            "unreadable_records": self.unreadable,
        }


def walk_direct(shapes, prof, depth):
    """Direct reads for PowerClip contents, which the batched layer walk does not reach."""
    for shape in shapes:
        rec = inspection._inspect_shape(shape, None, {}, None)
        prof.record(rec, depth)
        prof.powerclip_contents += 1
        if shape.Type == 6:
            prof.text["paragraph" if shape.Text.Type == 1 else "artistic"] += 1
        if shape.Type == 7:
            walk_direct(shape.Shapes, prof, depth + 1)
        pc = shape.PowerClip
        if pc is not None:
            prof.powerclips += 1
            walk_direct(pc.Shapes, prof, depth + 1)


def profile_layer(path, page_index, layer_index, prof):
    """Run in pieces so no single bridge call approaches the 60 s limit."""
    def structure():
        layer = find_doc(conn.app, path).Pages(page_index).AllLayers.Item(layer_index)
        _, entries = inspection._layer_structure(layer)
        return entries
    r = conn.safe_call(structure)
    if not r["success"]:
        raise RuntimeError(r["error"])
    entries = r["result"]

    def chunk(start):
        layer = find_doc(conn.app, path).Pages(page_index).AllLayers.Item(layer_index)
        anchor = layer.Shapes.Item(1)
        part = entries[start:start + CHUNK]
        recs = inspection._read_records(layer, {}, anchor, part, None)
        extra = []
        for s in range(0, len(part), 100):
            sub = part[s:s + 100]
            q = []
            for p, _ in sub:
                base = inspection._query_path(p)
                q += [f"iif({base}.Type = 7, 0, {base}.PowerClip.Shapes.Count)",
                      f"iif({base}.Type = 6, {base}.Text.Type, -1)"]
            vals = inspection._query(anchor, q)
            extra += [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
        clips = []
        for (p, _), (pc, tt) in zip(part, extra):
            if pc:
                clips.append(p)
        content = Profile()
        for p in clips:
            walk_direct(inspection._resolve_path(layer, p).PowerClip.Shapes, content, len(p))
        return recs, extra, [len(p) for p, _ in part], len(clips), content
    for start in range(0, len(entries), CHUNK):
        r = conn.safe_call(chunk, start)
        if not r["success"]:
            raise RuntimeError(r["error"])
        recs, extra, depths, nclips, content = r["result"]
        for rec, (pc, tt), depth in zip(recs, extra, depths):
            prof.record(rec, depth - 1)
            if tt is not None and tt >= 0:
                prof.text["paragraph" if tt == 1 else "artistic"] += 1
        prof.powerclips += nclips + content.powerclips
        prof.powerclip_contents += content.powerclip_contents
        for attr in ("types", "fills", "effects", "spot", "cmyk", "rgb", "reg", "unknown", "overprint", "text"):
            getattr(prof, attr).update(getattr(content, attr))
        prof.recursive += content.recursive
        prof.max_depth = max(prof.max_depth, content.max_depth)


def profile(path):
    t0 = time.perf_counter()

    def open_doc():
        doc = find_doc(conn.app, path)
        opened = doc is None
        if opened:
            doc = conn.app.OpenDocument(path)
        return opened
    r = conn.safe_call(open_doc)
    if not r["success"]:
        return {"error": r["error"]}
    opened = r["result"]
    open_s = time.perf_counter() - t0
    out = {"file": ntpath.basename(path), "open_seconds": round(open_s, 2)}
    try:
        def doc_info():
            doc = find_doc(conn.app, path)
            pages = []
            for p in range(1, doc.Pages.Count + 1):
                page = doc.Pages(p)
                pages.append({"index": p, "name": page.Name, "width": page.SizeWidth, "height": page.SizeHeight,
                              "layers": [layer_flags(page.AllLayers.Item(i)) for i in range(1, page.AllLayers.Count + 1)]})
            return {"unit": doc.Unit, "pages": pages}
        r = conn.safe_call(doc_info)
        if not r["success"]:
            raise RuntimeError(r["error"])
        out.update(r["result"])
        total, seen_master = Profile(), set()
        for page in out["pages"]:
            pp = Profile()
            for i, layer in enumerate(page["layers"], start=1):
                if not layer["top_level"]:
                    continue
                if layer["master"]:
                    if layer["name"] in seen_master:
                        layer["note"] = "master layer; profiled on first page"
                        continue
                    seen_master.add(layer["name"])
                lp = Profile()
                profile_layer(path, page["index"], i, lp)
                layer["recursive"] = lp.recursive
                layer["max_group_depth"] = lp.max_depth
                for target in (pp, total):
                    for attr in ("types", "fills", "effects", "spot", "cmyk", "rgb", "reg", "unknown", "overprint", "text"):
                        getattr(target, attr).update(getattr(lp, attr))
                    target.recursive += lp.recursive
                    target.max_depth = max(target.max_depth, lp.max_depth)
                    target.powerclips += lp.powerclips
                    target.powerclip_contents += lp.powerclip_contents
                    target.unreadable += lp.unreadable
            page["top_level"] = sum(x["top_level"] for x in page["layers"] if not x.get("note"))
            page["recursive"] = pp.recursive
        out["summary"] = total.summary()
    except Exception:
        out["error"] = traceback.format_exc()
    finally:
        out["profile_seconds"] = round(time.perf_counter() - t0, 2)

        def close():
            doc = find_doc(conn.app, path)
            if doc is not None and opened:
                dirty = bool(doc.Dirty)
                doc.Dirty = False
                doc.Close()
                return dirty
        r = conn.safe_call(close)
        out["dirty_before_close"] = r.get("result")
    return out


def main():
    out_json, run = sys.argv[1], norm(sys.argv[2])
    files = sys.argv[3:]
    for f in files:
        if not norm(os.path.abspath(f)).startswith(run + "\\"):
            sys.exit(f"refusing {f}: not under the run folder")
    results = [profile(os.path.abspath(f)) for f in files]
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, ensure_ascii=False)
    for r in results:
        print(r["file"], r.get("open_seconds"), r.get("profile_seconds"), r.get("summary", {}).get("recursive_objects"),
              "ERROR" if r.get("error") else "")


main()
