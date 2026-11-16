"""Render an independent PDF composite and individual ink plates with Ghostscript."""

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps


def render(pdf: Path, output: Path, ghostscript: Path, dpi: int, page: int) -> dict:
    """Render one PDF page and produce a manifest of visible marks on each plate."""
    pdf = pdf.resolve(strict=True)
    ghostscript = ghostscript.resolve(strict=True)
    output = output.resolve()
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Choose a new output directory within an existing directory")
    if not 36 <= dpi <= 600 or page < 1:
        raise ValueError("Use 36–600 dpi and a one-based page number")

    with TemporaryDirectory(prefix="separation-proof-", dir=output.parent) as scratch:
        work = Path(scratch)
        ready = work / "proof"
        ready.mkdir()
        composite_tiff = work / "ink.tif"
        result = subprocess.run(
            [str(ghostscript), "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dPDFSTOPONERROR",
             "-sDEVICE=tiffsep", f"-r{dpi}", f"-dFirstPage={page}", f"-dLastPage={page}",
             f"-sOutputFile={composite_tiff}", "-f", str(pdf)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
        if result.returncode or not composite_tiff.is_file():
            raise RuntimeError(f"Ghostscript did not produce the requested page: {result.stdout[-1500:]}")

        with composite_tiff.open("rb") as source, Image.open(source) as composite:
            composite.convert("RGB").save(ready / "composite.png")
            width, height = composite.size

        spot_names = [line.removeprefix("%%SeparationName: ") for line in result.stdout.splitlines()
                      if line.startswith("%%SeparationName: ")]
        paths = sorted([*work.glob("ink(*).tif"), *work.glob("ink.*.tif")])
        plates = []
        for index, path in enumerate(paths, start=1):
            if path.stem.startswith("ink("):
                label = path.stem.removeprefix("ink(").removesuffix(")")
            else:
                label = path.stem.removeprefix("ink.")
                if label.startswith("s") and label[1:].isdigit():
                    number = int(label[1:])
                    if number >= len(spot_names):
                        raise RuntimeError(f"Ghostscript did not identify spot plate {label}")
                    label = spot_names[number]
            with path.open("rb") as source, Image.open(source) as plate:
                grayscale = plate.convert("L")
                ink_bounds = ImageOps.invert(grayscale).getbbox()
                file_name = f"plate-{index:02d}.png"
                grayscale.save(ready / file_name)
            plates.append({"name": label, "blank": ink_bounds is None, "ink_bounds_px": ink_bounds, "image": file_name})
        if not plates:
            raise RuntimeError("Ghostscript produced no separation TIFFs")

        manifest = {
            "proof_source": "Ghostscript tiffsep rendering of PDF (not printer/RIP output)",
            "input": str(pdf), "pdf_page": page, "dpi": dpi, "size_px": [width, height],
            "composite": "composite.png", "plates": plates,
        }
        (ready / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        ready.rename(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path, help="New directory for the composite, plate images, and manifest")
    parser.add_argument("--ghostscript", required=True, type=Path, help="Path to the Ghostscript console executable")
    parser.add_argument("--dpi", default=150, type=int)
    parser.add_argument("--page", default=1, type=int)
    args = parser.parse_args()
    manifest = render(args.pdf, args.output, args.ghostscript, args.dpi, args.page)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
