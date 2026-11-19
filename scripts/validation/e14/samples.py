"""E14 measurements through MCP on run copies: screen_job film title edit (spot ink kept, checked with
get_ink_inventory), Kings paragraph texts, Good News whole-page and whole-document inspect_text timings."""
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
RUN = Path(r"C:\CorelMcpLab\runs\e14\samples")
RUN.mkdir(parents=True, exist_ok=True)
log = []


def timed(name, **args):
    r = call(name, **args)
    log.append({"tool": name, "seconds": r.get("_seconds"), "success": r.get("success"),
                "bytes": len(json.dumps(r, ensure_ascii=False))})
    return r


# screen_job: film titles
SJ = str(RUN / "screen_job.cdr")
call("close_document", document_path=SJ, discard_changes=True)
shutil.copy(r"C:\CorelMcpLab\templates\screen_job.cdr", SJ)
assert call("open_document", path=SJ)["success"]
found = timed("find_shapes", document_path=SJ, pages=[1], filters={"type": "text"})
ids = [s["id"] if "id" in s else s["shape_id"] for s in found["data"]["shapes"]]
titles = timed("inspect_text", document_path=SJ, page_index=1, shape_ids=ids)
by_text = {s["text"]: s for s in titles["data"]["shapes"]}
print("titles:", {t: (s["id"], s["fill_runs"][0]["fill"]["ink"]) for t, s in by_text.items()})
inv_before = timed("get_ink_inventory", document_path=SJ, pages=[1])
title = by_text["PANTONE 186 C"]
r = timed("edit_text", document_path=SJ, page_index=1, shape_ids=[title["id"]], content="PANTONE 186 C 160")
print("append mesh:", r["success"], r["data"]["shapes"][0]["text"], r["data"]["shapes"][0]["replaced"],
      r["data"]["shapes"][0]["fill_runs"])
r = timed("edit_text", document_path=SJ, page_index=1, shape_ids=[title["id"]], content="186 C 200")
s = r["data"]["shapes"][0]
print("edit mesh:", r["success"], s["text"], s["replaced"], [(x["text"], x["fill"]) for x in s["fill_runs"]])
inv_after = timed("get_ink_inventory", document_path=SJ, pages=[1])


def text_inks(inv):
    out = {}
    for ink in inv["data"]["inks"] if "inks" in inv["data"] else inv["data"]["pages"][0]["inks"]:
        out[ink["ink"]] = ink.get("parts", {}).get("text_fill", 0)
    return out


print("ink text_fill before", text_inks(inv_before))
print("ink text_fill after ", text_inks(inv_after))
call("close_document", document_path=SJ, discard_changes=True)

# Kings: paragraph texts
K = str(RUN / "kings.cdr")
call("close_document", document_path=K, discard_changes=True)
shutil.copy(r"C:\CorelMcpLab\runs\e14\kings.cdr", K)
assert call("open_document", path=K)["success"]
r = timed("inspect_text", document_path=K)
print("kings:", r["message"], r["_seconds"], [(s["page_index"], s["id"], s["kind"], s["characters"],
                                               s.get("frame"), len(s.get("fill_runs", []))) for s in r["data"]["shapes"]])
print("kings fonts:", r["data"]["fonts"], r["data"]["missing_fonts"])
call("close_document", document_path=K, discard_changes=True)

# Good News: whole page and whole document
G = str(RUN / "goodnews.cdr")
call("close_document", document_path=G, discard_changes=True)
shutil.copy(r"C:\CorelMcpLab\runs\e14\goodnews.cdr", G)
assert call("open_document", path=G)["success"]
for attempt in (1, 2):
    r = timed("inspect_text", document_path=G, page_index=1)
    print(f"goodnews p1 #{attempt}:", r["message"], r["_seconds"], "s", log[-1]["bytes"], "bytes")
r = timed("inspect_text", document_path=G, page_index=1, include=["fonts", "frame"])
print("goodnews p1 fonts+frame:", r["message"], r["_seconds"], "s", log[-1]["bytes"], "bytes")
r = timed("inspect_text", document_path=G)
print("goodnews doc:", r["message"], r["_seconds"], "s", log[-1]["bytes"], "bytes", "next", r["data"]["next_offset"])
print("goodnews fonts:", [(f["font"], f["installed"], len(f["shape_ids"])) for f in r["data"]["fonts"]])
print("pages with text:", sorted({s["page_index"] for s in r["data"]["shapes"]}))
call("close_document", document_path=G, discard_changes=True)
(RUN / "calls.json").write_text(json.dumps(log, indent=1))
