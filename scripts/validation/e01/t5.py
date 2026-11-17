"""T5: per-page proof PDF via MCP, Ghostscript plate render, compare with client manifest."""
import sys, json, time, subprocess; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path
from audit_lib import Session
GS = r"C:\Program Files\gs\gs10.08.0\bin\gswin64c.exe"
PY = r"C:\repos\gh\coreldraw-mcp\.venv\Scripts\python.exe"
RENDER = r"C:\CorelMcpLab\wt\e01\server\render_separation_proof.py"
PROC = {"Cyan", "Magenta", "Yellow", "Black"}
tag = sys.argv[1]
with Session(r"C:\CorelMcpLab\runs\e01\calls.jsonl") as s:
    for f in sys.argv[2:]:
        key = f[:12].strip()
        inv = json.load(open(rf"C:\CorelMcpLab\runs\e01\inv\{key}.json", encoding="utf-8"))
        t2 = json.load(open(rf"C:\CorelMcpLab\runs\e01\inv\{key}.t2.json", encoding="utf-8"))
        doc = inv["doc"]; s.task = "T5 " + key
        s.call("open_template", {"path": doc}, note="re-activate")
        for p in inv["pages"]:
            pdf = Path(rf"C:\CorelMcpLab\runs\e01\proofs\{key}_{tag}_p{p}.pdf")
            r = s.call("export_page_proof_pdf", {"document_path": doc, "page_index": int(p), "output_path": str(pdf)})
            if not r.get("success"): print(key, p, "EXPORT FAIL", r.get("error")); continue
            outdir = pdf.with_suffix("")
            t = time.perf_counter()
            pr = subprocess.run([PY, RENDER, str(pdf), str(outdir), "--ghostscript", GS, "--dpi", "72"], capture_output=True, text=True)
            rs = round(time.perf_counter() - t, 2)
            if pr.returncode: print(key, p, "RENDER FAIL", pr.stderr[-500:]); continue
            man = json.loads((outdir / "manifest.json").read_text())
            inked = sorted(pl["name"] for pl in man["plates"] if not pl["blank"])
            m = t2["pages"][p]["manifest"]
            exp_spots = sorted(m["spots"])
            hidden = m["hidden_printable_layers"]
            exp_proc = sorted(k for k in m["other"] if k.startswith("CMYK") or k.startswith("RGB"))
            print(f"{key} p{p} export {r['_seconds']}s render {rs}s")
            print("   plates inked:", inked)
            print("   manifest spots:", exp_spots, "| process src:", exp_proc[:6], "| registration:", "registration" in m["other"], "| hidden printable:", hidden)
            print("   spots in manifest but blank plate:", sorted(set(exp_spots) - set(inked)), " inked spots not in manifest:", sorted(set(inked) - set(exp_spots) - PROC))
