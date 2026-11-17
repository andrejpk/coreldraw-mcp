"""Check template copies against MANIFEST.json through the running MCP server (mc.py, port 8765).

Usage (from scripts/validation/): verify_templates.py <manifest.json> <copies_dir> <out.json> [file.cdr ...]
<copies_dir> holds fresh copies of the templates in a run folder. Each copy is opened with open_document
and left open; close them afterwards with close_run_docs.py once the server is stopped.
"""
import json, ntpath, sys, time
from collections import Counter
import asyncio
from fastmcp import Client
from mc import URL


def call(tool_name, args):
    """Like mc.call, but takes args as a dict (mc.call cannot pass a tool argument called "name")."""
    async def go():
        async with Client(URL, timeout=900) as c:
            r = await c.call_tool(tool_name, args, raise_on_error=False, timeout=900)
            return r.structured_content if r.structured_content is not None else [getattr(x, "text", x) for x in r.content]
    t = time.perf_counter(); out = asyncio.run(go())
    if isinstance(out, dict):
        out["_seconds"] = round(time.perf_counter() - t, 2)
    return out

MANIFEST, COPIES, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
ONLY = set(sys.argv[4:])
UNIT_TO_IN = {"in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54, "pt": 1 / 72}
TOL = 0.003


def same_color(want, got):
    if want is None:
        return got is None
    if got is None or want["kind"] != got.get("kind"):
        return False
    if want["kind"] == "spot":
        return want["name"] == got.get("name") and want["tint"] == got.get("tint")
    if want["kind"] == "process":
        return want["model"] == got.get("model") and [round(x) for x in want["components"]] == \
            [round(x) for x in got.get("components", [])]
    return True


def reported_fill(rec):
    f = rec.get("fill")
    if f is None:
        return "absent"
    return f.get("color") if f["type"] == 1 else None if f["type"] == 0 else {"kind": "unknown"}


def reported_outline(rec):
    o = rec.get("outline")
    if o is None:
        return "absent"
    return None if o["type"] == 0 else o.get("color")


def verify(template):
    path = ntpath.join(COPIES, template["file"])
    res = {"file": template["file"], "issues": [], "timings": {}, "tool_failures": []}

    def tool(tool_name, **args):
        raw = call(tool_name, args)
        r = dict(raw.get("data") or {}) if isinstance(raw, dict) else {}
        r.update(success=raw.get("success") if isinstance(raw, dict) else False,
                 error=raw.get("error") if isinstance(raw, dict) else raw,
                 message=raw.get("message") if isinstance(raw, dict) else None,
                 _seconds=raw.get("_seconds") if isinstance(raw, dict) else None)
        res["timings"].setdefault(tool_name, []).append(r.get("_seconds"))
        if not r.get("success", False):
            res["tool_failures"].append({"tool": tool_name, "args": {k: v for k, v in args.items() if k != "document_path"},
                                         "error": r.get("error") or r})
        return r

    opened = tool("open_document", path=path)
    if not opened.get("success"):
        return res
    res["document_info"] = {k: opened.get(k) for k in ("page_count", "unit", "pages", "already_open")}
    by_id, unit = {}, None
    for page in template["pages"]:
        p = page["index"]
        layers = tool("get_page_layers", document_path=path, page_index=p)
        if layers.get("success"):
            got = [(x.get("name"), x.get("visible"), x.get("printable"), x.get("editable"), x.get("master"))
                   for x in layers["layers"]]
            want = [(x["name"], x["visible"], x["printable"], x["editable"], x["master"]) for x in page["layers"]]
            if got != want:
                res["issues"].append({"page": p, "check": "layers", "want": want, "got": got})
        ins = tool("inspect_shapes", document_path=path, page_index=p)
        if not ins.get("success"):
            continue
        unit = ins.get("document_unit")
        res.setdefault("inspected", {})[p] = len(ins["shapes"])
        for rec in ins["shapes"]:
            by_id.setdefault(rec["shape_id"], {})[p] = rec
        if template["file"] == "big_flat.cdr":
            tally = Counter((r.get("name"), json.dumps(r.get("fill"), sort_keys=True), r["outline"]["type"],
                             r.get("overprint_fill"), r.get("overprint_outline")) for r in ins["shapes"])
            want = Counter({(x["name"], json.dumps(x["fill"], sort_keys=True), x["outline_type"], x["overprint_fill"],
                             x["overprint_outline"]): x["count"] for x in template["inventory"]})
            if tally != want:
                res["issues"].append({"check": "big_flat inventory", "want": len(want), "got": len(tally)})
            t = time.perf_counter(); n, off = 0, 0
            while off is not None:
                r = tool("inspect_shapes", document_path=path, page_index=1, offset=off, limit=250,
                         fields=["type", "fill"])
                if not r.get("success"):
                    break
                n += len(r["shapes"]); off = r["next_offset"]
            res["big_paged"] = {"shapes": n, "seconds": round(time.perf_counter() - t, 1)}
    scale = UNIT_TO_IN.get(unit, 1.0)
    res["document_unit_after_open"] = unit
    for s in template["shapes"]:
        sid, want = s["shape_id"], s["intended"]
        pages = by_id.get(sid)
        if not pages:
            res["issues"].append({"shape": s["name"], "check": "present in inspect_shapes", "got": "missing",
                                  "why": "PowerClip content" if "powerclip_content_of" in want else None})
            continue
        p = s["page"] if s["page"] in pages else next(iter(pages))
        rec = pages[p]
        if want.get("master") and len(pages) != len(template["pages"]):
            res["issues"].append({"shape": s["name"], "check": "master shape on every page", "got": sorted(pages)})
        checks = [("name", s["name"], rec.get("name")), ("type", s["type"], rec.get("type")),
                  ("layer", s["layer"], rec.get("layer", {}).get("name"))]
        if s.get("bounds") and rec.get("bounds"):
            b = rec["bounds"]
            got = [b["left"] * scale, b["bottom"] * scale, b["width"] * scale, b["height"] * scale]
            if any(abs(x - y) > TOL for x, y in zip(s["bounds"], got)):
                checks.append(("bounds_in", s["bounds"], [round(x, 4) for x in got]))
        if "fill" in want:
            got = reported_fill(rec)
            if got == "absent" or not same_color(want["fill"], got):
                checks.append(("fill", want["fill"], got))
        if "outline" in want:
            got = reported_outline(rec)
            if got == "absent" or not same_color(want["outline"], got):
                checks.append(("outline", want["outline"], got))
        if "outline_width" in want and rec.get("outline", {}).get("width") is not None:
            if abs(rec["outline"]["width"] * scale - want["outline_width"]) > 0.0005:
                checks.append(("outline_width_in", want["outline_width"], rec["outline"]["width"] * scale))
        for flag in ("overprint_fill", "overprint_outline"):
            if flag in want and rec.get(flag) != want[flag]:
                checks.append((flag, want[flag], rec.get(flag)))
        if want.get("effect"):
            checks.append(("effects", "non-empty", rec.get("effects"))) if not rec.get("effects") else None
        for check, w, g in checks:
            if check in ("name", "type", "layer") and w == g:
                continue
            res["issues"].append({"shape": s["name"], "check": check, "want": w, "got": g})
        if "runs" in want:
            r = tool("inspect_text_fill_runs", document_path=path, page_index=p, shape_id=sid)
            if r.get("success"):
                got = [(x.get("start"), x.get("end"), x.get("fill", {}).get("color")) for x in r["runs"]]
                exp = [(x["range"][0], x["range"][1], x["fill"]) for x in want["runs"]]
                if len(got) != len(exp) or not all(a[:2] == tuple(b[:2]) and same_color(b[2], a[2])
                                                   for a, b in zip(got, exp)):
                    res["issues"].append({"shape": s["name"], "check": "text runs", "want": exp, "got": r["runs"]})
        if want.get("effect") == "contour":
            r = tool("inspect_contours", document_path=path, page_index=p, shape_id=sid)
            if r.get("success"):
                res.setdefault("contours", {})[s["name"]] = r["contours"]
        if want.get("effect") == "drop_shadow":
            res.setdefault("drop_shadow_effects", {})[s["name"]] = rec.get("effects")
    if template["file"] == "effects.cdr":
        op_both = next(s["shape_id"] for s in template["shapes"] if s["name"] == "op_both")
        r = tool("inspect_shapes", document_path=path, page_index=1, fields=["overprint_fill", "overprint_outline"])
        record = next((x for x in r.get("shapes", []) if x["shape_id"] == op_both), {})
        res["overprint_op_both"] = {k: record.get(k) for k in ("overprint_fill", "overprint_outline")}
    if template["file"] in ("text_runs.cdr", "structure.cdr"):
        r = tool("find_shapes", document_path=path, pages=[1], filters={"type": "text"})
        res["text_shapes"] = r.get("count")
    if template["file"] in ("colors.cdr", "screen_job.cdr"):
        r = tool("get_ink_inventory", document_path=path)
        res["get_ink_inventory"] = {k: v for k, v in r.items() if k not in ("_seconds",)} if r.get("success") else r
    if template["file"] == "screen_job.cdr":
        r = tool("get_print_separation_settings", document_path=path)
        res["separation_settings"] = r
        pdf = ntpath.join(COPIES, "screen_job_proof.pdf")
        r = tool("export_page_proof_pdf", document_path=path, page_index=1, output_path=pdf)
        res["proof_pdf"] = {k: v for k, v in r.items() if k != "_seconds"}
    r = tool("find_shapes", document_path=path, filters={"name": template["shapes"][0]["name"]})         if template["shapes"] else {}
    res["find_by_name"] = {k: v for k, v in r.items() if k != "_seconds"} if r else None
    return res


def main():
    with open(MANIFEST, encoding="utf-8") as fh:
        manifest = json.load(fh)
    results = []
    for t in manifest["templates"]:
        if ONLY and t["file"] not in ONLY:
            continue
        t0 = time.perf_counter()
        r = verify(t)
        r["seconds"] = round(time.perf_counter() - t0, 1)
        results.append(r)
        print(f"{t['file']}: {len(r['issues'])} issues, {len(r['tool_failures'])} tool failures, {r['seconds']} s")
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, ensure_ascii=False, default=str)


main()
