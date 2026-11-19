"""find_shapes: find objects by what they are (combinable filters, cleanup-artifact presets, raw read-only CQL).

Reads go through the page structure cache (``core.structure``): one walk per page, then batched CQL reads of
only the properties the query needs. CorelDRAW's own ``Shapes.FindShapes(Name, Type, Recursive, Query)`` is not
used: its Name argument is an exact, case-sensitive match (an empty name finds only unnamed shapes) and it never
enters PowerClips, so on documents with named objects it silently misses shapes.
"""

from __future__ import annotations

import fnmatch
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Literal

from core import colorspec
from core.connection import get_connection
from core.models import ToolResult
from core.results import failure
from core.structure import (
    CONTAINER_TYPES,
    EFFECT_GROUP_TYPES,
    StaleStructureError,
    locate_entries,
    page_layers,
    read_entries,
    shape_range,
)
from core.targets import normalize_path, resolve_document, resolve_page
from core.units import MEASUREMENT_UNITS, convert, unit_name
from tools.inks import (
    _FOUNTAIN,
    _PAINT,
    _STORY,
    _STORY_OUTLINE,
    _ZERO_MM,
    TINY_CLUSTER,
    TINY_MM,
    _color,
    _ink,
    _safe_read,
    _text_parts,
)

Preset = Literal["zero_size", "invisible", "stacked_duplicates", "off_page", "hairline", "empty_text", "tiny",
                 "hidden_printable_content", "registration_on_art"]

TYPE_NAMES = {
    1: "rectangle", 2: "ellipse", 3: "curve", 4: "polygon", 5: "bitmap", 6: "text", 7: "group", 9: "guideline",
    10: "blend_group", 11: "extrude_group", 12: "ole", 13: "contour_group", 14: "dimension", 15: "bevel_group",
    16: "drop_shadow_group", 18: "artistic_media_group", 19: "connector", 20: "mesh_fill", 21: "perfect_shape",
    22: "custom_effect_group", 23: "symbol",
}
_TYPE_CODES = {name: code for code, name in TYPE_NAMES.items()}
_TEXT, _BITMAP, _GUIDELINE = 6, 5, 9
_VECTOR = {1, 2, 3, 4, 21}  # shapes whose only marks are their fill and outline
_REGISTRATION = 20
_DEFAULT_THRESHOLD_MM = {"hairline": 0.1, "tiny": TINY_MM}
_BATCH = 2000
_RESERVE_SECONDS = 10.0
_MAX_SELECT_CHECK = 500
_FILTER_KEYS = {"ids", "type", "name", "text", "color", "color_part", "color_tolerance", "outline_width", "size",
                "area", "intersects", "inside", "on_page", "printable", "visible", "locked", "master", "effect",
                "powerclip", "depth", "overprint_fill", "overprint_outline"}
_REASON_ORDER = ["zero_size", "zero_area", "no_fill_no_outline", "zero_width_outline_only", "ink_without_area",
                 "clipped_out", "hidden_object", "stacked_duplicate", "off_page", "crosses_page_edge",
                 "zero_width_outline", "hairline", "empty_text", "whitespace_text", "tiny", "tiny_in_cluster",
                 "hidden_printable_layer", "registration_on_art", "match"]
_results_cache: dict = {}


class _Stub:
    """Stands in for the inventory's page accumulator when reusing its text-run reader."""

    def warn(self, code, shape_id):
        pass


@dataclass
class _Shape:
    page: int
    view: object
    entry: object
    order: tuple  # (layer ordinal, index in the layer): top-most first
    name: str = ""
    bounds: tuple | None = None
    visible: bool = True
    hidden: bool = False  # the object or a parent is hidden
    fill: object = None
    outline: object = None
    stops: list | None = None
    story: object = None
    story_outline: object = None
    overprint: tuple | None = None
    effects: int = 0
    cql: bool | None = None
    clip_bounds: tuple | None = None  # bounds of the PowerClip container that clips the shape
    siblings: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def id(self):
        return self.entry.shape_id

    @property
    def type(self):
        return self.entry.type

    @property
    def visible_bounds(self):
        """Bounds clipped to the PowerClip container (None when clipped out entirely)."""
        if self.bounds is None or self.clip_bounds is None:
            return self.bounds
        return _intersection(self.bounds, self.clip_bounds)

    def text(self):
        return (self.story[1] or "") if self.story else ""

    def outline_width(self):
        """Width of a uniform outline in document units; None without one."""
        if self.type == _TEXT:
            line = self.story_outline
        else:
            line = self.outline
        if not line or not line[0]:
            return None
        return line[1][0]

    def paints(self):
        """[(role, color value, text?)] of every painted part; role is fill or outline."""
        out = []
        if self.type == _TEXT:
            for part, value in self.extra.get("runs", ()):
                role = "outline" if part in ("text_outline", "outline_zero_width") else "fill"
                out.append((role, value, True))
            return out
        if self.fill:
            if self.fill[0] == 1:
                out.append(("fill", self.fill[1], False))
            elif self.fill[0] == _FOUNTAIN and self.stops:
                out.extend(("fill", stop, False) for stop in self.stops)
        if self.outline and self.outline[0]:
            out.append(("outline", self.outline[1][1], False))
        return out

    def has_fill(self):
        if self.type == _TEXT:
            if "runs" in self.extra:  # mixed-fill text reports Story.Fill.Type 0: the runs decide
                return any(role == "fill" for role, _, _ in self.paints())
            return bool(self.story) and self.story[2] != 0
        return bool(self.fill) and self.fill[0] != 0

    def has_outline(self):
        if self.type == _TEXT and "runs" in self.extra:
            return any(role == "outline" for role, _, _ in self.paints())
        return self.outline_width() is not None

    def inked(self):
        return self.type == _BITMAP or self.has_fill() or self.has_outline()


def _intersection(a, b):
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[0] <= box[2] and box[1] <= box[3] else None


def _intersects(a, b):
    return a is not None and not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _inside(a, b, tolerance=0.0):
    return a is not None and (a[0] >= b[0] - tolerance and a[1] >= b[1] - tolerance and a[2] <= b[2] + tolerance
                              and a[3] <= b[3] + tolerance)


# --- query parsing ------------------------------------------------------------------------------------------

def _range(value, key, scale=1.0):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"filters.{key} must be [min, max] (either may be null)")
    low, high = value
    for bound in (low, high):
        if bound is not None and not isinstance(bound, (int, float)):
            raise ValueError(f"filters.{key} bounds must be numbers or null")
    return (None if low is None else low * scale, None if high is None else high * scale)


def _rect(value, key, scale):
    if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(isinstance(v, (int, float)) for v in value):
        raise ValueError(f"filters.{key} must be [left, bottom, right, top]")
    left, bottom, right, top = (v * scale for v in value)
    return (min(left, right), min(bottom, top), max(left, right), max(bottom, top))


def _in_range(value, bounds):
    low, high = bounds
    return value is not None and (low is None or value >= low) and (high is None or value <= high)


@dataclass
class _Query:
    preset: str | None
    filters: dict
    threshold: float | None  # document units
    zero: float
    cql: str | None
    matcher: object = None
    types: set | None = None

    def needs_paint(self):
        return self.preset in ("zero_size", "invisible", "stacked_duplicates", "hairline", "tiny",
                               "registration_on_art") or "color" in self.filters or "outline_width" in self.filters


def _parse(preset, filters, threshold, unit, cql, doc_unit):
    filters = dict(filters or {})
    unknown = set(filters) - _FILTER_KEYS
    if unknown:
        raise ValueError(f"Unknown filters: {', '.join(sorted(unknown))}. Use {', '.join(sorted(_FILTER_KEYS))}")
    unit = unit or doc_unit
    if unit not in MEASUREMENT_UNITS and unit != doc_unit:
        raise ValueError(f"unit must be one of {', '.join(MEASUREMENT_UNITS)}")
    scale = convert(1.0, unit, doc_unit) if unit != doc_unit else 1.0
    parsed = {}
    for key, value in filters.items():
        if value is None:
            continue
        if key in ("outline_width", "size", "depth"):
            parsed[key] = _range(value, key, 1.0 if key == "depth" else scale)
        elif key == "area":
            parsed[key] = _range(value, key, scale * scale)
        elif key in ("intersects", "inside"):
            parsed[key] = _rect(value, key, scale)
        elif key in ("printable", "visible", "locked", "master", "effect", "overprint_fill", "overprint_outline"):
            if not isinstance(value, bool):
                raise ValueError(f"filters.{key} must be true or false")
            parsed[key] = value
        elif key == "on_page":
            if value not in (True, False, "crossing"):
                raise ValueError('filters.on_page must be true, false or "crossing"')
            parsed[key] = value
        elif key == "powerclip":
            if value not in ("container", "content"):
                raise ValueError('filters.powerclip must be "container" or "content"')
            parsed[key] = value
        elif key == "color_part":
            if value not in ("fill", "outline", "text", "any"):
                raise ValueError("filters.color_part must be fill, outline, text or any")
            parsed[key] = value
        elif key == "color_tolerance":
            parsed[key] = float(value)
        elif key == "ids":
            ids = [str(v) for v in (value if isinstance(value, list) else [value])]
            if not ids:
                raise ValueError("filters.ids must list one or more shape IDs")
            parsed[key] = ids
        elif key in ("name", "text"):
            if not isinstance(value, str):
                raise ValueError(f"filters.{key} must be a string")
            parsed[key] = value
        else:
            parsed[key] = value
    query = _Query(preset, parsed, None, convert(_ZERO_MM, "mm", doc_unit), (cql or "").strip() or None)
    if "type" in parsed:
        values = parsed["type"] if isinstance(parsed["type"], list) else [parsed["type"]]
        types = set()
        for value in values:
            code = value if isinstance(value, int) else _TYPE_CODES.get(str(value).lower())
            if code is None:
                raise ValueError(f"Unknown shape type {value!r}; use {', '.join(sorted(_TYPE_CODES))} or a type code")
            types.add(code)
        query.types = types
    if "color" in parsed:
        try:
            query.matcher = colorspec.Matcher(colorspec.parse(parsed["color"]), parsed.get("color_tolerance", 0.0))
        except colorspec.ColorSpecError as exc:
            raise ValueError(f"filters.color: {exc}") from None
    elif "color_part" in parsed or "color_tolerance" in parsed:
        raise ValueError("color_part and color_tolerance need filters.color")
    if threshold is not None:
        if preset not in _DEFAULT_THRESHOLD_MM:
            raise ValueError("threshold applies to the hairline and tiny presets")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        query.threshold = threshold * scale
    elif preset in _DEFAULT_THRESHOLD_MM:
        query.threshold = convert(_DEFAULT_THRESHOLD_MM[preset], "mm", doc_unit)
    if query.cql is not None and "application" in query.cql.lower():
        raise ValueError("cql is evaluated on each shape ($item); Application is not available")
    return query


# --- scanning -----------------------------------------------------------------------------------------------

def _read(view, entries, expressions, conn):
    rows = []
    for start in range(0, len(entries), _BATCH):
        if conn.time_left() < _RESERVE_SECONDS:
            raise TimeoutError("The scope is too large to scan within the time limit; pass pages or layers")
        rows.extend(_safe_read(view, entries[start:start + _BATCH], expressions))
    return rows


def _apply_cql(conn, shapes, cql):
    """Evaluate the caller's CQL condition on shapes that passed every other filter (one read per layer)."""
    by_view = defaultdict(list)
    for shape in shapes:
        by_view[id(shape.view)].append(shape)
    for group in by_view.values():
        view = group[0].view
        entries = [shape.entry for shape in group]
        for start in range(0, len(entries), _BATCH):
            if conn.time_left() < _RESERVE_SECONDS:
                raise TimeoutError("The scope is too large to scan within the time limit; pass pages or layers")
            try:
                rows = read_entries(view, entries[start:start + _BATCH], [f"iif({cql}, 1, 0)"])
            except StaleStructureError:
                raise
            except Exception as exc:
                raise ValueError(f"cql could not be evaluated on every shape in scope ({exc}). Narrow the scope "
                                 "with filters (for example type) so the expression applies to each shape") from None
            for shape, row in zip(group[start:start + _BATCH], rows, strict=True):
                shape.cql = bool(row[0])
    return [shape for shape in shapes if shape.cql]


def _scan_layer(conn, query, page_index, page_bounds, view, shapes_out):
    entries = view.structure.entries
    if not entries:
        return
    base = _read(view, entries, ["$item.Name", "array($item.LeftX, $item.BottomY, $item.RightX, $item.TopY)",
                                 "$item.Visible"], conn)
    clips = {path for path, _, _, holds in view.structure.groups if holds}
    siblings = Counter(entry.parent for entry in entries if entry.type not in CONTAINER_TYPES)
    by_id, hidden = {}, {}
    for index, (entry, row) in enumerate(zip(entries, base, strict=True)):
        shape = _Shape(page_index, view, entry, (view.ordinal or 0, index))
        if row is not None:
            name, bounds, visible = row
            shape.name, shape.bounds, shape.visible = name or "", tuple(bounds) if bounds else None, bool(visible)
        shape.hidden = not shape.visible or hidden.get(entry.parent_id, False)
        hidden[entry.shape_id] = shape.hidden
        shape.siblings = siblings.get(entry.parent, 0)
        shape.extra["clip_container"] = entry.path in clips
        by_id[entry.shape_id] = shape
    for shape in by_id.values():
        if shape.entry.clip is not None and shape.entry.clip in by_id:
            container = by_id[shape.entry.clip]
            shape.clip_bounds = container.visible_bounds if container.bounds else None
            if shape.clip_bounds is None:
                shape.clip_bounds = (0.0, 0.0, -1.0, -1.0)  # the container itself is clipped out
    ordinary = [entry for entry in entries if entry.type not in CONTAINER_TYPES]
    painted = [entry for entry in ordinary if entry.type != _TEXT]
    texts = [entry for entry in ordinary if entry.type == _TEXT]
    if query.needs_paint() and painted:
        for entry, row in zip(painted, _read(view, painted, _PAINT, conn), strict=True):
            if row is not None:
                by_id[entry.shape_id].fill, by_id[entry.shape_id].outline = row
        for entry in painted:
            shape = by_id[entry.shape_id]
            if shape.fill and shape.fill[0] == _FOUNTAIN and shape.fill[1] and query.matcher is not None:
                items = ", ".join(f"$item.Fill.Fountain.Colors.Item({i}).Color" for i in range(shape.fill[1]))
                row = _safe_read(view, [entry], [f"array({items}).convert({_color('$item')})"])[0]
                shape.stops = list(row[0]) if row else None
    if texts:
        stories = _read(view, texts, _STORY, conn)
        outlines = _read(view, texts, [_STORY_OUTLINE], conn)
        story_rows = {}
        outline_rows = {}
        for entry, story, outline in zip(texts, stories, outlines, strict=True):
            shape = by_id[entry.shape_id]
            shape.story, shape.story_outline = story, outline[0] if outline else None
            story_rows[entry.shape_id], outline_rows[entry.shape_id] = story, shape.story_outline
        if query.needs_paint():
            stub = _Stub()
            for entry in texts:
                by_id[entry.shape_id].extra["runs"] = _text_parts(entry, story_rows, outline_rows, view, stub)
    if any(key in query.filters for key in ("overprint_fill", "overprint_outline")) and ordinary:
        for entry, row in zip(ordinary, _read(view, ordinary, ["$item.OverprintFill", "$item.OverprintOutline"],
                                              conn), strict=True):
            by_id[entry.shape_id].overprint = tuple(bool(v) for v in row) if row else None
    if "effect" in query.filters and ordinary:
        for entry, row in zip(ordinary, _read(view, ordinary, ["$item.Effects.Count"], conn), strict=True):
            by_id[entry.shape_id].effects = row[0] if row else 0
    shapes_out.extend(by_id.values())


def _scope_views(views, layers, query):
    scoped = []
    for view in views:
        if layers is not None and view.name not in layers:
            continue
        if query.preset is not None and "printable" not in query.filters and not view.printable:
            continue  # presets look for what prints, unless the caller sets filters.printable
        scoped.append(view)
    return scoped


# --- filters and presets ------------------------------------------------------------------------------------

def _filters_match(shape, query, page_bounds):
    f = query.filters
    view, entry = shape.view, shape.entry
    if "ids" in f and shape.id not in f["ids"]:
        return False
    if query.types is not None and shape.type not in query.types:
        return False
    if "name" in f:
        pattern = f["name"]
        if any(ch in pattern for ch in "*?["):
            if not fnmatch.fnmatchcase(shape.name.lower(), pattern.lower()):
                return False
        elif shape.name != pattern:
            return False
    if "text" in f and (shape.type != _TEXT or f["text"].lower() not in shape.text().lower()):
        return False
    for key, actual in (("printable", view.printable), ("locked", not view.editable), ("master", bool(view.master)),
                        ("visible", bool(view.visible) and not shape.hidden)):
        if key in f and f[key] != bool(actual):
            return False
    if "depth" in f and not _in_range(len(entry.path) - 1, f["depth"]):
        return False
    if "powerclip" in f:
        if f["powerclip"] == "content" and entry.clip is None:
            return False
        if f["powerclip"] == "container" and not shape.extra.get("clip_container"):
            return False
    if "effect" in f:
        has = bool(shape.effects) or entry.type in EFFECT_GROUP_TYPES or entry.effect is not None
        if has != f["effect"]:
            return False
    for index, key in enumerate(("overprint_fill", "overprint_outline")):
        if key in f and (shape.overprint is None or shape.overprint[index] != f[key]):
            return False
    bounds = shape.visible_bounds
    if any(key in f for key in ("size", "area", "intersects", "inside", "on_page")) and shape.bounds is None:
        return False
    width = height = 0.0
    if shape.bounds is not None:
        width, height = shape.bounds[2] - shape.bounds[0], shape.bounds[3] - shape.bounds[1]
    if "size" in f and not _in_range(max(width, height), f["size"]):
        return False
    if "area" in f and not _in_range(width * height, f["area"]):
        return False
    if "intersects" in f and not _intersects(bounds, f["intersects"]):
        return False
    if "inside" in f and not _inside(bounds, f["inside"]):
        return False
    if "on_page" in f:
        state = _page_state(bounds, page_bounds)
        wanted = {True: ("inside", "crossing"), False: ("off",), "crossing": ("crossing",)}[f["on_page"]]
        if state not in wanted:
            return False
    if "outline_width" in f and not _in_range(shape.outline_width(), f["outline_width"]):
        return False
    if query.matcher is not None:
        part = f.get("color_part", "any")
        parts = sorted({("text" if text and part == "text" else role) for role, value, text in shape.paints()
                        if (part == "any" or (part == "text" and text) or part == role)
                        and query.matcher.matches(value)})
        if not parts:
            return False
        shape.extra["parts"] = parts
    return True


def _page_state(bounds, page_bounds):
    if bounds is None:
        return "off"
    if not _intersects(bounds, page_bounds):
        return "off"
    return "inside" if _inside(bounds, page_bounds) else "crossing"


def _ordinary(shape):
    return shape.type not in CONTAINER_TYPES and shape.type != _GUIDELINE


def _preset_reasons(shapes, query, page_bounds):
    """{shape ID: (reason, detail)} for one page's shapes (already narrowed by filters)."""
    preset, zero, found = query.preset, query.zero, {}
    candidates = [shape for shape in shapes if _ordinary(shape)]
    if preset == "zero_size":
        for shape in candidates:
            if shape.type == _TEXT or shape.bounds is None:
                continue
            width, height = shape.bounds[2] - shape.bounds[0], shape.bounds[3] - shape.bounds[1]
            if max(width, height) <= zero:
                found[shape.id] = ("zero_size", None)
            elif min(width, height) <= zero and not (shape.outline_width() or 0) > 0:
                found[shape.id] = ("zero_area", None)
    elif preset == "invisible":
        for shape in candidates:
            reason = _invisible(shape, zero)
            if reason:
                found[shape.id] = (reason, None)
    elif preset == "stacked_duplicates":
        found.update(_duplicates(candidates, zero))
    elif preset == "off_page":
        for shape in candidates:
            if shape.bounds is None or shape.visible_bounds is None:
                continue  # unreadable, or clipped out (reported by the invisible preset)
            state = _page_state(shape.visible_bounds, page_bounds)
            if state == "off":
                found[shape.id] = ("off_page", None)
            elif state == "crossing":
                found[shape.id] = ("crosses_page_edge", None)
    elif preset == "hairline":
        for shape in candidates:
            width = shape.outline_width()
            if width is not None and width < query.threshold:
                found[shape.id] = ("zero_width_outline" if width <= 0 else "hairline",
                                   {"outline_width_pt": round(convert(width, shape.extra["unit"], "pt"), 3)})
    elif preset == "empty_text":
        for shape in candidates:
            if shape.type == _TEXT and shape.story is not None and not shape.text().strip():
                found[shape.id] = ("empty_text" if not shape.text() else "whitespace_text", None)
    elif preset == "tiny":
        found.update(_tiny(candidates, query))
    elif preset == "hidden_printable_content":
        for shape in candidates:
            if shape.view.printable and not shape.view.visible:
                found[shape.id] = ("hidden_printable_layer", None)
    elif preset == "registration_on_art":
        found.update(_registration_on_art(candidates, page_bounds))
    return found


def _invisible(shape, zero):
    if shape.type == _TEXT:
        if shape.story is not None and not shape.has_fill() and not shape.has_outline():
            return "no_fill_no_outline"
    elif shape.type in _VECTOR:
        if shape.fill is not None and not shape.has_fill():
            if shape.outline is not None and not shape.outline[0]:
                return "no_fill_no_outline"
            if (shape.outline_width() or 0) <= 0:
                return "zero_width_outline_only"
    if not shape.inked():
        return None
    if shape.bounds is not None and shape.clip_bounds is not None and shape.visible_bounds is None:
        return "clipped_out"
    if shape.hidden and shape.view.visible:
        return "hidden_object"
    if shape.type == _TEXT:
        return "ink_without_area" if shape.story is not None and not shape.text().strip() else None
    if shape.bounds is not None:
        width, height = shape.bounds[2] - shape.bounds[0], shape.bounds[3] - shape.bounds[1]
        if max(width, height) <= zero or (min(width, height) <= zero and not (shape.outline_width() or 0) > 0):
            return "ink_without_area"
    return None


def _ink_key(value):
    """An ink identity that ignores palette IDs and appearance (one spot can carry several palette IDs)."""
    key, description = _ink(value)
    return [*key, description.get("tint")]


def _paint_key(shape):
    if shape.type == _TEXT:
        story = shape.story
        fill = [story[2], _ink_key(story[3]) if story[2] == 1 else None] if story else None
        line = shape.story_outline
    else:
        fill = [shape.fill[0], _ink_key(shape.fill[1]) if shape.fill[0] == 1 else None,
                [_ink_key(stop) for stop in shape.stops or ()]] if shape.fill else None
        line = shape.outline
    width = shape.outline_width()
    outline = [line[0], _ink_key(line[1][1])] if line and line[0] else None
    return (json.dumps(fill, default=str), json.dumps(outline, default=str),
            None if width is None else round(width, 6), shape.text() if shape.type == _TEXT else None)


_NODES = ["$item.DisplayCurve.Nodes.Count",
          "iif($item.DisplayCurve.Nodes.Count <= 5000, "
          "$item.DisplayCurve.Nodes.toarray().convert(array($item.PositionX, $item.PositionY)), 0)"]


def _duplicates(candidates, zero):
    """Exact stacked copies: same type, bounds, paint and node positions (text: same text). Hash on type, bounds
    and paint, then confirm only the candidates' geometry; never compares all pairs."""
    step = max(zero * 100, 1e-9)  # coarse buckets (0.1 mm); bounds are confirmed to zero
    buckets = defaultdict(list)
    for shape in candidates:
        if shape.bounds is None or shape.entry.effect is not None:
            continue
        key = (shape.type, *(round(value / step) for value in shape.bounds), _paint_key(shape))
        buckets[key].append(shape)
    groups = [group for group in buckets.values() if len(group) > 1]
    if not groups:
        return {}
    # Confirm geometry for the candidates only (one batched read per layer).
    by_view = defaultdict(list)
    for group in groups:
        for shape in group:
            by_view[id(shape.view)].append(shape)
    for shapes in by_view.values():
        view = shapes[0].view
        rows = _safe_read(view, [shape.entry for shape in shapes], _NODES)
        for shape, row in zip(shapes, rows, strict=True):
            shape.extra["geometry"] = row
    found = {}
    for group in groups:
        clusters = []  # [(signature, [shapes])]: same node count and bounds, every node within zero
        for shape in group:
            geometry = shape.extra.get("geometry")
            if geometry is None:
                continue  # unreadable geometry: not confirmed
            count, nodes = geometry
            # Order-insensitive node positions: a copy redrawn from another start node is still a copy.
            signature = (count, sorted(nodes) if isinstance(nodes, tuple) else nodes)
            for other, members in clusters:
                if _same_geometry(signature, other, shape.bounds, members[0].bounds, zero):
                    members.append(shape)
                    break
            else:
                clusters.append((signature, [shape]))
        confirmed = {index: members for index, (_, members) in enumerate(clusters)}
        for copies in confirmed.values():
            if len(copies) < 2:
                continue
            copies.sort(key=lambda shape: shape.order)
            bottom = copies[-1]  # stacking order: the last is the lowest; the ones above it are the extra copies
            for shape in copies[:-1]:
                found[shape.id] = ("stacked_duplicate", {"duplicate_of": bottom.id})
    return found


def _same_geometry(first, second, bounds, other_bounds, zero):
    if any(abs(a - b) > zero for a, b in zip(bounds, other_bounds, strict=True)) or first[0] != second[0]:
        return False
    if not isinstance(first[1], list) or not isinstance(second[1], list):
        return first[1] == second[1]
    return len(first[1]) == len(second[1]) and all(
        abs(a[0] - b[0]) <= zero and abs(a[1] - b[1]) <= zero for a, b in zip(first[1], second[1], strict=True))


def _tiny(candidates, query):
    hits = []
    for shape in candidates:
        if shape.bounds is None or not shape.inked():
            continue
        size = max(shape.bounds[2] - shape.bounds[0], shape.bounds[3] - shape.bounds[1])
        if query.zero < size < query.threshold:
            hits.append(shape)
    per_parent = Counter(shape.entry.parent for shape in hits)
    found = {}
    for shape in hits:
        count = per_parent[shape.entry.parent]
        cluster = count >= TINY_CLUSTER
        found[shape.id] = ("tiny_in_cluster" if cluster else "tiny",
                           {"parent_id": shape.entry.parent_id} if shape.entry.parent_id else None)
    return found


def _registration_on_art(candidates, page_bounds):
    """Registration-colored objects whose visible bounds overlap other printing art."""
    printing = [shape for shape in candidates if shape.view.visible and not shape.hidden
                and _page_state(shape.visible_bounds, page_bounds) != "off"]
    registered, art = [], []
    for shape in printing:
        kinds = {"registration" if value and value[0] == _REGISTRATION else "ink" for _, value, _ in shape.paints()}
        if shape.type == _BITMAP:
            kinds.add("ink")
        if "registration" in kinds:
            registered.append(shape)
        if "ink" in kinds:
            art.append(shape)
    if not registered or not art:
        return {}
    cell = max(page_bounds[2] - page_bounds[0], page_bounds[3] - page_bounds[1]) / 64 or 1.0
    grid = defaultdict(list)
    for shape in art:
        box = shape.visible_bounds
        for x in range(int(box[0] // cell), int(box[2] // cell) + 1):
            for y in range(int(box[1] // cell), int(box[3] // cell) + 1):
                if len(grid[(x, y)]) < 100000:
                    grid[(x, y)].append(shape)
    found = {}
    for shape in registered:
        box = shape.visible_bounds
        overlaps = set()
        for x in range(int(box[0] // cell), int(box[2] // cell) + 1):
            for y in range(int(box[1] // cell), int(box[3] // cell) + 1):
                overlaps.update(other.id for other in grid.get((x, y), ())
                                if other is not shape and _intersects(box, other.visible_bounds)
                                and not (shape.type == _TEXT and other.type == _TEXT))  # labels side by side
        if overlaps:
            found[shape.id] = ("registration_on_art", {"overlaps": len(overlaps)})
    return found


# --- records ------------------------------------------------------------------------------------------------

def _record(shape, reason, detail):
    record = {"id": shape.id, "type": TYPE_NAMES.get(shape.type, f"type_{shape.type}"), "page": shape.page,
              "layer": shape.view.name}
    if shape.name:
        record["name"] = shape.name
    if shape.view.master:
        record["master"] = True
    if shape.bounds is not None:
        record["bounds"] = [round(value, 5) for value in shape.bounds]
    if shape.type == _TEXT and shape.story is not None:
        text = shape.text()
        record["text"] = text[:40] + ("…" if len(text) > 40 else "")
    record["reason"] = reason
    if detail:
        record.update(detail)
    if "parts" in shape.extra:
        record["color_parts"] = shape.extra["parts"]
    return record


def _summaries(matches):
    """Compact summaries: tiny clusters by parent; duplicate groups."""
    summary = {}
    for reason, label in (("tiny_in_cluster", "tiny_clusters"), ("tiny", "tiny_by_parent")):
        parents = Counter((m["page"], m.get("parent_id"), m["layer"]) for m in matches if m["reason"] == reason)
        if parents:
            summary[label] = [{"page": page, "parent_id": parent, "layer": layer, "count": count}
                              for (page, parent, layer), count in parents.most_common(5)]
    groups = {m["duplicate_of"] for m in matches if m.get("duplicate_of")}
    if groups:
        summary["duplicate_groups"] = len(groups)
    return summary


# --- tool ---------------------------------------------------------------------------------------------------

def _scan(conn, document_path, pages, layers, query_args):
    doc, actual_path = resolve_document(conn.app, document_path)
    doc_unit = unit_name(doc.Unit)
    query = _parse(*query_args, doc_unit)
    page_count = doc.Pages.Count
    wanted = list(range(1, page_count + 1)) if pages is None else list(dict.fromkeys(pages))
    if not wanted or any(not isinstance(index, int) or not 1 <= index <= page_count for index in wanted):
        raise ValueError(f"pages must be page numbers between 1 and {page_count}")
    matches, fingerprints, seen_layers, seen_ids = [], [], set(), set()
    for page_index in wanted:
        page = resolve_page(doc, page_index)
        page_bounds = (page.LeftX, page.BottomY, page.RightX, page.TopY)
        for fresh in (False, True):
            try:
                views = page_layers(doc, page, fresh=fresh)
                seen_layers.update(view.name for view in views)
                shapes = []
                for view in _scope_views(views, layers, query):
                    _scan_layer(conn, query, page_index, page_bounds, view, shapes)
                for shape in shapes:
                    shape.extra["unit"] = doc_unit
                selected = [shape for shape in shapes if _filters_match(shape, query, page_bounds)]
                if query.cql is not None:
                    selected = _apply_cql(conn, selected, query.cql)
                seen_ids.update(shape.id for shape in shapes)
                if query.preset is None:
                    reasons = {shape.id: ("match", None) for shape in selected}
                else:
                    reasons = _preset_reasons(selected, query, page_bounds)
                break
            except StaleStructureError:
                if fresh:
                    raise RuntimeError("The page changed while it was being read; retry") from None
        fingerprints.append((page_index, tuple(id(view.structure) for view in views)))
        page_matches = [(shape, *reasons[shape.id]) for shape in selected if shape.id in reasons]
        page_matches.sort(key=lambda item: (_REASON_ORDER.index(item[1]) if item[1] in _REASON_ORDER else 99,
                                            item[0].order))
        matches.extend(_record(shape, reason, detail) for shape, reason, detail in page_matches)
    if layers is not None and (missing := sorted(set(layers) - seen_layers)):
        raise ValueError(f"No layer named {', '.join(map(repr, missing))} on the requested pages")
    missing_ids = sorted(set(query.filters.get("ids", ())) - seen_ids, key=lambda value: (len(value), value))
    return {"document_path": actual_path, "document_unit": doc_unit, "pages": wanted, "matches": matches,
            "missing_ids": missing_ids, "fingerprints": fingerprints}


def _cached(key, conn, document_path, fingerprints):
    """True when the page structures of a cached result are still the cached (valid) ones."""
    def check():
        doc, _ = resolve_document(conn.app, document_path)
        return all(tuple(id(view.structure) for view in page_layers(doc, resolve_page(doc, page))) == ids
                   for page, ids in fingerprints)

    result = conn.read_call(check)
    return result["success"] and result["result"]


def _select(conn, document_path, page_index, ids):
    def run():
        doc, actual_path = resolve_document(conn.app, document_path, activate=True)
        page = resolve_page(doc, page_index)
        if doc.ActivePage.Index != page_index:
            page.Activate()
        located = locate_entries(doc, page, ids) if ids else {}
        by_view = defaultdict(list)
        for view, entry in located.values():
            by_view[id(view)].append((view, entry))
        total = None
        for items in by_view.values():
            view = items[0][0]
            read_entries(view, [entry for _, entry in items], [])  # verify the IDs before building the range
            part = shape_range(view, [entry for _, entry in items])
            if total is None:
                total = part
            else:
                total.AddRange(part)
        if total is None:
            doc.ClearSelection()
        else:
            total.CreateSelection()
        selection = doc.SelectionRange
        data = {"selected_count": selection.Count}
        if len(ids) <= _MAX_SELECT_CHECK:
            selected = {str(selection.Item(index).StaticID) for index in range(1, selection.Count + 1)}
            data["not_selected"] = [shape_id for shape_id in ids if shape_id not in selected]
        return data

    return conn.safe_call(run)


def find_shapes(
    document_path: str,
    pages: list[int] | None = None,
    layers: list[str] | None = None,
    preset: Preset | None = None,
    filters: dict | None = None,
    threshold: float | None = None,
    unit: str | None = None,
    cql: str | None = None,
    limit: int = 100,
    offset: int = 0,
    select: bool = False,
) -> ToolResult:
    """Find objects by criteria, including cleanup artifacts that make bad films. Read-only unless select=True.

    pages (default all), layers (exact names; master layers included). Presets, on printable layers unless
    filters.printable is set: zero_size, invisible (no visible paint, clipped out or hidden), stacked_duplicates
    (copies above the bottom one), off_page (off or crossing the edge), hairline (outline below threshold,
    default 0.1 mm), empty_text, tiny (inked, larger side below threshold, default 0.1 mm; tiny_in_cluster: 20+
    under one parent), hidden_printable_content, registration_on_art.
    filters (all must match): ids, type ("curve", "text"...), name (exact or glob), text (substring), color
    (a color spec, see set_fill) + color_part fill|outline|text|any + color_tolerance; [min, max] ranges
    outline_width, size (larger side), area, depth; intersects/inside [left, bottom, right, top]; on_page
    true|false|"crossing"; booleans printable, visible, locked, master, effect, overprint_fill, overprint_outline;
    powerclip "container"|"content". Lengths and threshold in unit (default: the document unit). cql: a
    read-only CorelDRAW query on $item ("$item.Outline.Width > 0.1").
    Returns count, counts per reason and page, summary, and records (id, type, page, layer, name, bounds, text,
    reason, duplicate_of) from offset, up to limit (1-1000): page through with next_offset. select=True selects
    every match in the window (one page; activates the document).
    """
    if not 1 <= limit <= 1000 or offset < 0:
        return ToolResult.fail("limit must be between 1 and 1000 and offset nonnegative")
    conn = get_connection()
    query_args = (preset, filters, threshold, unit, cql)
    key = json.dumps([normalize_path(document_path), pages, layers, preset, filters, threshold, unit, cql],
                     sort_keys=True, default=str)
    cached = _results_cache.get(key)
    if cached is not None and cached["generation"] == conn.generation and offset > 0 \
            and _cached(key, conn, document_path, cached["result"]["fingerprints"]):
        result = cached["result"]
    else:
        scanned = conn.read_call(lambda: _scan(conn, document_path, pages, layers, query_args))
        if not scanned["success"]:
            return failure(scanned, document_path=document_path)
        result = scanned["result"]
        _results_cache.pop(key, None)
        _results_cache[key] = {"generation": conn.generation, "result": result}
        while len(_results_cache) > 4:
            _results_cache.pop(next(iter(_results_cache)))
    matches = result["matches"]
    data = {
        "document_path": result["document_path"], "document_unit": result["document_unit"], "pages": result["pages"],
        "preset": preset, "count": len(matches), "counts": dict(Counter(m["reason"] for m in matches)),
        "by_page": {str(page): count for page, count in sorted(Counter(m["page"] for m in matches).items())},
        "summary": _summaries(matches), "shapes": matches[offset:offset + limit],
        "next_offset": offset + limit if offset + limit < len(matches) else None,
    }
    if result["missing_ids"]:
        data["missing_ids"] = result["missing_ids"]
    if select:
        if len(result["pages"]) != 1:
            return ToolResult(success=False, error="select=True needs exactly one page (pass pages=[n])", data=data)
        if result["missing_ids"]:
            return ToolResult(success=False, error="Some requested IDs were not found (see missing_ids); selection "
                              "unchanged", data=data)
        selected = _select(conn, document_path, result["pages"][0], [m["id"] for m in matches])
        _results_cache.pop(key, None)
        if not selected["success"]:
            return ToolResult(success=False, error=selected["error"], data={**data, "state": selected.get("state")})
        data.update(selected["result"])
        if data["selected_count"] != len(matches) or data.get("not_selected"):
            return ToolResult(success=False, error="CorelDRAW selected a different set of shapes (locked layers or "
                              "master objects cannot be selected)", data=data)
    return ToolResult.ok(**data)
