"""Find the spot colors of an installed palette that are closest to a given color, deterministically.

``nearest_spot_colors`` lets an agent turn process-coloured art into a Pantone (or another spot) by measurement
instead of guessing: the candidates are ranked by CIEDE2000 against the Lab values stored in the palette XML.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from core import colorspec
from core.connection import get_connection
from core.models import ToolResult
from core.results import failure
from core.targets import resolve_document, resolve_page

DEFAULT_PALETTE = "PANTONE+ Solid Coated"
_MAX_COUNT = 50


# --- CIEDE2000 and Lab helpers (pure) -----------------------------------------------------------------------

def ciede2000(lab1, lab2) -> float:
    """CIEDE2000 colour difference between two (L, a, b) tuples (Sharma, Wu and Dalal's formulation)."""
    lab_l1, a1, b1 = lab1
    lab_l2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_mean7 = ((c1 + c2) / 2) ** 7
    g = 0.5 * (1 - math.sqrt(c_mean7 / (c_mean7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dl, dc = lab_l2 - lab_l1, c2p - c1p
    if not c1p * c2p:
        dh = 0.0
    elif abs(h2p - h1p) <= 180:
        dh = h2p - h1p
    else:
        dh = h2p - h1p + (360 if h2p <= h1p else -360)
    dh_term = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    l_mean, c_mean = (lab_l1 + lab_l2) / 2, (c1p + c2p) / 2
    if not c1p * c2p:
        h_mean = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        h_mean = (h1p + h2p) / 2
    else:
        h_mean = (h1p + h2p + (360 if h1p + h2p < 360 else -360)) / 2
    t = (1 - 0.17 * math.cos(math.radians(h_mean - 30)) + 0.24 * math.cos(math.radians(2 * h_mean))
         + 0.32 * math.cos(math.radians(3 * h_mean + 6)) - 0.20 * math.cos(math.radians(4 * h_mean - 63)))
    sl = 1 + 0.015 * (l_mean - 50) ** 2 / math.sqrt(20 + (l_mean - 50) ** 2)
    sc, sh = 1 + 0.045 * c_mean, 1 + 0.015 * c_mean * t
    rc = 2 * math.sqrt(c_mean ** 7 / (c_mean ** 7 + 25 ** 7))
    rt = -math.sin(math.radians(60 * math.exp(-(((h_mean - 275) / 25) ** 2)))) * rc
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dh_term / sh) ** 2 + rt * (dc / sc) * (dh_term / sh))


def lab_to_hex(lab) -> str:
    """sRGB "#RRGGBB" of a D50 Lab value (Bradford-adapted to D65, clipped to gamut)."""
    lightness, a, b = lab
    fy = (lightness + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def inverse(value):
        return value ** 3 if value ** 3 > 0.008856 else (value - 16 / 116) / 7.787

    x, y, z = 0.96422 * inverse(fx), inverse(fy), 0.82521 * inverse(fz)
    linear = (3.1338561 * x - 1.6168667 * y - 0.4906146 * z,
              -0.9787684 * x + 1.9161415 * y + 0.0334540 * z,
              0.0719453 * x - 0.2289914 * y + 1.4052427 * z)
    parts = []
    for channel in linear:
        channel = min(1.0, max(0.0, channel))
        channel = 12.92 * channel if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055
        parts.append(round(channel * 255))
    return "#{:02X}{:02X}{:02X}".format(*parts)


def approximate_lab(rgb) -> tuple:
    """Fallback only: Lab (D50) of an sRGB triple (0-255), ignoring any ICC profile."""
    linear = [(c / 255 / 12.92) if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    r, g, b = linear
    x = 0.4360747 * r + 0.3850649 * g + 0.1430804 * b
    y = 0.2225045 * r + 0.7168786 * g + 0.0606169 * b
    z = 0.0139322 * r + 0.0971045 * g + 0.7141733 * b
    f = [v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116 for v in (x / 0.96422, y, z / 0.82521)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def approximate_cmyk_to_rgb(c, m, y, k) -> tuple:
    """Fallback only: naive CMYK (0-100) to sRGB, no profile."""
    return tuple(round(255 * (1 - v / 100) * (1 - k / 100)) for v in (c, m, y))


# --- palette reading --------------------------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _read_palette(path: str, mtime: float) -> tuple:
    """((name, (L, a, b) D50, "#RRGGBB"), ...) of a palette XML (Lab is stored as L/100, (a+128)/255, (b+128)/255)."""
    root = ET.parse(path).getroot()
    colors = []
    for node in root.iter("cs"):
        values = {c.get("cs", "").upper(): [float(v) for v in c.get("tints", "").split(",") if v]
                  for c in node.findall("color")}
        lab = None
        if len(values.get("LAB", ())) == 3:
            lightness, a, b = values["LAB"]
            lab = (lightness * 100, a * 255 - 128, b * 255 - 128)
        rgb = values.get("RGB")
        if len(rgb or ()) == 3:
            hex_value = "#{:02X}{:02X}{:02X}".format(*(round(v * 255) for v in rgb))
        else:
            hex_value = lab_to_hex(lab) if lab else None
        if lab is None and hex_value:
            lab = approximate_lab(tuple(int(hex_value[i:i + 2], 16) for i in (1, 3, 5)))
        if lab and node.get("name"):
            colors.append((node.get("name"), lab, hex_value))
    return tuple(colors)


def _palette_key(name: str) -> str:
    """Palette name for matching: case, spaces, punctuation and the registered sign ignored, "+" kept (PANTONE+
    is a different library from the older PANTONE)."""
    return re.sub(r"[^0-9a-z]", "", name.lower().replace("+", "plus").replace("®", "").replace("(r)", ""))


def find_palette(index: dict, wanted: str | None) -> tuple:
    """(name, guid, path, other matches) of the palette matching ``wanted`` by normalized name. Exact match first,
    else the first palette (colorspec's preference order) whose name contains it."""
    key = _palette_key(wanted or DEFAULT_PALETTE)
    palettes = index["palettes"]
    exact = [p for p in palettes if _palette_key(p[0]) == key]
    matches = exact or [p for p in palettes if key in _palette_key(p[0])]
    if not matches:
        raise ValueError(f"No installed spot palette matches {wanted or DEFAULT_PALETTE!r}; installed: "
                         + ", ".join(sorted({p[0] for p in palettes})[:40]))
    name, guid, path = matches[0]
    return name, guid, path, [p[0] for p in matches[1:]]


def rank(target_lab, colors, count) -> list:
    """The ``count`` palette colors nearest to ``target_lab`` by CIEDE2000 (ties broken by name)."""
    scored = sorted(((ciede2000(target_lab, lab), name, lab, hex_value) for name, lab, hex_value in colors),
                    key=lambda row: (row[0], row[1]))
    return [{"name": name, "lab": [round(v, 2) for v in lab], "hex": hex_value, "delta_e": round(delta, 2)}
            for delta, name, lab, hex_value in scored[:count]]


# --- the tool --------------------------------------------------------------------------------------------------------

def _query_lab(color) -> tuple:
    """Lab of a COM color by CorelDRAW's own conversion (the application's color management)."""
    probe = color.GetCopy()
    probe.ConvertToLab()
    # CorelDRAW reports the lightness on a 0-255 scale (white is 254-255); a and b are the usual scale, rounded to whole numbers.
    return probe.LabLuminance * 100 / 255, probe.LabComponentA, probe.LabComponentB


def _fallback_lab(spec):
    if spec.kind == "cmyk":
        return approximate_lab(approximate_cmyk_to_rgb(*spec.components))
    if spec.kind == "rgb":
        return approximate_lab(tuple(spec.components))
    raise RuntimeError("CorelDRAW could not convert this color to Lab")


def nearest_spot_colors(
    color: str | dict,
    palette: str = DEFAULT_PALETTE,
    count: int = 3,
    document_path: str = "",
    page_index: int = 1,
) -> ToolResult:
    """Rank the spot colors of an installed palette by CIEDE2000 distance to a color. Read-only, deterministic.

    Use it to pick the Pantone for process-coloured art instead of guessing, then apply it with set_fill or
    set_outline.

    - color: "CMYK 76/10/8/0", "RGB 20/120/200", "#1478C8", a spot name, or {"from_shape_id": "121",
      "part": "fill" | "outline"} (the uniform fill or outline of that shape; needs document_path and page_index).
    - palette: installed spot palette by name, matched ignoring case, spaces and punctuation; default
      "PANTONE+ Solid Coated" (CorelDRAW's PANTONE+ Solid Coated-V5 library). The result names the file and any
      other palette that also matched.
    - count: candidates to return (1-50, default 3).
    - document_path / page_index: only for from_shape_id (a spot name uses the active document).

    Returns query (identity, Lab, hex, lab_source), palette and candidates sorted by delta_e (CIEDE2000) with name,
    palette, lab [L, a, b], hex and delta_e. Candidate Lab comes from the palette XML. The query's Lab is computed
    by CorelDRAW's own conversion (Color.ConvertToLab, application color management); if that fails the tool uses
    a profile-less sRGB approximation and says so in lab_source. delta_e under about 2 is hard to tell apart;
    above about 5 is a clearly different color.
    """
    if not isinstance(count, int) or not 1 <= count <= _MAX_COUNT:
        return ToolResult.fail(f"count must be an integer between 1 and {_MAX_COUNT}")
    try:
        spec = colorspec.parse(color)
    except colorspec.ColorSpecError as exc:
        return ToolResult.fail(str(exc))
    if spec.kind in ("none", "registration"):
        return ToolResult.fail(f"A {spec.kind} color has no Lab value to match")
    if spec.kind == "donor" and not document_path:
        return ToolResult.fail("from_shape_id needs document_path and page_index")
    conn = get_connection()

    def run():
        app = conn.app
        palette_name, guid, path, others = find_palette(colorspec.spot_index(app), palette)
        colors = _read_palette(path, Path(path).stat().st_mtime)
        doc = actual_path = None
        if document_path:
            doc, actual_path = resolve_document(app, document_path)
        elif spec.kind == "spot":
            doc = app.ActiveDocument
            if doc is None:
                raise ValueError("A spot color name needs an open document (pass document_path)")
        if spec.kind == "donor":
            from tools.colors import _resolve_color

            resolved = _resolve_color(app, doc, resolve_page(doc, page_index), spec)
        else:
            resolved = colorspec.resolve(app, doc, spec)
        if resolved.none:
            raise ValueError("The donor has no color (its fill or outline is none)")
        try:
            lab, source = _query_lab(resolved.color), "CorelDRAW Color.ConvertToLab (application color management)"
        except Exception:
            lab = _fallback_lab(spec)
            source = "approximation: naive CMYK to sRGB, then sRGB to Lab D50, no ICC profile (CorelDRAW failed)"
        found = rank(lab, colors, count)
        for row in found:
            row["palette"] = palette_name
        return {"document_path": actual_path,
                "query": {"identity": resolved.identity, "lab": [round(v, 2) for v in lab], "hex": lab_to_hex(lab),
                          "lab_source": source},
                "palette": {"name": palette_name, "guid": guid, "file": path, "other_matches": others,
                            "color_count": len(colors)},
                "candidates": found}

    result = conn.read_call(run)
    if not result["success"]:
        return failure(result, document_path=document_path or None)
    return ToolResult.ok(**result["result"])
