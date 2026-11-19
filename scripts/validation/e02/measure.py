r"""E02+E05 measurements through MCP (server on 127.0.0.1:8765), logged with audit_lib.

    python measure.py <run dir>      e.g. C:\CorelMcpLab\runs\e02

T6b (E01): recolor PANTONE 186 C -> PANTONE 485 C on a fresh Kings copy (fills, outlines, text runs), verify with
get_ink_inventory and Ghostscript plates of export_page_proof_pdf, save as a new file.
GN: PANTONE 354 C -> PANTONE 485 C on a fresh Good News copy (dry run, real run, inventory, undo, inventory).
Results: <run dir>\measure.json; every call in <run dir>\calls.jsonl.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_lib import RENDER, Session  # noqa: E402

RUN = Path(sys.argv[1])
SAMPLES = Path(r"C:\CorelMcpLab\samples")
GS = next(iter(sorted(Path("C:/Program Files/gs").glob("*/bin/gswin64c.exe"))))
MUT, OUT = RUN / "mut", RUN / "out"
for folder in (MUT, OUT):
    folder.mkdir(parents=True, exist_ok=True)
KINGS, GOOD = MUT / "kings_t6b.cdr", MUT / "goodnews_354.cdr"
SAVED = OUT / "kings_485.cdr"
for path in (SAVED, OUT / "kings_485_p1.pdf"):
    path.unlink(missing_ok=True)
shutil.copyfile(SAMPLES / "51019 - Kings High School 2021 Shirt Order - SCREENS.cdr", KINGS)
shutil.copyfile(SAMPLES / "53210 - ME-MI Good News Club - SCREENS.cdr", GOOD)
results = {}


def inks(session, path, names):
    r = session.call("get_ink_inventory", {"document_path": str(path), "max_ids_per_ink": 50})
    found = {}
    for page in r["data"]["pages"]:
        for ink in page["inks"]:
            if ink["ink"] in names:
                found[f'p{page["page_index"]} {ink["ink"]}'] = {"objects": ink["objects"], "parts": ink["parts"]}
    return found, r


def plates(pdf):
    out = Path(pdf).with_suffix(".plates")
    shutil.rmtree(out, ignore_errors=True)
    done = subprocess.run([sys.executable, RENDER, str(pdf), str(out), "--ghostscript", str(GS), "--dpi", "72"],
                          capture_output=True, text=True, check=False)
    manifest = json.loads((out / "manifest.json").read_text()) if (out / "manifest.json").is_file() else {}
    return sorted(p["name"] for p in manifest.get("plates", []) if not p["blank"]), done.returncode


with Session(str(RUN / "calls.jsonl")) as s:
    s.task = "E02 T6b"
    t0 = time.perf_counter()
    calls = []

    def call(tool, args):
        r = s.call(tool, args)
        calls.append((tool, r.get("success"), len(json.dumps(r, default=str))))
        return r

    call("open_document", {"path": str(KINGS)})
    dry = call("replace_color", {"document_path": str(KINGS), "from_color": "PANTONE 186 C",
                                 "to_color": "PANTONE 485 C"})
    real = call("replace_color", {"document_path": str(KINGS), "from_color": "PANTONE 186 C",
                                  "to_color": "PANTONE 485 C", "dry_run": False})
    pdf = OUT / "kings_485_p1.pdf"
    proof = call("export_page_proof_pdf", {"document_path": str(KINGS), "page_index": 1, "output_path": str(pdf)})
    saved = call("save_document", {"document_path": str(KINGS), "save_as_path": str(SAVED)})
    mcp_seconds = round(time.perf_counter() - t0, 2)
    after, _ = inks(s, SAVED, ("PANTONE 186 C", "PANTONE 485 C"))
    inked, code = plates(pdf)
    results["t6b"] = {
        "calls": len(calls), "failed": sum(1 for _, ok, _ in calls if not ok), "seconds": mcp_seconds,
        "bytes": sum(size for _, _, size in calls), "dry_summary": dry["data"]["summary"],
        "real": {k: real["data"].get(k) for k in ("summary", "verified", "remaining", "seconds", "palette")},
        "inventory_after": after, "plates": inked, "render_exit": code, "saved": saved.get("success"),
        "proof": proof.get("success"),
    }
    print(json.dumps(results["t6b"], indent=1, default=str))
    s.call("close_document", {"document_path": str(SAVED), "discard_changes": True})

    s.task = "E02 GN 354"
    s.call("open_document", {"path": str(GOOD)})
    names = ("PANTONE 354 C", "PANTONE 485 C")
    before, inv_before = inks(s, GOOD, names)
    timings = {}
    for label, args in (("dry", {}), ("real", {"dry_run": False})):
        t = time.perf_counter()
        r = s.call("replace_color", {"document_path": str(GOOD), "from_color": "PANTONE 354 C",
                                     "to_color": "PANTONE 485 C", **args})
        timings[label] = {"seconds": round(time.perf_counter() - t, 2), "success": r.get("success"),
                          "error": r.get("error"), "bytes": len(json.dumps(r, default=str)),
                          **{k: r.get("data", {}).get(k) for k in ("summary", "verified", "remaining", "seconds")}}
    mid, _ = inks(s, GOOD, names)
    t = time.perf_counter()
    undone = s.call("undo", {"document_path": str(GOOD)})
    timings["undo"] = {"seconds": round(time.perf_counter() - t, 2), "success": undone.get("success")}
    restored, inv_after = inks(s, GOOD, names)
    status = s.call("get_coreldraw_status")
    results["goodnews"] = {
        "before": before, "after_replace": mid, "after_undo": restored, "timings": timings,
        "undo_restores_inventory": inv_before["data"]["pages"] == inv_after["data"]["pages"],
        "status_after": status.get("data", {}).get("state"),
    }
    print(json.dumps(results["goodnews"], indent=1, default=str))
    s.call("close_document", {"document_path": str(GOOD), "discard_changes": True})

(RUN / "measure.json").write_text(json.dumps(results, indent=1, default=str))
