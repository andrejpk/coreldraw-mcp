"""Ink inventory: which inks each page uses, where they come from, and which separation plates they imply."""

import math
import re
from collections import Counter, defaultdict

from core.connection import get_connection
from core.models import ToolResult
from core.structure import CONTAINER_TYPES, StaleStructureError, page_layers, read_entries
from core.targets import resolve_document, resolve_page
from core.units import convert, unit_name
from tools.inspection import effect_controls

PROCESS_PLATES = ("Cyan", "Magenta", "Yellow", "Black")
_TEXT, _BITMAP = 6, 5
_NO_FILL, _UNIFORM, _FOUNTAIN, _PATTERN = 0, 1, 2, 7
_FILL_NAMES = {2: "fountain", 7: "pattern", 9: "texture", 10: "PostScript", 11: "hatch"}
# cdrColorType codes
_PANTONE, _CMYK, _CMY, _RGB, _GRAY, _REGISTRATION = 1, 2, 3, 5, 9, 20
_MODEL_NAMES = {3: "CMY", 4: "CMYK255", 6: "HSB", 7: "HLS", 8: "BW", 9: "Gray", 11: "YIQ", 12: "Lab", 21: "user ink"}
# cdrImageType codes
_BITMAP_MODES = {0: "black and white", 1: "16 colors", 2: "grayscale", 3: "paletted", 4: "RGB", 5: "CMYK",
                 6: "duotone", 7: "Lab", 8: "CMYK multichannel", 9: "RGB multichannel", 10: "spot multichannel"}
# Object states, most decisive first. Only "prints" and "hidden_printable" produce plates.
_STATES = ("prints", "hidden_printable", "hidden_object", "off_page", "clipped", "non_printable_layer")
_MARKING = ("prints", "hidden_printable")
_TEXT_CHUNK = 200  # characters per CQL array; each character costs about 5 ms inside CorelDRAW
_MAX_MIXED_CHARACTERS = 4000  # per text shape; longer mixed-color texts are reported as unread
_BATCH = 2000  # entries per read; the time budget is checked between reads
_RESERVE_SECONDS = 15.0
TINY_MM = 0.1  # find_shapes preset tiny uses the same default
TINY_CLUSTER = 20  # tiny objects under one parent that are fine detail rather than stray specks
_SIMILAR_DELTA_E = 12.0
_ZERO_MM = 0.001  # CorelDRAW reports a zero-length line as about 0.0004 mm


class _OutOfTimeError(Exception):
    pass


# --- CQL ----------------------------------------------------------------------------------------------------

def _color(c):
    """CQL reading one color: type, then spot (name, tint, palette GUID, sRGB hex at that tint) or components."""
    spot = f"array({c}.Name, {c}.Tint, {c}.PaletteIdentifier, {c}.HexValue)"
    return (f"array({c}.Type, iif({c}.Type = {_REGISTRATION}, 0, iif({c}.Type = {_PANTONE} or {c}.IsSpot, {spot}, "
            f"iif({c}.Type = {_CMYK}, array({c}.CMYKCyan, {c}.CMYKMagenta, {c}.CMYKYellow, {c}.CMYKBlack), "
            f"iif({c}.Type = {_RGB}, array({c}.RGBRed, {c}.RGBGreen, {c}.RGBBlue), "
            f"iif({c}.Type = {_CMY}, array({c}.CMYCyan, {c}.CMYMagenta, {c}.CMYYellow), "
            f"iif({c}.Type = {_GRAY}, {c}.Gray, 0)))))), {c}.IsSpot)")


_BOUNDS = "array($item.LeftX, $item.BottomY, $item.RightX, $item.TopY)"
_PLACE = [_BOUNDS, "$item.Visible", "$item.Transparency.Type"]
_PAINT = [
    f"array($item.Fill.Type, iif($item.Fill.Type = {_UNIFORM}, {_color('$item.Fill.UniformColor')}, "
    f"iif($item.Fill.Type = {_FOUNTAIN}, $item.Fill.Fountain.Colors.Count, 0)))",
    f"array($item.Outline.Type, iif($item.Outline.Type = 0, 0, array($item.Outline.Width, "
    f"{_color('$item.Outline.Color')})))",
]
_STORY = [
    "$item.Text.Story.Characters.Count", "$item.Text.Story.Text", "$item.Text.Story.Fill.Type",
    f"iif($item.Text.Story.Fill.Type = {_UNIFORM}, {_color('$item.Text.Story.Fill.UniformColor')}, 0)",
]
_STORY_OUTLINE = (f"array($item.Text.Story.Outline.Type, iif($item.Text.Story.Outline.Type = 0, 0, "
                  f"array($item.Text.Story.Outline.Width, {_color('$item.Text.Story.Outline.Color')})))")
_CHARACTER = (f"array($item.Text, $item.Fill.Type, iif($item.Fill.Type = {_UNIFORM}, "
              f"{_color('$item.Fill.UniformColor')}, 0), $item.Outline.Type, iif($item.Outline.Type = 0, 0, "
              f"array($item.Outline.Width, {_color('$item.Outline.Color')})))")


def _safe_read(view, entries, expressions):
    """read_entries, isolating entries whose properties CorelDRAW cannot read (None for those)."""
    if not entries:
        return []
    try:
        return read_entries(view, entries, expressions)
    except StaleStructureError:
        raise
    except Exception:
        if len(entries) == 1:
            return [None]
        middle = len(entries) // 2
        return _safe_read(view, entries[:middle], expressions) + _safe_read(view, entries[middle:], expressions)


# --- colors -------------------------------------------------------------------------------------------------

def _rgb_plates(red, green, blue):
    """Naive RGB -> CMYK separation (for an estimate only; the output path's color management decides)."""
    r, g, b = red / 255, green / 255, blue / 255
    k = 1 - max(r, g, b)
    if k >= 1:
        return ["Black"]
    c, m, y = ((1 - value - k) / (1 - k) for value in (r, g, b))
    return [plate for plate, value in zip(PROCESS_PLATES, (c, m, y, k), strict=True) if value > 0.005]


def _ink(value):
    """Decode a queried color into (key, description). The description has ink, kind, plates and estimated."""
    if value is None:
        return ("unknown",), {"ink": "Unreadable color", "kind": "unknown", "plates": [], "estimated": True}
    color_type, details, is_spot = value
    if color_type == _REGISTRATION:
        return ("registration",), {"ink": "Registration", "kind": "registration", "plates": ["(all)"],
                                   "estimated": False}
    if color_type == _PANTONE or is_spot:
        name, tint, palette, appearance = details
        return ("spot", name), {"ink": name, "kind": "spot", "plates": [name], "estimated": False,
                                "tint": tint, "palette": palette, "appearance": (tint, appearance)}
    if color_type == _CMYK:
        components = [int(round(part)) for part in details]
        plates = [plate for plate, part in zip(PROCESS_PLATES, components, strict=True) if part > 0]
        return ("cmyk", *components), {"ink": "CMYK " + "/".join(map(str, components)), "kind": "process",
                                       "model": "CMYK", "components": components, "plates": plates,
                                       "estimated": False}
    if color_type == _RGB:
        components = [int(part) for part in details]
        return ("rgb", *components), {"ink": "RGB " + "/".join(map(str, components)), "kind": "rgb", "model": "RGB",
                                      "components": components, "plates": _rgb_plates(*components),
                                      "estimated": True}
    if color_type == _CMY:
        components = [int(round(part)) for part in details]
        plates = [plate for plate, part in zip(PROCESS_PLATES, components, strict=False) if part > 0]
        return ("cmy", *components), {"ink": "CMY " + "/".join(map(str, components)), "kind": "other", "model": "CMY",
                                      "components": components, "plates": plates, "estimated": True}
    if color_type == _GRAY:
        level = int(details)
        return ("gray", level), {"ink": f"Gray {level}", "kind": "other", "model": "Gray", "components": [level],
                                 "plates": ["Black"] if level < 255 else [], "estimated": True}
    model = _MODEL_NAMES.get(color_type, f"type {color_type}")
    return ("other", color_type), {"ink": f"{model} color", "kind": "other", "model": model,
                                   "plates": list(PROCESS_PLATES), "estimated": True}


def _fill_inks(fill, stops=None, pattern=None):
    """[(part, color value or None, fill type)] for a queried fill."""
    if fill is None:
        return [("fill", None, None)]
    fill_type, details = fill
    if fill_type == _NO_FILL:
        return []
    if fill_type == _UNIFORM:
        return [("fill", details, fill_type)]
    if fill_type == _FOUNTAIN and stops is not None:
        return [("fountain_stop", stop, fill_type) for stop in stops]
    if fill_type == _PATTERN and pattern is not None:
        return [("pattern", color, fill_type) for color in pattern]
    return [("fill_unknown", None, fill_type)]


# --- page reading -------------------------------------------------------------------------------------------

class _Page:
    """Accumulates the inks of one page."""

    def __init__(self, page, unit, max_ids, collect=False):
        self.max_ids = max_ids
        # Per-object records for plate attribution (render_plates): only when asked for.
        self.objects = [] if collect else None
        self.inks = {}
        self.layer_rows = []
        self.bitmaps = []
        self.warnings = defaultdict(lambda: {"count": 0, "ids": []})
        self.registration = {"prints": False, "hidden_printable": False}
        self.unit = unit
        self.bounds = (page.LeftX, page.BottomY, page.RightX, page.TopY)
        known = unit in ("in", "mm", "cm", "pt")
        self.speck = convert(TINY_MM, "mm", unit) if known else None
        self.tiny = []  # (shape ID, parent ID)
        self.zero = convert(_ZERO_MM, "mm", unit) if known else 0.0
        self.tint_inks = defaultdict(set)

    def warn(self, code, shape_id):
        entry = self.warnings[code]
        entry["count"] += 1
        if len(entry["ids"]) < self.max_ids and shape_id not in entry["ids"]:
            entry["ids"].append(shape_id)

    def add(self, key, description, part, state, shape):
        """One use of an ink by one part of one object."""
        ink = self.inks.get(key)
        if ink is None:
            ink = self.inks[key] = {**{k: v for k, v in description.items()
                                       if k not in ("tint", "palette", "appearance")},
                                    "tints": set(), "palettes": set(), "appearance": None,
                                    "objects": {s: set() for s in _STATES}, "parts": Counter(),
                                    "layers": Counter(), "flags": Counter(), "ids": {},
                                    "marking": {state: set() for state in _MARKING}}
        if description.get("tint") is not None:
            ink["tints"].add(description["tint"])
            ink["palettes"].add(description["palette"])
            if ink["appearance"] is None or description["appearance"][0] > ink["appearance"][0]:
                ink["appearance"] = description["appearance"]
        shape_id = shape["id"]
        new_object = all(shape_id not in ids for ids in ink["objects"].values())
        ink["objects"][state].add(shape_id)
        if state in _MARKING:
            ink["parts"][part] += 1
            if part != "text_whitespace":
                ink["marking"][state].add(shape_id)
        if new_object:
            ink["layers"][shape["layer"]] += 1
            for flag in ("in_powerclip", "effect_generated", "transparent", "locked_layer", "master_layer"):
                if shape.get(flag):
                    ink["flags"][flag] += 1
        record = ink["ids"].setdefault(shape_id, {"id": shape_id, "layer": shape["layer"], "parts": [],
                                                  "state": state, **shape["link"]})
        if part not in record["parts"]:
            record["parts"].append(part)

    def plates(self, states):
        """Plates marked by objects in the given states: (marking plates, estimated-only plates)."""
        exact, estimated = set(), set()
        for ink in self.inks.values():
            if ink["kind"] != "registration" and any(ink["marking"][state] for state in states):
                (estimated if ink["estimated"] else exact).update(ink["plates"])
        return exact, estimated - exact


def _layer_row(view, entries):
    return {**view.details(), "objects": sum(1 for entry in entries if entry.type not in CONTAINER_TYPES),
            "hidden_printable": bool(view.printable and not view.visible)}


def _intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _read_layer(conn, page_data, view, include_ids):
    entries = view.structure.entries
    page_data.layer_rows.append(_layer_row(view, entries))
    if not entries:
        return
    layer = view.name
    controls = (effect_controls(view)
                if include_ids and any(entry.effect is not None for entry in entries) else {})
    places, paints = {}, {}
    ordinary = [entry for entry in entries if entry.type not in CONTAINER_TYPES]
    for start in range(0, len(entries), _BATCH):
        if conn.time_left() < _RESERVE_SECONDS:
            raise _OutOfTimeError
        batch = entries[start:start + _BATCH]
        for entry, row in zip(batch, _safe_read(view, batch, _PLACE), strict=True):
            places[entry.shape_id] = row
        painted = [entry for entry in batch if entry.type not in CONTAINER_TYPES and entry.type != _TEXT]
        for entry, row in zip(painted, _safe_read(view, painted, _PAINT), strict=True):
            paints[entry.shape_id] = row

    # Extra reads for the few objects that need them.
    fountains = [entry for entry in ordinary if (paints.get(entry.shape_id) or [None])[0]
                 and paints[entry.shape_id][0][0] == _FOUNTAIN and paints[entry.shape_id][0][1]]
    stops = {}
    for entry in fountains:
        count = paints[entry.shape_id][0][1]
        items = ", ".join(f"$item.Fill.Fountain.Colors.Item({index}).Color" for index in range(count))
        row = _safe_read(view, [entry], [f"array({items}).convert({_color('$item')})"])[0]
        stops[entry.shape_id] = list(row[0]) if row else None
    patterns = {}
    pattern_entries = [entry for entry in ordinary if (paints.get(entry.shape_id) or [None])[0]
                       and paints[entry.shape_id][0][0] == _PATTERN]
    for entry, row in zip(pattern_entries, _safe_read(view, pattern_entries, [
            f"array($item.Fill.Pattern.FrontColor, $item.Fill.Pattern.BackColor).convert({_color('$item')})"]),
            strict=True):
        patterns[entry.shape_id] = list(row[0]) if row else None
    bitmaps = [entry for entry in ordinary if entry.type == _BITMAP]
    modes = dict(zip((entry.shape_id for entry in bitmaps),
                     (row[0] if row else None for row in _safe_read(view, bitmaps, ["$item.Bitmap.Mode"])),
                     strict=True))
    texts = [entry for entry in ordinary if entry.type == _TEXT]
    stories = dict(zip((entry.shape_id for entry in texts), _safe_read(view, texts, _STORY), strict=True))
    # A story whose characters have different outlines cannot report one outline: None means "read the runs".
    outlines = dict(zip((entry.shape_id for entry in texts),
                        (row[0] if row else None for row in _safe_read(view, texts, [_STORY_OUTLINE])), strict=True))

    # Visibility, clipping and page placement, inherited down the structure.
    hidden = {}
    for entry in entries:
        place = places.get(entry.shape_id)
        own_hidden = place is not None and not place[1]
        hidden[entry.shape_id] = own_hidden or hidden.get(entry.parent_id, False)

    for entry in ordinary:
        place = places.get(entry.shape_id)
        bounds = tuple(place[0]) if place else None
        if not view.printable:
            state = "non_printable_layer"
        elif hidden[entry.shape_id]:
            state = "hidden_object"
        elif bounds and not _intersects(bounds, page_data.bounds):
            state = "off_page"
        elif bounds and entry.clip is not None and places.get(entry.clip) \
                and not _intersects(bounds, tuple(places[entry.clip][0])):
            state = "clipped"
        elif not view.visible:
            state = "hidden_printable"
        else:
            state = "prints"
        link = {}
        if entry.clip is not None:
            link["powerclip_container_id"] = entry.clip
        if entry.effect is not None:
            link["effect_group_id"] = entry.effect
            if entry.effect in controls:
                link["effect_control_id"] = controls[entry.effect]
        shape = {"id": entry.shape_id, "layer": layer, "link": link, "in_powerclip": entry.clip is not None,
                 "effect_generated": entry.effect is not None, "locked_layer": not view.editable,
                 "master_layer": bool(view.master), "transparent": bool(place and place[2])}
        uses = _uses(entry, paints, stops, patterns, stories, outlines, view, page_data, state)
        if entry.type == _BITMAP:
            mode = modes.get(entry.shape_id)
            name = _BITMAP_MODES.get(mode, "unknown")
            page_data.bitmaps.append({"id": entry.shape_id, "layer": layer, "mode": name, "state": state, **link})
            plates = {"black and white": ["Black"], "grayscale": ["Black"]}.get(name, list(PROCESS_PLATES))
            uses.append(("bitmap", ("bitmap", name), {"ink": f"Bitmap ({name})", "kind": "bitmap",
                                                      "plates": plates, "estimated": True}))
            if state in _MARKING and name not in ("black and white", "grayscale", "duotone", "spot multichannel"):
                page_data.warn("bitmap_process", entry.shape_id)
        if state == "hidden_printable" and any(description["plates"] for _, _, description in uses):
            page_data.warn("hidden_printable_ink", entry.shape_id)
        if page_data.objects is not None and uses:
            outline = outlines.get(entry.shape_id) if entry.type == _TEXT else (paints.get(entry.shape_id) or (0, 0))[1]
            page_data.objects.append({
                "id": entry.shape_id, "layer": layer, "master": bool(view.master), "state": state, "bounds": bounds,
                "outline_width": outline[1][0] if outline and outline[0] else 0.0,
                "uses": [(part, description["ink"], description["kind"], tuple(description["plates"]))
                         for part, _, description in uses]})
        for part, key, description in uses:
            page_data.add(key, description, part, state, shape)
            if state in _MARKING and part != "text_whitespace" and description["kind"] == "registration":
                page_data.registration[state] = True
        _artifact_warnings(page_data, entry, bounds, paints.get(entry.shape_id), uses, shape, state)


def _uses(entry, paints, stops, patterns, stories, outlines, view, page_data, state):
    """[(part, key, description)] for every ink one object uses."""
    uses = []
    if entry.type == _TEXT:
        for part, value in _text_parts(entry, stories, outlines, view, page_data):
            key, description = _ink(value)
            uses.append((part, key, description))
        return uses
    paint = paints.get(entry.shape_id)
    if paint is None:
        if entry.type != _BITMAP:
            uses.append(("unreadable", ("unknown",), _ink(None)[1]))
            page_data.warn("unreadable", entry.shape_id)
        return uses
    fill, outline = paint
    for part, value, fill_type in _fill_inks(fill, stops.get(entry.shape_id), patterns.get(entry.shape_id)):
        if part == "fill_unknown":
            name = _FILL_NAMES.get(fill_type, f"type {fill_type}")
            uses.append(("fill", ("unknown_fill", fill_type), {"ink": f"{name} fill (colors not read)",
                                                              "kind": "unknown", "plates": [], "estimated": True}))
            page_data.warn("unknown_fill_colors", entry.shape_id)
        else:
            key, description = _ink(value)
            uses.append((part, key, description))
    if fill and fill[0] == _FOUNTAIN:
        kinds = {key for part, key, _ in uses if part == "fountain_stop"}
        if len({key[:2] if key[0] == "spot" else key[:1] for key in kinds}) > 1 and state in _MARKING:
            page_data.warn("fountain_mixes_inks", entry.shape_id)
    outline_type, details = outline
    if outline_type:
        width, color = details
        key, description = _ink(color)
        uses.append(("outline" if width > 0 else "outline_zero_width", key, description))
    return uses


def _text_parts(entry, stories, outlines, view, page_data):
    """[(part, color value)] for a text shape: one use per run of characters with one fill (and outline)."""
    story = stories.get(entry.shape_id)
    outline = outlines.get(entry.shape_id)
    if story is None:
        page_data.warn("unreadable", entry.shape_id)
        return [("text_fill", None)]
    count, text, fill_type, fill_color = story
    if fill_type == _UNIFORM and outline is not None:
        whitespace = not (text or "").strip()
        parts = [("text_whitespace" if whitespace else "text_fill", fill_color)]
        if outline[0]:
            width, color = outline[1]
            parts.append(("text_whitespace" if whitespace else "text_outline" if width > 0
                          else "outline_zero_width", color))
        return parts
    if count > _MAX_MIXED_CHARACTERS:
        page_data.warn("text_runs_not_read", entry.shape_id)
        return [("text_fill", None)]
    characters = []
    expressions = []
    for start in range(1, count + 1, _TEXT_CHUNK):
        items = ", ".join(f"$item.Characters.Item({index})"
                          for index in range(start, min(count, start + _TEXT_CHUNK - 1) + 1))
        expressions.append(f"array($item.Text.Story).convert(array({items}).convert({_CHARACTER}))")
    row = _safe_read(view, [entry], expressions)[0] if expressions else ()
    if row is None:
        page_data.warn("unreadable", entry.shape_id)
        return [("text_fill", None)]
    for chunk in row:
        characters.extend(chunk[0])
    runs = []
    for char_text, char_fill_type, char_fill, char_outline_type, char_outline in characters:
        fill = char_fill if char_fill_type == _UNIFORM else ("none" if char_fill_type == _NO_FILL else None)
        line = char_outline if char_outline_type else "none"
        if runs and runs[-1][1] == fill and runs[-1][2] == line:
            runs[-1][0] += char_text
        else:
            runs.append([char_text, fill, line])
    parts = []
    for run_text, fill, line in runs:
        whitespace = not run_text.strip()
        if fill != "none":
            parts.append(("text_whitespace" if whitespace else "text_fill", fill))
        if line != "none":
            width, color = line
            parts.append(("text_whitespace" if whitespace else "text_outline" if width > 0
                          else "outline_zero_width", color))
    return parts


def _artifact_warnings(page_data, entry, bounds, paint, uses, shape, state):
    if not uses or state not in _MARKING:
        if uses and state == "hidden_object":
            page_data.warn("hidden_object_with_ink", entry.shape_id)
        return
    inked = [part for part, _, description in uses if description["plates"]]
    if not inked:
        return
    if any(part == "outline_zero_width" for part in inked):
        page_data.warn("zero_width_outline_with_ink", entry.shape_id)
    if any(part == "text_whitespace" for part in inked):
        page_data.warn("ink_on_whitespace_text", entry.shape_id)
    if shape["transparent"]:
        page_data.warn("transparency_with_ink", entry.shape_id)
    if bounds and entry.type != _TEXT:
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if width <= page_data.zero and height <= page_data.zero:
            page_data.warn("zero_size_with_ink", entry.shape_id)
        elif page_data.speck is not None and max(width, height) < page_data.speck:
            page_data.tiny.append((entry.shape_id, entry.parent_id))
        elif min(width, height) <= page_data.zero and any(part in ("fill", "fountain_stop", "pattern") for part in inked):
            page_data.warn("fill_on_zero_area_shape", entry.shape_id)


# --- spot identity ------------------------------------------------------------------------------------------

def _normalized(name):
    return re.sub(r"[^0-9a-z]", "", re.sub(r"pantone|®|\(r\)", "", name.lower()))


def _lab(hex_value):
    """CIE L*a*b* (D65) of an sRGB "#RRGGBB" value."""
    if not hex_value or not re.fullmatch(r"#[0-9A-Fa-f]{6}", hex_value):
        return None
    linear = []
    for index in (1, 3, 5):
        channel = int(hex_value[index:index + 2], 16) / 255
        linear.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    r, g, b = linear
    xyz = ((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b,
           (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
    f = [value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116 for value in xyz]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


_WARNINGS = {
    "hidden_printable_ink": "Inks on hidden layers that are still printable: CorelDRAW prints them, but "
                            "export_page_proof_pdf (Publish to PDF) omits them unless include_hidden_printable",
    "ink_only_off_page": "Inks used only by objects outside the page: their plates would be blank",
    "similar_spot_names": "Spot names that differ only in prefix, case or punctuation",
    "similar_spot_colors": f"Different spots that look alike (CIE76 delta E below {_SIMILAR_DELTA_E:g}, from "
                           "CorelDRAW's sRGB appearance at the highest tint used)",
    "spot_tints": "Spots used at tints other than 100% (halftoned)",
    "spot_named_like_process": "Spot inks named like a process ink",
    "spot_palette_mismatch": "One spot name taken from more than one palette",
    "zero_width_outline_with_ink": "Printable outlines of zero width that still carry an ink",
    "zero_size_with_ink": "Printable zero-size objects that still carry an ink",
    "tiny_with_ink": f"Printable inked objects smaller than {TINY_MM} mm. Isolated ones (under {TINY_CLUSTER} below one "
                     "parent) are likely specks; clusters are fine detail. find_shapes(preset='tiny') lists them",
    "fill_on_zero_area_shape": "Fills on printable shapes with zero width or height",
    "ink_on_whitespace_text": "Ink only on whitespace characters (prints nothing)",
    "transparency_with_ink": "Printable objects with transparency (output blends or halftones the ink)",
    "fountain_mixes_inks": "Fountain fills between different inks (may be converted to process at output)",
    "hidden_object_with_ink": "Hidden objects that carry an ink",
    "unknown_fill_colors": "Fill types whose colors are not read (texture, PostScript, hatch, bitmap pattern)",
    "bitmap_process": "Bitmaps whose color mode implies process plates",
    "unreadable": "Objects whose colors CorelDRAW could not read",
    "text_runs_not_read": f"Mixed-color texts longer than {_MAX_MIXED_CHARACTERS} characters (runs not read)",
}


def _finish(page_data, include_ids, printable_only):
    inks = []
    for ink in page_data.inks.values():
        counts = {state: len(ids) for state, ids in ink["objects"].items() if ids}
        if printable_only and not any(state in counts for state in _MARKING):
            continue
        entry = {"ink": ink["ink"], "kind": ink["kind"], "plates": ink["plates"]}
        for field in ("model", "components"):
            if field in ink:
                entry[field] = ink[field]
        if ink["estimated"]:
            entry["plates_estimated"] = True
        if ink["tints"]:
            entry["tints"] = sorted(ink["tints"], reverse=True)
        if ink["palettes"]:
            entry["palette_ids"] = sorted(ink["palettes"])
        if ink["appearance"]:
            entry["appearance"] = {"tint": ink["appearance"][0], "srgb": ink["appearance"][1]}
        entry["objects"] = counts
        entry["parts"] = dict(ink["parts"])
        entry["layers"] = dict(ink["layers"])
        if ink["flags"]:
            entry["flags"] = dict(ink["flags"])
        if include_ids:
            order = {state: index for index, state in enumerate(_STATES)}
            records = sorted(ink["ids"].values(), key=lambda record: order[record["state"]])
            entry["ids"] = records[:page_data.max_ids]
        inks.append(entry)
    inks.sort(key=lambda entry: (["spot", "process", "rgb", "other", "bitmap", "registration", "unknown"]
                                 .index(entry["kind"]), entry["ink"]))

    printed, printed_estimated = page_data.plates(_MARKING)
    proof, proof_estimated = page_data.plates(("prints",))
    # Every spot that printable content names is a plate of the page, even when nothing marks it (off-page or
    # clipped objects, whitespace-only text, hidden layers). Registration marks print on all of the page's plates.
    present = {ink["ink"] for ink in page_data.inks.values() if ink["kind"] == "spot"
               and any(ink["objects"][state] for state in ("prints", "hidden_printable", "off_page", "clipped"))}
    registered = set(PROCESS_PLATES) | present
    print_marked = printed | printed_estimated
    print_plates = print_marked | (registered if page_data.registration["prints"]
                                   or page_data.registration["hidden_printable"] else set())
    proof_plates = proof | proof_estimated | (registered if page_data.registration["prints"] else set())
    order = list(PROCESS_PLATES)

    def ordered(plates):
        return sorted(plates, key=lambda plate: (plate in order, order.index(plate) if plate in order else 0, plate))

    plates = {
        "print": ordered(print_plates),
        "pdf_proof": ordered(proof_plates),
        "registration_only": ordered(print_plates - print_marked),
        "estimated_only": ordered(printed_estimated - printed),
        "hidden_printable_only": ordered(print_plates - proof_plates),
        "blank_spot_plates": ordered(present - print_plates),
    }

    _spot_warnings(page_data)
    for ink in page_data.inks.values():
        marking = any(ink["marking"][state] for state in _MARKING)
        if not marking and ink["objects"]["off_page"] and ink["plates"]:
            warning = page_data.warnings["ink_only_off_page"]
            warning.setdefault("inks", []).append(ink["ink"])
            for shape_id in sorted(ink["objects"]["off_page"], key=int):
                page_data.warn("ink_only_off_page", shape_id)
    _tiny_warning(page_data)
    warnings = []
    for code, entry in page_data.warnings.items():
        warning = {"code": code, "message": _WARNINGS[code], "count": entry["count"]}
        for extra in ("inks", "isolated", "clusters"):
            if extra in entry:
                warning[extra] = entry[extra]
        if include_ids and entry["ids"]:
            warning["ids"] = entry["ids"]
        warnings.append(warning)
    return inks, plates, warnings


def _tiny_warning(page_data):
    """One summary for tiny inked objects: isolated specks first, clusters (fine detail) by parent."""
    if not page_data.tiny:
        return
    per_parent = Counter(parent for _, parent in page_data.tiny)
    isolated = [shape_id for shape_id, parent in page_data.tiny if per_parent[parent] < TINY_CLUSTER]
    warning = page_data.warnings["tiny_with_ink"]
    warning.update(count=len(page_data.tiny), isolated=len(isolated),
                   clusters=[{"parent_id": parent, "count": count} for parent, count in per_parent.most_common(3)
                             if count >= TINY_CLUSTER])
    warning["ids"] = isolated[:page_data.max_ids]


def _spot_warnings(page_data):
    spots = [ink for ink in page_data.inks.values() if ink["kind"] == "spot"
             and any(ink["marking"][state] for state in _MARKING)]
    groups = defaultdict(set)
    for ink in spots:
        groups[_normalized(ink["ink"])].add(ink["ink"])
    names = [sorted(names) for names in groups.values() if len(names) > 1]
    if names:
        page_data.warnings["similar_spot_names"].update(count=len(names), inks=names)
    tinted = [{"ink": ink["ink"], "tints": sorted(ink["tints"], reverse=True)} for ink in spots
              if ink["tints"] - {100}]
    if tinted:
        page_data.warnings["spot_tints"].update(count=len(tinted), inks=tinted)
    process_like = [ink["ink"] for ink in spots
                    if re.search(r"\bprocess\b|\b(cyan|magenta|yellow)\b", ink["ink"], re.IGNORECASE)]
    if process_like:
        page_data.warnings["spot_named_like_process"].update(count=len(process_like), inks=process_like)
    mixed = [{"ink": ink["ink"], "palette_ids": sorted(ink["palettes"])} for ink in spots if len(ink["palettes"]) > 1]
    if mixed:
        page_data.warnings["spot_palette_mismatch"].update(count=len(mixed), inks=mixed)
    labs = {ink["ink"]: _lab(ink["appearance"][1]) for ink in spots if ink["appearance"]}
    close = []
    names_sorted = sorted(name for name, lab in labs.items() if lab is not None)
    for index, first in enumerate(names_sorted):
        for second in names_sorted[index + 1:]:
            distance = math.dist(labs[first], labs[second])
            if distance < _SIMILAR_DELTA_E:
                close.append({"inks": [first, second], "delta_e": round(distance, 1)})
    if close:
        page_data.warnings["similar_spot_colors"].update(count=len(close), inks=close)


# --- tool ---------------------------------------------------------------------------------------------------

def get_ink_inventory(
    document_path: str,
    pages: list[int] | None = None,
    printable_only: bool = False,
    include_ids: bool = True,
    max_ids_per_ink: int = 5,
) -> ToolResult:
    """Every ink each page uses, the source objects of each ink, and the separation plates the page implies.

    Read-only; document_path is any open document. pages: one-based page numbers (default all). Shared
    (master) layers are included on every page they print on. Inks: spot (name, tints, palette identity),
    process CMYK (plates = nonzero components), registration, RGB/other models and bitmaps (plates estimated:
    color management decides), unknown. Each ink counts objects by state: prints, hidden_printable
    (printable layer that is hidden: CorelDRAW prints it, Publish to PDF omits it), hidden_object, off_page
    (entirely outside the page: prints nothing), clipped (PowerClip content outside its container),
    non_printable_layer; parts used (fill, outline, outline_zero_width, text_fill/text_outline per
    character run, text_whitespace, fountain_stop, pattern, bitmap); objects per layer; flags (in_powerclip,
    effect_generated, transparent, locked_layer, master_layer); and up to max_ids_per_ink (0-50) StaticIDs
    with layer, parts, state and PowerClip/effect links, printing objects first.

    Page plates: print (non-blank plates of a CorelDRAW print), pdf_proof (same for export_page_proof_pdf),
    registration_only (plates that only registration marks), estimated_only, hidden_printable_only,
    blank_spot_plates (spots the page names but nothing marks: off page, clipped, whitespace). Warnings flag
    near-duplicate spots, tints, hidden-but-printable inks, off-page-only inks, zero-width or zero-size
    inked objects, tiny objects (isolated vs clusters), whitespace-only text runs, transparency, mixed fountains and process bitmaps.
    Printable_only drops inks with no printing object. A long document may return next_pages: call
    again with pages=next_pages.
    """
    if not 0 <= max_ids_per_ink <= 50:
        return ToolResult.fail("max_ids_per_ink must be between 0 and 50")
    conn = get_connection()

    def inventory():
        doc, actual_path = resolve_document(conn.app, document_path)
        page_count = doc.Pages.Count
        wanted = list(range(1, page_count + 1)) if pages is None else list(dict.fromkeys(pages))
        if not wanted or any(not isinstance(index, int) or not 1 <= index <= page_count for index in wanted):
            raise ValueError(f"pages must be page numbers between 1 and {page_count}")
        unit = unit_name(doc.Unit)
        results = []
        for position, page_index in enumerate(wanted):
            if results and conn.time_left() < _RESERVE_SECONDS * 2:
                return results, wanted[position:], actual_path, unit, page_count
            page = resolve_page(doc, page_index)
            try:
                results.append(_page_inventory(conn, doc, page, page_index, unit, include_ids, max_ids_per_ink,
                                               printable_only))
            except _OutOfTimeError:
                if not results:
                    raise TimeoutError(f"Page {page_index} could not be read within the time limit") from None
                return results, wanted[position:], actual_path, unit, page_count
        return results, None, actual_path, unit, page_count

    result = conn.read_call(inventory)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    results, next_pages, actual_path, unit, page_count = result["result"]
    plates = sorted({plate for page in results for plate in page["plates"]["print"]})
    return ToolResult.ok(document_path=actual_path, document_unit=unit, page_count=page_count,
                         document_print_plates=plates, pages=results, next_pages=next_pages)


def _page_inventory(conn, doc, page, page_index, unit, include_ids, max_ids, printable_only, collect=False):
    """One page's inventory. collect=True adds "objects": every inked object with bounds, state and inks."""
    for fresh in (False, True):
        page_data = _Page(page, unit, max_ids, collect)
        try:
            for view in page_layers(doc, page, fresh=fresh):
                _read_layer(conn, page_data, view, include_ids)
            break
        except StaleStructureError:
            continue
    else:
        raise RuntimeError("The page changed while it was being read; retry when it is not being edited")
    inks, plates, warnings = _finish(page_data, include_ids, printable_only)
    left, bottom, right, top = page_data.bounds
    result = {"page_index": page_index, "page_name": page.Name,
              "page_bounds": {"left": left, "bottom": bottom, "right": right, "top": top},
              "plates": plates, "inks": inks, "layers": page_data.layer_rows, "bitmaps": page_data.bitmaps,
              "warnings": warnings}
    if collect:
        result["objects"] = page_data.objects
    return result

