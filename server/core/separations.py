"""Ghostscript separation rendering and plate image measurements (no COM).

Ghostscript's ``tiffsep`` device writes one 8-bit plate per process ink and per spot in the PDF, plus a CMYK
composite built from those plates (so overprints are simulated). ``-dOverrideICC`` keeps process colors as
written (100 K stays K-only instead of becoming rich black through ICC conversion).
"""

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

PROCESS = ("Cyan", "Magenta", "Yellow", "Black")
_TIMEOUT = 120


def find_ghostscript() -> Path:
    """The Ghostscript console executable: GHOSTSCRIPT_EXE, then PATH, then the newest C:\\Program Files\\gs."""
    configured = os.environ.get("GHOSTSCRIPT_EXE")
    if configured:
        if Path(configured).is_file():
            return Path(configured)
        raise FileNotFoundError(f"GHOSTSCRIPT_EXE points to a missing file: {configured}")
    for name in ("gswin64c", "gs"):
        found = shutil.which(name)
        if found:
            return Path(found)
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files")]
    candidates = [path for root in roots for path in glob.glob(os.path.join(root, "gs", "*", "bin", "gswin64c.exe"))]
    if candidates:
        def version(path):
            return tuple(int(part) for part in re.findall(r"\d+", Path(path).parent.parent.name))
        return Path(max(candidates, key=version))
    raise FileNotFoundError("Ghostscript (gswin64c.exe) was not found. Install Ghostscript, or set GHOSTSCRIPT_EXE "
                            "to its console executable")


def render_tiffsep(pdf: Path, work: Path, ghostscript: Path, dpi: int):
    """Render page 1 of ``pdf``. Returns (composite RGB image, [(plate name, ink array uint8 0..255)])."""
    composite_tiff = work / "ink.tif"
    result = subprocess.run(
        [str(ghostscript), "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dPDFSTOPONERROR", "-dOverrideICC", "-sDEVICE=tiffsep",
         f"-r{dpi}", "-dFirstPage=1", "-dLastPage=1", f"-sOutputFile={composite_tiff}", "-f", str(pdf)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=_TIMEOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if result.returncode or not composite_tiff.is_file():
        raise RuntimeError(f"Ghostscript did not render the proof: {result.stdout[-1500:]}")
    with Image.open(composite_tiff) as image:
        composite = image.convert("RGB")
    spot_names = [line.removeprefix("%%SeparationName: ") for line in result.stdout.splitlines()
                  if line.startswith("%%SeparationName: ")]
    plates = []
    for path in sorted([*work.glob("ink(*).tif"), *work.glob("ink.*.tif")]):
        if path.stem.startswith("ink("):
            label = path.stem.removeprefix("ink(").removesuffix(")")
        else:
            label = path.stem.removeprefix("ink.")
            if label.startswith("s") and label[1:].isdigit():
                number = int(label[1:])
                if number >= len(spot_names):
                    raise RuntimeError(f"Ghostscript did not identify spot plate {label}")
                label = spot_names[number]
        with Image.open(path) as image:
            ink = 255 - np.asarray(image.convert("L"), dtype=np.uint8)
        plates.append((label, ink))
    if not plates:
        raise RuntimeError("Ghostscript produced no separation plates")
    order = {name: index for index, name in enumerate(PROCESS)}
    plates.sort(key=lambda plate: (plate[0] in order, order.get(plate[0], 0), plate[0]))
    return composite, plates


def save_plate(ink: np.ndarray, path: Path, dpi: int | None = None) -> None:
    """Save a plate as a film: black where ink is. With dpi, the PNG records its resolution (pHYs), so whatever
    reads or prints it knows its real size."""
    Image.fromarray(255 - ink).save(path, compress_level=1, **({"dpi": (dpi, dpi)} if dpi else {}))


def bbox(mask: np.ndarray):
    """(x0, y0, x1, y1) of True pixels, exclusive end, or None."""
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        return None
    columns = np.flatnonzero(mask.any(axis=0))
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


def integral(mask: np.ndarray) -> np.ndarray:
    """Summed-area table with a zero first row and column: counts of True pixels in any rectangle in O(1)."""
    table = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int64)
    table[1:, 1:] = mask.cumsum(axis=0, dtype=np.int64).cumsum(axis=1)
    return table


def count_in(table: np.ndarray, rect) -> int:
    x0, y0, x1, y1 = rect
    return int(table[y1, x1] - table[y0, x1] - table[y1, x0] + table[y0, x0])


def _shifted(mask, dx, dy):
    """mask moved by (dx, dy); pixels shifted in from outside are False."""
    out = np.zeros_like(mask)
    height, width = mask.shape
    out[max(dy, 0):height + min(dy, 0), max(dx, 0):width + min(dx, 0)] = \
        mask[max(-dy, 0):height + min(-dy, 0), max(-dx, 0):width + min(-dx, 0)]
    return out


def inset(mask: np.ndarray, radius: float) -> np.ndarray:
    """Erode by a disk of ``radius`` pixels (a negative radius dilates). Outside the image counts as empty."""
    reach = int(abs(radius))
    if reach < 1:
        return mask.copy()
    offsets = [(dx, dy) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1)
               if dx * dx + dy * dy <= radius * radius]
    out = mask.copy()
    for dx, dy in offsets:
        if radius > 0:
            out &= _shifted(mask, dx, dy)
        else:
            out |= _shifted(mask, dx, dy)
    return out


def components(mask: np.ndarray, limit: int = 5, max_runs: int = 200_000):
    """8-connected components as [(pixels, (x0, y0, x1, y1))], largest first, at most ``limit``.

    Labels runs of True pixels row by row with union-find. Returns None when the mask has too many runs.
    """
    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    edges = np.diff(padded, axis=1)
    rows, starts = np.nonzero(edges == 1)
    _, ends = np.nonzero(edges == -1)  # row-major order, so ends pair with starts
    if rows.size > max_runs:
        return None
    runs = list(zip(rows.tolist(), starts.tolist(), ends.tolist(), strict=True))
    parent = list(range(len(runs)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    previous, current, row = [], [], -1
    for index, (y, x0, x1) in enumerate(runs):
        if y != row:
            previous = current if y == row + 1 else []
            current, row, first = [], y, 0
        # Runs are sorted by x within a row: skip previous-row runs that end left of this one.
        while first < len(previous) and runs[previous[first]][2] < x0:
            first += 1
        other = first
        while other < len(previous) and runs[previous[other]][1] <= x1:
            a, b = find(index), find(previous[other])
            if a != b:
                parent[a] = b
            other += 1
        current.append(index)
    groups = {}
    for index, (y, x0, x1) in enumerate(runs):
        root = find(index)
        size, (gx0, gy0, gx1, gy1) = groups.get(root, (0, (x0, y, x1, y + 1)))
        groups[root] = (size + x1 - x0, (min(gx0, x0), min(gy0, y), max(gx1, x1), max(gy1, y + 1)))
    return sorted(groups.values(), key=lambda group: -group[0])[:limit]
