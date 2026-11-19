r"""E01 task T2 redone with get_ink_inventory: film manifest per page plus the sources of process, registration
and RGB ink, for the six E01 documents. One open_document and one get_ink_inventory call per document.

    python t2.py <run_dir> <doc.cdr> [...]       (logs to <run_dir>\t2.jsonl, writes <run_dir>\t2.json)
"""
import json
import ntpath
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_lib import Session, summarize_log  # noqa: E402

run_dir = Path(sys.argv[1])
log = run_dir / "t2.jsonl"
log.unlink(missing_ok=True)
manifest = {}
with Session(str(log)) as session:
    for document in sys.argv[2:]:
        session.task = "T2 " + ntpath.basename(document)[:12]
        assert session.call("open_document", {"path": document, "activate": False}).get("success")
        result = session.call("get_ink_inventory", {"document_path": document, "max_ids_per_ink": 5})
        assert result.get("success"), result
        pages = {}
        for page in result["data"]["pages"]:
            pages[page["page_index"]] = {
                "films": page["plates"]["print"],
                "registration_only": page["plates"]["registration_only"],
                "blank_spot_plates": page["plates"]["blank_spot_plates"],
                "process_sources": {ink["ink"]: [i["id"] for i in ink.get("ids", []) if i["state"] == "prints"]
                                    for ink in page["inks"] if ink["kind"] in ("process", "rgb", "other", "bitmap")
                                    and ink["objects"].get("prints")},
                "warnings": {w["code"]: w["count"] for w in page["warnings"]},
            }
        manifest[ntpath.basename(document)] = pages
(run_dir / "t2.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
print(json.dumps(summarize_log(str(log)), indent=1))
for name, pages in manifest.items():
    for index, page in pages.items():
        print(name[:20], index, page["films"], "process sources:", page["process_sources"])
