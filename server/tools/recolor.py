"""replace_color: change one color to another across a scope (pages, layers, parts), with a dry run.

Matching uses the ink inventory's color model (``tools.inks``): a spot by name (and tint when given), process or
RGB by components (with a tolerance), or registration. A real run is one undo step: bulk fills and outlines are
applied through ShapeRanges (thousands of shapes per COM call), text runs per character range, fountain stops
and effect colors per object. Afterwards the scope is walked again: every change is read back, and anything
that still matches is reported with the reason it could not change.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from core import colorspec
from core.connection import get_connection
from core.models import ToolResult
from core.structure import CONTAINER_TYPES, StaleStructureError, invalidate, locate_entries, page_layers, shape_range
from core.targets import resolve_document, resolve_page
from core.units import unit_name
from tools import inks, inspection
from tools.colors import (
    _characters,
    _com_shape,
    _command_group,
    _fill_state,
    _outline_range,
    _outline_state,
    _refusal,
)

Part = Literal["fill", "outline", "text", "fountain_stops", "effects"]
_PARTS = ("fill", "outline", "text", "fountain_stops", "effects")
_TEXT, _BITMAP = 6, 5
_UNIFORM, _FOUNTAIN = 1, 2
_BATCH = 2000
_MAX_CHARACTERS = 4000
_EFFECT_PROPERTIES = {6: ("FillColor", "OutlineColor", "FillColorTo"), 5: ("Color",)}
_EFFECT_NAMES = {6: "contour", 5: "drop_shadow"}
_EFFECTS_READ = ("array($item.Effects.Item({i}).Type, iif($item.Effects.Item({i}).Type = 6, array("
                 + ", ".join(inks._color(f"$item.Effects.Item({{i}}).Contour.{name}") for name in _EFFECT_PROPERTIES[6])
                 + "), iif($item.Effects.Item({i}).Type = 5, array("
                 + inks._color("$item.Effects.Item({i}).DropShadow.Color") + "), 0)))")


@dataclass
class _Use:
    """One color use: an object's part (fill, outline, a text run, a fountain stop, an effect color)."""

    page: int
    view: object
    entry: object
    part: str  # fill | outline | fountain_stop | text_fill | text_outline | effect
    where: tuple = ()  # text: (start, end); fountain_stop: (index,); effect: (effect index, property)
    value: object = None  # queried color value

    @property
    def key(self):
        return (self.page if not self.view.master else 0, self.entry.shape_id, self.part, self.where)


@dataclass
class _Scan:
    uses: list = field(default_factory=list)
    characters: dict = field(default_factory=dict)  # (page, id) -> characters of text read per character
    skipped: list = field(default_factory=list)  # (page, view, entry, reason): text runs not read
    layers: set = field(default_factory=set)
    controls: dict = field(default_factory=dict)  # (view id, effect group ID) -> control shape ID


def _views(doc, pages, layers, fresh):
    """[(page index, view)] in scope; master layers once (on the first page in scope)."""
    seen_master, scoped = set(), []
    for page_index in pages:
        for view in page_layers(doc, resolve_page(doc, page_index), fresh=fresh):
            if layers is not None and view.name not in layers:
                continue
            if view.master:
                if view.name in seen_master:
                    continue
                seen_master.add(view.name)
            scoped.append((page_index, view))
    return scoped


def _scan(doc, pages, layers, parts, fresh=False) -> _Scan:
    scan = _Scan()
    for page_index, view in _views(doc, pages, layers, fresh):
        scan.layers.add(view.name)
        _scan_layer(scan, page_index, view, parts)
    return scan


def _scan_layer(scan, page_index, view, parts):
    entries = [entry for entry in view.structure.entries if entry.type not in CONTAINER_TYPES]
    plain = [entry for entry in entries if entry.type not in (_TEXT, _BITMAP)]
    texts = [entry for entry in entries if entry.type == _TEXT]
    add = scan.uses.append
    if {"fill", "outline", "fountain_stops"} & set(parts):
        for start in range(0, len(plain), _BATCH):
            batch = plain[start:start + _BATCH]
            for entry, row in zip(batch, inks._safe_read(view, batch, inks._PAINT), strict=True):
                if row is None:
                    continue
                (fill_type, fill), (outline_type, outline) = row
                if fill_type == _UNIFORM and "fill" in parts:
                    add(_Use(page_index, view, entry, "fill", (), fill))
                elif fill_type == _FOUNTAIN and fill and "fountain_stops" in parts:
                    items = ", ".join(f"$item.Fill.Fountain.Colors.Item({index})" for index in range(fill))
                    stops = inks._safe_read(view, [entry], [f"array({items}).convert({inks._color('$item.Color')})"])[0]
                    for index, stop in enumerate(stops[0] if stops else ()):
                        add(_Use(page_index, view, entry, "fountain_stop", (index,), stop))
                if outline_type and "outline" in parts:
                    add(_Use(page_index, view, entry, "outline", (), outline[1]))
    if "text" in parts and texts:
        stories = inks._safe_read(view, texts, inks._STORY)
        outlines = inks._safe_read(view, texts, [inks._STORY_OUTLINE])
        for entry, story, outline in zip(texts, stories, outlines, strict=True):
            if story is None:
                scan.skipped.append((page_index, view, entry, "text story unreadable"))
                continue
            count, _, fill_type, fill = story
            if not count:
                continue
            if fill_type == _UNIFORM and outline is not None:
                add(_Use(page_index, view, entry, "text_fill", (0, count), fill))
                if outline[0][0]:
                    add(_Use(page_index, view, entry, "text_outline", (0, count), outline[0][1][1]))
                continue
            if count > _MAX_CHARACTERS:
                scan.skipped.append((page_index, view, entry, f"mixed-color text of {count} characters (runs not read)"))
                continue
            chars = _characters(view, entry, count)
            scan.characters[(page_index, entry.shape_id)] = chars
            for part, runs in (("text_fill", _value_runs(chars, 1, 2, _UNIFORM)),
                               ("text_outline", _value_runs(chars, 3, 4, None))):
                for start, end, value in runs:
                    add(_Use(page_index, view, entry, part, (start, end), value))
    if "effects" in parts and any(entry.effect is not None for entry in view.structure.entries):
        links = inspection.effect_controls(view)
        scan.controls.update({(id(view), group): control for group, control in links.items()})
        controls = sorted(set(links.values()), key=int)
        position = view.structure.position
        for control in controls:
            entry = view.structure.entries[position[control]]
            count = inks._safe_read(view, [entry], ["$item.Effects.Count"])[0]
            if not count:
                continue
            reads = [_EFFECTS_READ.format(i=index) for index in range(1, count[0] + 1)]
            row = inks._safe_read(view, [entry], reads)[0]
            for index, (effect_type, colors) in enumerate(row or (), 1):
                for name, value in zip(_EFFECT_PROPERTIES.get(effect_type, ()), colors or (), strict=False):
                    add(_Use(page_index, view, entry, "effect", (index, name), value))


def _value_runs(chars, type_index, value_index, uniform):
    """[(start, end, color value)] runs of characters sharing one color (fill: uniform only; outline: any)."""
    runs = []
    for position, character in enumerate(chars):
        kind, details = character[type_index], character[value_index]
        if uniform is not None:
            value = details if kind == uniform else None
        else:
            value = details[1] if kind else None
        if runs and runs[-1][2] == value and runs[-1][1] == position:
            runs[-1][1] = position + 1
        else:
            runs.append([position, position + 1, value])
    return [(start, end, value) for start, end, value in runs if value is not None]


# --- planning -----------------------------------------------------------------------------------------------

def _reason(use, to_none, include_master):
    view, entry = use.view, use.entry
    if view.master and include_master:
        reason = None if view.editable else "on a locked layer"
    else:
        reason = _refusal(view, entry, bitmap_ok=False)
    if reason:
        return reason
    if to_none and use.part in ("fountain_stop", "effect"):
        return "a fountain stop or effect color cannot be none"
    return None


def _spec_of_identity(identity):
    kind = identity.get("kind")
    if kind == "spot":
        return colorspec.Spec("spot", name=identity["ink"], tint=identity.get("tint"))
    if kind == "process":
        return colorspec.Spec("cmyk", components=tuple(identity["components"]))
    if kind == "rgb":
        return colorspec.Spec("rgb", components=tuple(identity["components"]))
    if kind == "registration":
        return colorspec.Spec("registration")
    raise colorspec.ColorSpecError(f"The donor color ({identity.get('ink')}) cannot be matched")


def _donor(app, doc, pages, spec):
    """Resolve a donor spec on the first page in scope that holds the shape."""
    from tools.colors import _resolve_color

    last = None
    for page_index in pages:
        try:
            return _resolve_color(app, doc, resolve_page(doc, page_index), spec)
        except ValueError as exc:
            last = exc
    raise ValueError(f"Donor {spec.shape_id} was not found on the pages in scope ({last})")


class _Targets:
    """The new color per use: one resolved color per tint (spot to spot keeps each object's tint)."""

    def __init__(self, app, doc, pages, spec, from_spec):
        self.app, self.doc, self.spec = app, doc, spec
        self.carry_tint = spec.kind == "spot" and spec.tint is None and from_spec.kind == "spot"
        self.cache = {}
        self.fixed = _donor(app, doc, pages, spec) if spec.kind == "donor" else None
        self.info = self.fixed.info if self.fixed else {}

    def resolved(self, use):
        if self.fixed is not None:
            return self.fixed
        tint = None
        if self.carry_tint:
            key, description = inks._ink(use.value)
            tint = description.get("tint") if key[0] == "spot" else None
        tint_key = round(float(tint), 3) if tint is not None else None
        if tint_key not in self.cache:
            self.cache[tint_key] = colorspec.resolve(self.app, self.doc, self.spec) if self.spec.kind != "spot" \
                else _spot(self.app, self.doc, self.spec, tint)
            self.info = self.info or self.cache[tint_key].info
        return self.cache[tint_key]


def _spot(app, doc, spec, tint):
    color, info = colorspec.resolve_spot(app, doc, spec, tint)
    return colorspec.Resolved(spec, color, colorspec.identity(colorspec.value_of(color)), info)


def _label(use):
    view = use.view
    record = {"id": use.entry.shape_id, "layer": view.name, "page": use.page if not view.master else None,
              "part": use.part}
    if use.part in ("text_fill", "text_outline"):
        record["range"] = list(use.where)
    elif use.part == "fountain_stop":
        record["stop"] = use.where[0]
    elif use.part == "effect":
        record["effect"] = use.where[0]
        record["property"] = use.where[1]
    return record


def _group(items, max_ids):
    """[(use, old identity, new identity)] -> grouped changes {part, old, new, count, objects, ids}."""
    groups = {}
    for use, old, new in items:
        key = (use.part, str(old), str(new))
        group = groups.setdefault(key, {"part": use.part, "old": old, "new": new, "count": 0, "objects": set(),
                                        "ids": []})
        group["count"] += 1
        group["objects"].add(use.entry.shape_id)
        if use.part in ("fill", "outline"):
            if len(group["ids"]) < max_ids:
                group["ids"].append(use.entry.shape_id)
        elif len(group["ids"]) < max_ids:
            group["ids"].append(_label(use))
    result = []
    for group in groups.values():
        group["objects"] = len(group["objects"])
        if group["count"] > len(group["ids"]):
            group["ids_truncated"] = True
        result.append(group)
    return result


# --- applying -----------------------------------------------------------------------------------------------

def _apply(doc, plan):
    """Apply [(use, resolved)] inside the caller's command group."""
    bulk = defaultdict(list)  # (part, view id, new key) -> (view, resolved, [entries])
    text = defaultdict(list)
    for use, resolved in plan:
        if use.part in ("fill", "outline"):
            group = bulk.setdefault((use.part, id(use.view), str(resolved.identity)), (use.view, resolved, []))
            group[2].append(use.entry)
        elif use.part in ("text_fill", "text_outline"):
            text[(id(use.view), use.entry.shape_id)].append((use, resolved))
        elif use.part == "fountain_stop":
            shape = _com_shape(use.view, use.entry, use.entry.shape_id)
            shape.Fill.Fountain.Colors.Item(use.where[0]).Color = resolved.color
        elif use.part == "effect":
            shape = _com_shape(use.view, use.entry, use.entry.shape_id)
            effect = shape.Effects.Item(use.where[0])
            holder = effect.Contour if effect.Type == 6 else effect.DropShadow
            setattr(holder, use.where[1], resolved.color)
    for (part, _, _), (view, resolved, entries) in bulk.items():
        unique = list({entry.shape_id: entry for entry in entries}.values())
        for start in range(0, len(unique), 20000):
            rng = shape_range(view, unique[start:start + 20000])
            if part == "fill":
                if resolved.none:
                    rng.ApplyNoFill()
                else:
                    rng.ApplyUniformFill(resolved.color)
            elif resolved.none:
                for index in range(1, rng.Count + 1):
                    rng.Item(index).Outline.SetNoOutline()
            else:
                _outline_range(rng, -1.0, resolved.color)
    for items in text.values():
        use = items[0][0]
        story = _com_shape(use.view, use.entry, use.entry.shape_id).Text.Story
        for use, resolved in items:
            start, end = use.where
            target = story.Range(start, end)
            if use.part == "text_fill":
                if resolved.none:
                    target.Fill.ApplyNoFill()
                else:
                    target.Fill.ApplyUniformFill(resolved.color)
            elif resolved.none:
                target.Outline.SetNoOutline()
            else:
                target.Outline.Color = resolved.color


def _verify(doc, pages, layers, parts, before, plan, matcher, unit):
    """Walk the scope again; return (after scan, verification errors)."""
    after = _scan(doc, pages, layers, parts, fresh=True)
    found = defaultdict(list)
    for use in after.uses:
        found[(use.page if not use.view.master else 0, use.entry.shape_id, use.part)].append(use)
    problems = []
    text_changes = defaultdict(list)
    for use, resolved in plan:
        if use.part in ("text_fill", "text_outline"):
            text_changes[(use.page, use.entry.shape_id)].append((use, resolved))
            continue
        candidates = [u for u in found[(use.key[0], use.entry.shape_id, use.part)] if u.where == use.where]
        actual = colorspec.identity(candidates[0].value) if candidates else dict(colorspec.NONE)
        if use.part == "effect" and not candidates:
            actual = {"kind": "unreadable"}
        if not colorspec.same(actual, resolved.identity):
            problems.append({**_label(use), "expected": resolved.identity, "actual": actual})
    for (page_index, shape_id), items in text_changes.items():
        use = items[0][0]
        # Characters read per character before the edit (mixed runs); None when the story had one fill/outline.
        old_chars = before.characters.get((page_index, shape_id))
        new_chars = after.characters.get((page_index, shape_id))
        if new_chars is None:
            count = use.where[1] if old_chars is None else len(old_chars)
            new_chars = _characters(*_fresh_entry(doc, page_index, use), count)
        changed = set()
        for change, resolved in items:
            start, end = change.where
            index = 2 if change.part == "text_fill" else 4
            for position in range(start, min(end, len(new_chars))):
                changed.add((position, index))
                character = new_chars[position]
                if change.part == "text_fill":
                    actual = _fill_state((character[1], character[2]))
                else:
                    state = _outline_state((character[3], character[4]), unit)
                    actual = state.get("color", state)
                if not colorspec.same(actual, resolved.identity):
                    problems.append({**_label(change), "position": position, "expected": resolved.identity,
                                     "actual": actual})
                    break
        if old_chars is not None:
            texts_ok = [c[0] for c in old_chars] == [c[0] for c in new_chars]
            others = [(p, i) for p in range(len(old_chars)) for i in (2, 4) if (p, i) not in changed]
            moved = [p for p, i in others if p < len(new_chars) and
                     (old_chars[p][i - 1], old_chars[p][i]) != (new_chars[p][i - 1], new_chars[p][i])]
            if not texts_ok or moved:
                problems.append({"id": shape_id, "part": "text", "problem": "text or characters outside the changed "
                                 f"runs changed (positions {moved[:10]})"})
    remaining = [use for use in after.uses if matcher.matches(use.value)]
    return after, problems, remaining


def _fresh_entry(doc, page_index, use):
    found = locate_entries(doc, resolve_page(doc, page_index), [use.entry.shape_id])
    return found[use.entry.shape_id]


# --- tool ---------------------------------------------------------------------------------------------------

def replace_color(
    document_path: str,
    from_color: str | dict,
    to_color: str | dict,
    pages: list[int] | None = None,
    parts: list[Part] | None = None,
    layers: list[str] | None = None,
    tolerance: float = 0.0,
    include_master_layers: bool = False,
    dry_run: bool = True,
    max_ids: int = 200,
) -> ToolResult:
    """Replace one color with another on every matching object part in a scope. Dry run by default.

    from_color / to_color use the set_fill color specs (spot name with optional tint and palette, CMYK, RGB,
    registration, donor {"from_shape_id", "part"}; to_color may also be "none"). Spots match by name ignoring
    the PANTONE prefix, case and punctuation, at any tint unless from_color gives one; a spot replaced by a
    spot without a tint keeps each object's tint. CMYK/RGB match by components within tolerance.
    pages: one-based (default all); layers: exact names (default all); parts: fill, outline, text (fill and
    outline of each character run; other runs keep their colors), fountain_stops, effects (contour fill/
    outline/fill-to and drop-shadow colors; the generated objects follow). Master layers change only with
    include_master_layers (they print on every page).
    dry_run=True changes nothing and lists exactly what would change, grouped by part, old and new color
    (count exact, up to max_ids IDs per group; text runs with ranges), and what cannot change and why (locked
    or master layer, effect-generated objects, unread long text). dry_run=False applies everything as one
    undo step, walks the scope again, verifies every change (verification_errors) and lists anything that
    still matches (remaining).
    """
    started = time.monotonic()
    wanted_parts = list(dict.fromkeys(parts)) if parts else list(_PARTS)
    if any(part not in _PARTS for part in wanted_parts):
        return ToolResult.fail(f"parts must be among {', '.join(_PARTS)}")
    if not 0 <= max_ids <= 100000:
        return ToolResult.fail("max_ids must be between 0 and 100000")
    try:
        from_spec = colorspec.parse(from_color)
        to_spec = colorspec.parse(to_color)
        if from_spec.kind == "none":
            raise colorspec.ColorSpecError("from_color cannot be none")
        if from_spec.kind != "donor":
            colorspec.Matcher(from_spec, tolerance)
    except colorspec.ColorSpecError as exc:
        return ToolResult.fail(str(exc))
    conn = get_connection()

    def run():
        app = conn.app
        doc, actual_path = resolve_document(app, document_path, activate=not dry_run)
        page_count = doc.Pages.Count
        scope = list(range(1, page_count + 1)) if pages is None else list(dict.fromkeys(pages))
        if not scope or any(not isinstance(index, int) or not 1 <= index <= page_count for index in scope):
            raise ValueError(f"pages must be page numbers between 1 and {page_count}")
        source = from_spec
        if from_spec.kind == "donor":
            source = _spec_of_identity(_donor(app, doc, scope, from_spec).identity)
        matcher = colorspec.Matcher(source, tolerance)
        unit = doc.Unit
        try:
            for fresh in (False, True):
                try:
                    before = _scan(doc, scope, layers, wanted_parts, fresh=fresh)
                    break
                except StaleStructureError:
                    if fresh:
                        raise
            if layers is not None and (missing := set(layers) - before.layers):
                raise ValueError(f"No layer named {', '.join(sorted(missing))} on the pages in scope")
            matched = [use for use in before.uses if matcher.matches(use.value)]
            targets = _Targets(app, doc, scope, to_spec, source)
            plan, refused, generated = [], defaultdict(list), []
            for use in matched:
                reason = _reason(use, to_spec.kind == "none", include_master_layers)
                if reason:
                    (generated if use.entry.effect is not None else refused[reason]).append(use)
                    continue
                resolved = targets.resolved(use)
                if colorspec.same(colorspec.identity(use.value), resolved.identity):
                    continue  # already the new color
                plan.append((use, resolved))
            # Effect-generated objects follow their effect: covered when the control's effect color changes.
            changed_controls = {(id(use.view), use.entry.shape_id) for use, _ in plan if use.part == "effect"}
            follows = []
            for use in generated:
                control = before.controls.get((id(use.view), use.entry.effect))
                if control is not None and (id(use.view), control) in changed_controls:
                    follows.append({**_label(use), "effect_control_id": control})
                else:
                    refused[_reason(use, False, include_master_layers) + _effect_hint(use, before, wanted_parts)].append(use)
            for page_index, view, entry, reason in before.skipped:
                refused[reason].append(_Use(page_index, view, entry, "text"))
            report = {
                "document_path": actual_path, "document_unit": unit_name(unit), "dry_run": dry_run,
                "pages": scope, "parts": wanted_parts, "from": source.label(),
                "matched_inks": sorted({colorspec.identity(use.value)["ink"] for use in matched}),
                "to": _to_label(targets, plan, to_spec),
                **({"palette": targets.info} if targets.info else {}),
                "summary": {"changes": len(plan), "objects": len({(u.key[0], u.entry.shape_id) for u, _ in plan}),
                            **{part: n for part, n in _count_parts(plan).items()},
                            "not_changed": sum(len(uses) for uses in refused.values())},
                "changes": _group([(use, colorspec.identity(use.value), resolved.identity) for use, resolved in plan],
                                  max_ids),
                "follows_effect": follows[:max_ids],
                "not_changed": [{"reason": reason, "count": len(uses),
                                 "ids": [_label(use) for use in uses[:max_ids]]} for reason, uses in refused.items()],
            }
            if dry_run or not plan:
                report["seconds"] = round(time.monotonic() - started, 2)
                return report
            try:
                with _command_group(doc, "MCP replace_color"):
                    _apply(doc, plan)
            except Exception as exc:
                raise RuntimeError(f"{exc}. Some changes may have been applied: they form one undo step "
                                   "(call undo once), then inspect before retrying") from exc
            invalidate(actual_path)
            _, problems, remaining = _verify(doc, scope, layers, wanted_parts, before, plan, matcher, unit)
            report["verified"] = len(plan) - len(problems)
            report["remaining"] = {"count": len(remaining), "ids": [_label(use) for use in remaining[:max_ids]]}
            if problems:
                report["verification_errors"] = problems[:max(max_ids, 20)]
            report["seconds"] = round(time.monotonic() - started, 2)
            return report
        finally:
            if not dry_run:
                invalidate(actual_path)

    result = conn.read_call(run) if dry_run else conn.safe_call(run)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if data.get("verification_errors"):
        return ToolResult(success=False, data=data, error=f"Read-back differs on {len(data['verification_errors'])} "
                                                          "changes; the replacement is one undo step (undo)")
    return ToolResult.ok(**data)


def _effect_hint(use, scan, parts):
    if "effects" not in parts:
        return " (add effects to parts to change effect colors)"
    control = scan.controls.get((id(use.view), use.entry.effect))
    if control is None:
        return " (blend, extrude or other effect: not linked to a control shape)"
    return f" (control {control}; its effect colors do not match)"


def _to_label(targets, plan, to_spec):
    if targets.fixed is not None:
        return targets.fixed.identity
    if plan:
        identity = dict(plan[0][1].identity)
        if targets.carry_tint:
            identity["tint"] = "each object's tint"
        return identity
    return {"ink": to_spec.label(), "kind": to_spec.kind}


def _count_parts(plan):
    counts = defaultdict(int)
    for use, _ in plan:
        counts[use.part] += 1
    return dict(counts)
