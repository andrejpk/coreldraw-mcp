r"""Ground truth for get_ink_inventory (E03): predicted plates against Ghostscript renders of proof PDFs.

Through the running MCP server: opens each document copy (without activating it), runs get_ink_inventory once
per document, publishes every page with export_page_proof_pdf, renders the plates with
server/render_separation_proof.py (tiffsep) and compares each page's non-blank plates with the inventory's
plates.pdf_proof. Calls are logged with audit_lib (calls, seconds, bytes).

    python compare_plates.py <run_dir> <doc.cdr> [<doc.cdr> ...]

Writes <run_dir>\plates\<doc>\p<N>.pdf, the renders, and <run_dir>\plates\compare.json.
"""
import json
import ntpath
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_lib import RENDER, Session  # noqa: E402

GS = next(iter(sorted(Path("C:/Program Files/gs").glob("*/bin/gswin64c.exe"))))


def render(pdf, out):
    shutil.rmtree(out, ignore_errors=True)
    result = subprocess.run([sys.executable, RENDER, str(pdf), str(out), "--ghostscript", str(GS), "--dpi", "72"],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stdout[-800:] + result.stderr[-800:])
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def main():
    run_dir = Path(sys.argv[1])
    out_root = run_dir / "plates"
    out_root.mkdir(exist_ok=True)
    report = {}
    with Session(str(run_dir / "calls.jsonl")) as session:
        for document in sys.argv[2:]:
            name = ntpath.basename(document)
            session.task = "plates " + name
            opened = session.call("open_document", {"path": document, "activate": False})
            assert opened.get("success"), opened
            started = time.perf_counter()
            inventory = session.call("get_ink_inventory", {"document_path": document})
            assert inventory.get("success"), inventory
            entry = {"inventory_seconds": inventory["_seconds"], "pages": {}}
            data = inventory["data"]
            while data.get("next_pages"):
                more = session.call("get_ink_inventory", {"document_path": document, "pages": data["next_pages"]})
                assert more.get("success"), more
                entry["inventory_seconds"] += more["_seconds"]
                data["pages"] += more["data"]["pages"]
                data["next_pages"] = more["data"]["next_pages"]
            entry["inventory_calls_wall"] = round(time.perf_counter() - started, 2)
            folder = out_root / Path(name).stem
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir()
            for page in data["pages"]:
                index = page["page_index"]
                pdf = folder / f"p{index}.pdf"
                proof = session.call("export_page_proof_pdf", {"document_path": document, "page_index": index,
                                                               "output_path": str(pdf)})
                assert proof.get("success"), proof
                manifest = render(pdf, folder / f"p{index}")
                rendered = sorted(plate["name"] for plate in manifest["plates"] if not plate["blank"])
                present = sorted(plate["name"] for plate in manifest["plates"])
                predicted = page["plates"]["pdf_proof"]
                entry["pages"][index] = {
                    "predicted": predicted, "rendered": rendered, "present_in_pdf": present,
                    "missing_from_render": sorted(set(predicted) - set(rendered)),
                    "unexpected_in_render": sorted(set(rendered) - set(predicted)),
                    "plates": page["plates"],
                    "warnings": [warning["code"] for warning in page["warnings"]],
                }
                status = "OK" if set(predicted) == set(rendered) else "MISMATCH"
                print(f"{name} p{index}: {status} predicted={predicted} rendered={rendered}")
            report[name] = entry
    (out_root / "compare.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


main()
