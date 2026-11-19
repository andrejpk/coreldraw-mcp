"""Color specifications shared by every color tool: parsed in one place, resolved to CorelDRAW colors, and described
and matched in the identity model of the ink inventory (``tools.inks``).

Accepted specs (a string or an object):

* ``"none"``; ``"registration"``;
* ``"CMYK 0/100/100/0"`` (also ``,`` or spaces), ``"RGB 255/0/0"`` or ``"#FF0000"``;
* any other string is a spot ink name: ``"PANTONE 485 C"``, ``"485 C"`` or a custom spot such as ``"RVW-BK01A"``;
* ``{"ink": <one of the strings above>, "tint": 0-100, "palette": <palette name or GUID>}`` (tint and palette
  only for a spot). An ink entry of ``get_ink_inventory`` or a color of ``inspect_shapes`` is accepted as is;
* ``{"from_shape_id": "121", "part": "fill" | "outline"}``: the uniform fill (default) or outline color of a donor
  shape on the same page.

Spot names are looked up in the document's own palette first (spots the document already uses), then in the
installed spot palettes (``<CorelDRAW>\\Color\\Palettes\\Spot`` and the user's custom palettes). Names match with
or without the ``PANTONE`` prefix, ignoring case, spaces and punctuation. A name that matches several different
inks fails as ambiguous; an unknown name fails with near matches. One name in several palettes resolves to the
document palette, else the first palette in a stable preference order (PANTONE+ before older libraries), and
the result names the alternatives. The palette can be pinned with ``palette``.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

ColorSpec = str | dict

# cdrColorType codes (tools.inks uses the same).
_PANTONE, _CMYK, _RGB, _REGISTRATION, _SPOT = 1, 2, 5, 20, 25
_NUMBER = r"(-?\d+(?:\.\d+)?)"
_SEP = r"[\s/,;]+"
_CMYK_RE = re.compile(rf"^cmyk\s*\(?\s*{_NUMBER}{_SEP}{_NUMBER}{_SEP}{_NUMBER}{_SEP}{_NUMBER}\s*\)?$", re.IGNORECASE)
_RGB_RE = re.compile(rf"^rgb\s*\(?\s*{_NUMBER}{_SEP}{_NUMBER}{_SEP}{_NUMBER}\s*\)?$", re.IGNORECASE)
_HEX_RE = re.compile(r"^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$", re.IGNORECASE)
_KEYS = {"ink", "tint", "palette", "from_shape_id", "part", "kind", "name", "model", "components", "type",
         "tints", "plates", "objects", "parts", "layers", "flags", "ids", "palette_ids", "appearance",
         "plates_estimated", "is_spot"}
NONE = {"ink": "none", "kind": "none"}


class ColorSpecError(ValueError):
    """The color spec is malformed or names no color."""


@dataclass(frozen=True)
class Spec:
    kind: str  # spot | cmyk | rgb | registration | none | donor
    name: str | None = None
    tint: float | None = None  # spot only; None: 100 when applied, any tint when matched
    palette: str | None = None
    components: tuple = ()
    shape_id: str | None = None  # donor
    part: str = "fill"  # donor part

    def label(self) -> str:
        if self.kind == "spot":
            return self.name + (f" @{self.tint:g}%" if self.tint is not None else "")
        if self.kind in ("cmyk", "rgb"):
            return f"{self.kind.upper()} " + "/".join(f"{value:g}" for value in self.components)
        if self.kind == "donor":
            return f"{self.part} of shape {self.shape_id}"
        return self.kind


# --- parsing ------------------------------------------------------------------------------------------------

def _numbers(values, count, top, label):
    if len(values) != count:
        raise ColorSpecError(f"{label} needs {count} components")
    numbers = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ColorSpecError(f"{label} components must be numbers") from None
        if not 0 <= number <= top:
            raise ColorSpecError(f"{label} components must be between 0 and {top}")
        numbers.append(int(round(number)) if top == 255 else number)
    return tuple(numbers)


def _parse_string(text: str) -> Spec:
    value = text.strip()
    if not value:
        raise ColorSpecError("Empty color")
    lowered = value.lower()
    if lowered in ("none", "no fill", "no outline"):
        return Spec("none")
    if lowered in ("registration", "registration color"):
        return Spec("registration")
    if match := _CMYK_RE.match(value):
        return Spec("cmyk", components=_numbers(match.groups(), 4, 100, "CMYK"))
    if match := _RGB_RE.match(value):
        return Spec("rgb", components=_numbers(match.groups(), 3, 255, "RGB"))
    if match := _HEX_RE.match(value):
        return Spec("rgb", components=tuple(int(group, 16) for group in match.groups()))
    if lowered.startswith(("cmyk", "rgb")) and re.match(r"^(cmyk|rgb)[\s(]", lowered):
        raise ColorSpecError(f"Cannot read {value!r}; use 'CMYK c/m/y/k' (0-100) or 'RGB r/g/b' (0-255)")
    return Spec("spot", name=value)


def _tint(value):
    if value is None:
        return None
    try:
        tint = float(value)
    except (TypeError, ValueError):
        raise ColorSpecError("tint must be a number from 0 to 100") from None
    if not 0 <= tint <= 100:
        raise ColorSpecError("tint must be a number from 0 to 100")
    return tint


def parse(spec: ColorSpec) -> Spec:
    """Parse a color spec (see the module docstring). Raises ColorSpecError with a message for the caller."""
    if isinstance(spec, Spec):
        return spec
    if isinstance(spec, str):
        return _parse_string(spec)
    if not isinstance(spec, dict):
        raise ColorSpecError("A color is a string such as 'PANTONE 485 C', 'CMYK 0/0/0/100', 'none', or an object")
    unknown = set(spec) - _KEYS
    if unknown:
        raise ColorSpecError(f"Unknown color keys: {', '.join(sorted(unknown))}. Use ink, tint, palette, "
                             "or from_shape_id and part")
    if spec.get("from_shape_id") is not None:
        part = spec.get("part", "fill")
        if part not in ("fill", "outline"):
            raise ColorSpecError("part must be fill or outline")
        return Spec("donor", shape_id=str(spec["from_shape_id"]), part=part)
    kind = spec.get("kind")
    if isinstance(spec.get("ink"), str):
        base = _parse_string(spec["ink"])
    elif kind == "spot" and spec.get("name"):
        base = Spec("spot", name=str(spec["name"]))
    elif kind in ("registration", "none"):
        base = Spec(kind)
    elif kind in ("process", "rgb") and spec.get("components") is not None:
        model = spec.get("model") or ("RGB" if kind == "rgb" else "CMYK")
        if model == "CMYK":
            base = Spec("cmyk", components=_numbers(spec["components"], 4, 100, "CMYK"))
        elif model == "RGB":
            base = Spec("rgb", components=_numbers(spec["components"], 3, 255, "RGB"))
        else:
            raise ColorSpecError(f"Color model {model!r} is not supported; use CMYK or RGB")
    else:
        raise ColorSpecError("A color object needs ink (or from_shape_id)")
    tint = spec.get("tint")
    palette = spec.get("palette")
    if base.kind != "spot":
        if tint is not None or palette is not None:
            raise ColorSpecError("tint and palette apply to spot colors only")
        return base
    return Spec("spot", name=base.name, tint=_tint(tint), palette=str(palette) if palette else None)


# --- identity (the ink inventory's color model) -------------------------------------------------------------

def value_of(color) -> tuple:
    """A COM color as the (type, details, is_spot) tuple that the inventory's CQL color query returns."""
    color_type = color.Type
    if color_type == _REGISTRATION:
        return (color_type, 0, False)
    is_spot = color_type == _PANTONE or bool(color.IsSpot)
    if is_spot:
        try:
            appearance = color.HexValue
        except Exception:
            appearance = None
        return (color_type, (color.Name, color.Tint, color.PaletteIdentifier, appearance), True)
    if color_type == _CMYK:
        return (color_type, (color.CMYKCyan, color.CMYKMagenta, color.CMYKYellow, color.CMYKBlack), False)
    if color_type == _RGB:
        return (color_type, (color.RGBRed, color.RGBGreen, color.RGBBlue), False)
    return (color_type, 0, False)


def identity(value) -> dict:
    """Compact identity of a queried color value, in the inventory's terms (ink, kind, tint or components)."""
    from tools.inks import _ink

    if value == "none":
        return dict(NONE)
    key, description = _ink(value)
    result = {"ink": description["ink"], "kind": description["kind"]}
    if key[0] == "spot":
        result["tint"] = description["tint"]
    elif "components" in description:
        result["components"] = description["components"]
    return result


def expected_identity(spec: Spec, tint=None) -> dict:
    """The identity a resolved, non-donor spec must read back as (spot tint: the given or carried tint)."""
    if spec.kind == "none":
        return dict(NONE)
    if spec.kind == "registration":
        return {"ink": "Registration", "kind": "registration"}
    if spec.kind == "cmyk":
        components = [int(round(value)) for value in spec.components]
        return {"ink": "CMYK " + "/".join(map(str, components)), "kind": "process", "components": components}
    if spec.kind == "rgb":
        components = list(spec.components)
        return {"ink": "RGB " + "/".join(map(str, components)), "kind": "rgb", "components": components}
    raise ValueError(f"No fixed identity for {spec.kind}")


def same(actual: dict, expected: dict) -> bool:
    """Read-back check: same ink, kind and tint (spot tints compared to 0.5 %)."""
    if actual.get("ink") != expected.get("ink") or actual.get("kind") != expected.get("kind"):
        return False
    if expected.get("kind") == "spot":
        return abs(float(actual.get("tint") or 0) - float(expected.get("tint") or 0)) < 0.5
    return actual.get("components") == expected.get("components")


def normalized(name: str) -> str:
    """Spot name key: case, spaces, punctuation, the PANTONE prefix and the registered sign ignored."""
    return re.sub(r"[^0-9a-z]", "", re.sub(r"pantone|®|\(r\)|�", "", name.lower()))


class Matcher:
    """Does a queried color value match a ``from`` spec? Spot: name (normalized) and, if given, tint.
    Process and RGB: components within ``tolerance``. Registration: any registration color."""

    def __init__(self, spec: Spec, tolerance: float = 0.0):
        if spec.kind not in ("spot", "cmyk", "rgb", "registration"):
            raise ColorSpecError("Match a spot, CMYK, RGB or registration color")
        if tolerance < 0:
            raise ColorSpecError("tolerance must not be negative")
        self.spec, self.tolerance = spec, tolerance
        self.key = normalized(spec.name) if spec.kind == "spot" else None

    def matches(self, value) -> bool:
        if value is None or value == "none":
            return False
        from tools.inks import _ink

        key, description = _ink(value)
        kind = self.spec.kind
        if kind == "registration":
            return key == ("registration",)
        if kind == "spot":
            if key[0] != "spot" or normalized(key[1]) != self.key:
                return False
            return self.spec.tint is None or abs(float(description["tint"]) - self.spec.tint) < 0.5
        if key[0] != kind:
            return False
        return all(abs(float(a) - float(b)) <= self.tolerance + 1e-9
                   for a, b in zip(description["components"], self.spec.components, strict=True))


# --- spot lookup ----------------------------------------------------------------------------------------------

_PALETTE_RE = re.compile(r'<palette\b[^>]*\bname="([^"]*)"[^>]*\bguid="([^"]*)"', re.IGNORECASE)
_PALETTE_GUID_FIRST_RE = re.compile(r'<palette\b[^>]*\bguid="([^"]*)"[^>]*\bname="([^"]*)"', re.IGNORECASE)
_COLOR_RE = re.compile(r'<cs\b[^>]*\bname="([^"]*)"', re.IGNORECASE)
_index_cache: dict = {}


def _xml_unescape(text):
    return (text.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&amp;", "&"))


def _palette_rank(path: Path):
    parts = [part.lower() for part in path.parts]
    text = str(path).lower()
    return (0 if "pantone+" in parts else 1 if "pantone" in parts and "previous version" not in parts
            else 2 if "previous version" not in parts else 3, text)


def spot_index(app) -> dict:
    """Installed spot palettes, read once per server from their XML files.

    ``{"palettes": [(name, guid, path)], "names": {normalized: [(name, palette name, guid)]}}`` in preference
    order. Palette files are read from disk (fast and side-effect free); CorelDRAW creates the color.
    """
    roots = []
    for attribute, relative in (("SetupPath", ("Color", "Palettes", "Spot")), ("UserDataPath", ("Palettes",))):
        try:
            base = getattr(app, attribute)
        except Exception:
            base = None
        if base:
            roots.append(Path(base).joinpath(*relative))
    key = tuple(str(root) for root in roots)
    if key in _index_cache:
        return _index_cache[key]
    files = sorted({path for root in roots if root.is_dir() for path in root.rglob("*.xml")}, key=_palette_rank)
    palettes, names = [], {}
    for path in files:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")  # older palettes store the registered sign as cp1252
        head = text[:2000]
        match = _PALETTE_RE.search(head)
        if match:
            palette_name, guid = match.groups()
        elif match := _PALETTE_GUID_FIRST_RE.search(head):
            guid, palette_name = match.groups()
        else:
            continue
        palette_name = _xml_unescape(palette_name)
        palettes.append((palette_name, guid, str(path)))
        seen = set()
        for color_name in _COLOR_RE.findall(text):
            color_name = _xml_unescape(color_name)
            if color_name in seen:
                continue
            seen.add(color_name)
            names.setdefault(normalized(color_name), []).append((color_name, palette_name, guid))
    _index_cache[key] = {"palettes": palettes, "names": names}
    return _index_cache[key]


def _document_spots(doc):
    """[(name, index)] of spot colors in the document palette."""
    palette = doc.Palette
    spots = []
    for index in range(1, palette.ColorCount + 1):
        color = palette.Color(index)
        try:
            if color.Type in (_PANTONE, _SPOT) or color.IsSpot:
                spots.append((color.Name, index))
        except Exception:
            continue  # a palette entry without a readable type is not a usable spot
    return spots


def _distinct(candidates):
    return sorted({name for name, *_ in candidates})


def _suggestions(wanted, document_spots, index, limit=6):
    pool = {}
    for name, _ in document_spots:
        pool.setdefault(normalized(name), f"{name} (document palette)")
    for key, entries in index["names"].items():
        pool.setdefault(key, f"{entries[0][0]} ({entries[0][1]})")
    keys = difflib.get_close_matches(normalized(wanted), list(pool), n=limit, cutoff=0.6)
    prefix = [key for key in pool if key.startswith(normalized(wanted)) and key not in keys][: max(0, limit - len(keys))]
    return [pool[key] for key in keys + prefix][:limit]


def resolve_spot(app, doc, spec: Spec, tint: float | None = None):
    """Create the CorelDRAW spot color for a spot spec. Returns (color, info) with the palette used.

    ``tint`` overrides the spec's tint (used to carry a replaced object's tint over); default 100.
    """
    tint = spec.tint if tint is None else tint
    tint = 100.0 if tint is None else tint
    wanted = spec.name.strip()
    key = normalized(wanted)
    if not key:
        raise ColorSpecError(f"{wanted!r} is not a spot color name")
    index = spot_index(app)
    document_spots = _document_spots(doc)

    if spec.palette:
        pinned = spec.palette.strip().lower()
        if pinned in ("document", "document palette"):
            guid, palette_name, names = doc.Palette.Identifier, "document palette", [n for n, _ in document_spots]
        else:
            found = [(name, guid) for name, guid, _ in index["palettes"]
                     if pinned in (name.lower(), guid.lower())]
            if not found:
                raise ColorSpecError(f"No installed spot palette named {spec.palette!r}")
            palette_name, guid = found[0]
            names = [name for name, pal, pal_guid in index["names"].get(key, []) if pal_guid == guid]
        exact = [name for name in names if name.lower() == wanted.lower()] or \
                [name for name in names if normalized(name) == key]
        if not exact:
            raise ColorSpecError(f"{wanted!r} is not in palette {palette_name!r}. Near matches: "
                                 f"{', '.join(_suggestions(wanted, document_spots, index)) or 'none'}")
        return _create(app, guid, exact[0], tint, palette_name, [])

    # 1. The document palette: the spots this document already uses.
    in_document = [name for name, _ in document_spots if name.lower() == wanted.lower()] or \
                  [name for name, _ in document_spots if normalized(name) == key]
    if len(set(in_document)) > 1:
        raise ColorSpecError(f"{wanted!r} is ambiguous in the document palette: {', '.join(sorted(set(in_document)))}"
                             ". Pass the exact name")
    others = index["names"].get(key, [])
    if in_document:
        also = sorted({palette for name, palette, _ in others})
        return _create(app, doc.Palette.Identifier, in_document[0], tint, "document palette", also)
    # 2. Installed spot palettes.
    exact = [entry for entry in others if entry[0].lower() == wanted.lower()]
    if not exact and not wanted.lower().startswith("pantone"):
        exact = [entry for entry in others if entry[0].lower() == f"pantone {wanted}".lower()]
    candidates = exact or others
    if not candidates:
        raise ColorSpecError(f"Spot color {wanted!r} was not found in the document palette or the installed spot "
                             f"palettes. Near matches: {', '.join(_suggestions(wanted, document_spots, index)) or 'none'}")
    if len(_distinct(candidates)) > 1:
        raise ColorSpecError(f"{wanted!r} matches several inks: {', '.join(_distinct(candidates))}. Pass the exact name")
    name, palette_name, guid = candidates[0]
    also = sorted({palette for _, palette, _ in candidates[1:] if palette != palette_name})
    return _create(app, guid, name, tint, palette_name, also)


def _create(app, guid, name, tint, palette_name, also):
    color = app.CreateSpotColorByName(guid, name, tint)
    actual = value_of(color)
    if not actual[2] or actual[1][0] != name or abs(float(actual[1][1]) - float(tint)) >= 0.5:
        raise ColorSpecError(f"CorelDRAW did not create spot {name!r} at {tint:g}% from {palette_name!r}")
    info = {"palette": palette_name, "palette_id": guid}
    if also:
        info["also_in_palettes"] = also
    return color, info


# --- resolution -----------------------------------------------------------------------------------------------

@dataclass
class Resolved:
    """A spec turned into what an edit applies: a COM color (None for "none"), its identity and lookup notes."""

    spec: Spec
    color: object
    identity: dict
    info: dict

    @property
    def none(self) -> bool:
        return self.color is None


def resolve(app, doc, spec: Spec, *, donor_shape=None, tint=None) -> Resolved:
    """Resolve a parsed spec to a COM color. Donor specs need ``donor_shape`` (the resolved COM shape)."""
    if spec.kind == "none":
        return Resolved(spec, None, dict(NONE), {})
    if spec.kind == "registration":
        color = app.CreateRegistrationColor()
    elif spec.kind == "cmyk":
        color = app.CreateCMYKColor(*spec.components)
    elif spec.kind == "rgb":
        color = app.CreateRGBColor(*spec.components)
    elif spec.kind == "spot":
        color, info = resolve_spot(app, doc, spec, tint)
        return Resolved(spec, color, identity(value_of(color)), info)
    elif spec.kind == "donor":
        if donor_shape is None:
            raise ColorSpecError("The donor shape was not resolved")
        if spec.part == "fill":
            fill_type = donor_shape.Fill.Type
            if fill_type == 0:
                return Resolved(spec, None, dict(NONE), {"donor": spec.shape_id, "donor_part": "fill"})
            if fill_type != 1:
                raise ColorSpecError(f"Donor {spec.shape_id} has no uniform fill (fill type {fill_type})")
            color = donor_shape.Fill.UniformColor.GetCopy()
        else:
            if donor_shape.Outline.Type == 0:
                return Resolved(spec, None, dict(NONE), {"donor": spec.shape_id, "donor_part": "outline"})
            color = donor_shape.Outline.Color.GetCopy()
        return Resolved(spec, color, identity(value_of(color)), {"donor": spec.shape_id, "donor_part": spec.part})
    else:
        raise ColorSpecError(f"Unsupported color kind {spec.kind}")
    return Resolved(spec, color, identity(value_of(color)), {})
