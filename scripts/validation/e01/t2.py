"""T2/T3: film manifest + artifact finder per page, using MCP for text runs and built-in color tools for comparison."""
import sys, json, time; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import Session
from audit_manifest import manifest, artifacts
W = r"C:\CorelMcpLab\runs\e01\work" + "\\"
out = {}
with Session(r"C:\CorelMcpLab\runs\e01\calls.jsonl") as s:
    for f in sys.argv[1:]:
        key = f[:12].strip()
        inv = json.load(open(r"C:\CorelMcpLab\runs\e01\inv\%s.json" % key, encoding="utf-8"))
        doc = inv["doc"]
        s.task = "T2 " + key
        t0 = time.perf_counter()
        # re-activate: the only way is open_template on the same path
        o = s.call("open_template", {"path": doc}, note="re-activate already open document")
        info = s.call("get_document_info")
        res = {"reactivate": {"open": o.get("success"), "active": info["data"]["document_path"]}, "pages": {}}
        sep = s.call("get_print_separation_settings", {"document_path": doc})
        res["separation_settings"] = {"enabled_plates": [p.get("label") for p in (sep.get("data") or {}).get("available_plates") or [] if p.get("enabled")],
                                      "unavailable": (sep.get("data") or {}).get("unavailable")}
        for p, page in inv["pages"].items():
            shapes = page["shapes"]
            runs = {}
            for sh in shapes:
                if sh.get("type") == 6 and sh["layer"]["printable"]:
                    r = s.call("inspect_text_fill_runs", {"document_path": doc, "page_index": int(p), "shape_id": sh["shape_id"]})
                    if r.get("success"):
                        runs[sh["shape_id"]] = r["data"]["runs"]
                    else:
                        res.setdefault("text_run_errors", []).append((p, sh["shape_id"], r.get("error")))
            txt = s.call("list_all_text_shapes", {"page_index": int(p), "document_path": doc})
            s.call("switch_page", {"page_index": int(p)})
            rep = s.call("get_color_report"); rgb = s.call("check_rgb_colors")
            m = manifest(shapes, runs)
            a = artifacts(shapes, inv["info"]["width"], inv["info"]["height"])
            res["pages"][p] = {"manifest": m, "artifacts": a, "text_runs": runs,
                               "texts": [(t.get("shape_id"), t.get("text") or t.get("content_preview")) for t in (txt.get("data") or {}).get("shapes", txt.get("data", {}).get("items", []))] if txt.get("success") else txt.get("error"),
                               "get_color_report": rep.get("data"), "check_rgb_colors": rgb.get("data")}
        res["seconds"] = round(time.perf_counter() - t0, 2)
        out[key] = res
        json.dump(res, open(r"C:\CorelMcpLab\runs\e01\inv\%s.t2.json" % key, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        print(key, res["seconds"], res["reactivate"], res["separation_settings"])
        for p, v in res["pages"].items():
            print(" page", p, "spots:", {k: (x["fill"], x["outline"], x.get("text", 0), x["tints"]) for k, x in v["manifest"]["spots"].items()})
            print("   other:", {k: (x["count"], x["parts"], x["layers"]) for k, x in v["manifest"]["other"].items()})
            print("   hidden printable:", v["manifest"]["hidden_printable_layers"])
            print("   artifacts:", {k: (len(x) if isinstance(x, list) else x) for k, x in v["artifacts"].items()})
            print("   color_report:", json.dumps(v["get_color_report"], ensure_ascii=False)[:300])
            print("   rgb:", json.dumps(v["check_rgb_colors"], ensure_ascii=False)[:200])
