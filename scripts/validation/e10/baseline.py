r"""E10 baseline (before the change): per page, export_page_proof_pdf through MCP plus the standalone
render_separation_proof.py at 150 dpi, timed. This is the E01 T5 workflow an agent had to hand-roll.

    python baseline.py <run_dir> <doc.cdr> [...]
"""
import json
import ntpath
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

RENDER = str(Path(__file__).resolve().parents[3] / "server" / "render_separation_proof.py")
GS = r"C:\Program Files\gs\gs10.08.0\bin\gswin64c.exe"


def main():
    run = Path(sys.argv[1]) / "baseline"
    run.mkdir(exist_ok=True)
    rows = []
    for document in sys.argv[2:]:
        opened = call("open_document", path=document, activate=False)
        assert opened.get("success"), opened
        pages = opened["data"].get("page_count") or opened["data"].get("pages") or 1
        stem = Path(ntpath.basename(document)).stem[:20].replace(" ", "_")
        for page in range(1, int(pages) + 1):
            pdf = run / f"{stem}_p{page}.pdf"
            pdf.unlink(missing_ok=True)
            proof = call("export_page_proof_pdf", document_path=document, page_index=page, output_path=str(pdf))
            started = time.perf_counter()
            out = run / f"{stem}_p{page}"
            result = subprocess.run([sys.executable, RENDER, str(pdf), str(out), "--ghostscript", GS, "--dpi", "150"],
                                    capture_output=True, text=True, check=False)
            render_seconds = round(time.perf_counter() - started, 2)
            plates = []
            if not result.returncode:
                manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
                plates = [p["name"] for p in manifest["plates"] if not p["blank"]]
            rows.append({"document": ntpath.basename(document), "page": page, "export_s": proof.get("_seconds"),
                         "render_s": render_seconds, "plates": plates, "ok": proof.get("success")})
            print(rows[-1])
    (run / "baseline.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


main()
