"""Verify T6 outputs by proof render (MCP export + Ghostscript)."""
import sys, json, subprocess; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path
from PIL import Image, ImageOps, ImageChops, ImageFilter
from audit_lib import RENDER as R, RUN, Session
GS = r"C:\Program Files\gs\gs10.08.0\bin\gswin64c.exe"; PY = r"C:\repos\gh\coreldraw-mcp\.venv\Scripts\python.exe"
def proof(s, doc, name, dpi=150):
    pdf = Path(RUN + rf"\proofs\{name}.pdf")
    s.call("open_document", {"path": doc}, note="re-activate")
    r = s.call("export_page_proof_pdf", {"document_path": doc, "page_index": 1, "output_path": str(pdf)}); assert r["success"], r
    out = pdf.with_suffix(""); subprocess.run([PY, R, str(pdf), str(out), "--ghostscript", GS, "--dpi", str(dpi)], check=True)
    m = json.loads((out / "manifest.json").read_text()); return out, {p["name"]: p for p in m["plates"]}
def mask(out, p): return ImageOps.invert(Image.open(out / p["image"]).convert("L")).point(lambda v: 255 if v > 128 else 0)
with Session(RUN + r"\calls.jsonl") as s:
    s.task = "T6 verify"
    out, pl = proof(s, RUN + r"\mut\screen_job_mut.cdr", "screen_job_mut_v")
    white = mask(out, pl["PANTONE Trans. White"])
    inks = None
    for n in ("PANTONE 186 C", "PANTONE 300 C", "PANTONE 116 C"):
        m = mask(out, pl[n]); inks = m if inks is None else ImageChops.lighter(inks, m)
    # exclude film titles / registration: crop to the art region = bbox of union of inks within art layers is hard; use white bbox
    bb = white.getbbox(); print("white bbox px", bb)
    outside = ImageChops.subtract(white, inks).crop(bb); n_out = sum(outside.histogram()[255:])
    eroded_inks = inks.filter(ImageFilter.MinFilter(3))  # 1 px at 150 dpi = 0.48 pt
    print("white px outside inks:", n_out, " white px outside inks eroded by 1px:", sum(ImageChops.subtract(white, eroded_inks).crop(bb).histogram()[255:]))
    print("white px total", sum(white.crop(bb).histogram()[255:]))
    out2, pl2 = proof(s, RUN + r"\mut\kings_mut2.cdr", "kings_mut2_v", dpi=72)
    print("kings plates", {k: v["ink_bounds_px"] for k, v in pl2.items()})
