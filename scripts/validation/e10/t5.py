r"""E01 task T5 redone with render_plates: page proof + plate render + check against the inventory, every page.

E01 T5: 17 MCP calls + 11 hand-run Ghostscript renders, 7.8 s MCP + 0.6-1.0 s per render; process plates
misleading (K-only objects on C/M/Y), registration made every plate bbox page-wide, no attribution, hidden-but-
printable Halftone Scale missing silently.

    python t5.py <out_dir> <doc.cdr> [...]

One open_document per document and one render_plates per page. Writes <out_dir>\t5.json (calls, seconds, bytes,
per-page plates, registration-only plates, differences, warnings).
"""
import json
import ntpath
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402


def main():
    out = Path(sys.argv[1])
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    calls, rows = [], []
    started = time.perf_counter()
    for document in sys.argv[2:]:
        opened = call("open_document", path=document, activate=False)
        calls.append(("open_document", opened.get("_seconds"), len(json.dumps(opened)), opened.get("success")))
        stem = Path(ntpath.basename(document)).stem[:16].strip().replace(" ", "_")
        for page in range(1, opened["data"]["page_count"] + 1):
            r = call("render_plates", document_path=document, page_index=page, output_dir=str(out / f"{stem}_p{page}"))
            calls.append(("render_plates", r.get("_seconds"), len(json.dumps(r)), r.get("success")))
            d = r.get("data") or {}
            rows.append({
                "document": ntpath.basename(document), "page": page, "seconds": r.get("_seconds"),
                "tool_seconds": d.get("seconds"), "rendered": d.get("rendered_plates"),
                "predicted": d.get("predicted_plates"),
                "registration_only": [p["plate"] for p in d.get("plates", []) if p["registration_only"]],
                "art_plates": {p["plate"]: p.get("attributed_count", 0) for p in d.get("plates", [])
                               if p["non_blank"] and not p["registration_only"]},
                "differences": [(x["type"], x["plate"]) for x in d.get("differences", [])],
                "warnings": [w["code"] for w in d.get("warnings", [])], "error": r.get("error"),
            })
            print(json.dumps(rows[-1]))
    summary = {"calls": len(calls), "failed": sum(1 for c in calls if not c[3]),
               "mcp_seconds": round(sum(c[1] or 0 for c in calls), 2),
               "wall_seconds": round(time.perf_counter() - started, 2), "bytes": sum(c[2] for c in calls),
               "pages": len(rows), "agree": sum(1 for r in rows if r["rendered"] is not None
                                                and sorted(r["rendered"]) == sorted(r["predicted"]))}
    print(json.dumps(summary))
    (out / "t5.json").write_text(json.dumps({"summary": summary, "calls": calls, "pages": rows}, indent=1),
                                 encoding="utf-8")


main()
