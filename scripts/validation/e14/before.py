"""E14 baseline with the pre-E14 tools (run against a server started from the E19 commit 689f225): a whole-page
text audit of Good News p1 and Kings (runs per text shape, overflow, missing fonts), and a film-title mesh edit."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

GATE = Path(__file__).resolve().parents[1] / "e13b" / "gate.py"
if subprocess.run([sys.executable, str(GATE)]).returncode:
    sys.exit("gate stop")
RUN = Path(r"C:\CorelMcpLab\runs\e14\before")
RUN.mkdir(parents=True, exist_ok=True)
log = []


def timed(name, **args):
    r = call(name, **args)
    log.append({"tool": name, "seconds": r.get("_seconds") if isinstance(r, dict) else None,
                "success": isinstance(r, dict) and r.get("success"), "bytes": len(json.dumps(r, ensure_ascii=False)),
                "error": r.get("error") if isinstance(r, dict) else str(r)[:200]})
    return r


for stem, source in (("goodnews", r"C:\CorelMcpLab\runs\e14\goodnews.cdr"),
                     ("kings", r"C:\CorelMcpLab\runs\e14\kings.cdr")):
    path = str(RUN / f"{stem}.cdr")
    call("close_document", document_path=path, discard_changes=True)
    shutil.copy(source, path)
    assert call("open_document", path=path)["success"]
    start = len(log)
    found = timed("find_shapes", document_path=path, pages=[1], filters={"type": "text"}, limit=1000)
    ids = [s["id"] for s in found["data"]["shapes"]]
    for shape_id in ids:
        timed("inspect_text_fill_runs", document_path=path, page_index=1, shape_id=shape_id)
    timed("check_text_overflow_all", document_path=path)
    timed("check_missing_fonts", document_path=path)
    part = log[start:]
    print(stem, {"calls": len(part), "seconds": round(sum(x["seconds"] or 0 for x in part), 2),
                 "bytes": sum(x["bytes"] for x in part), "failed": sum(not x["success"] for x in part),
                 "texts": len(ids)})
    print("  overflow/fonts:", [(x["tool"], x["success"], x["error"]) for x in part[-2:]])
    call("close_document", document_path=path, discard_changes=True)

SJ = str(RUN / "screen_job.cdr")
call("close_document", document_path=SJ, discard_changes=True)
shutil.copy(r"C:\CorelMcpLab\templates\screen_job.cdr", SJ)
assert call("open_document", path=SJ)["success"]
start = len(log)
r = timed("set_text_content_by_id", document_path=SJ, page_index=1, shape_id="13", content="186 C 200")
runs = timed("inspect_text_fill_runs", document_path=SJ, page_index=1, shape_id="13")
print("title edit:", r.get("success"), runs["data"]["runs"] if runs.get("success") else runs)
r = timed("set_text_style", document_path=SJ, shape_id="13", size=30)
print("set_text_style:", r)
call("close_document", document_path=SJ, discard_changes=True)
(RUN / "calls.json").write_text(json.dumps(log, indent=1))
