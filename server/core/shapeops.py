"""Shared machinery for the geometry and effect edits: one undo step with rollback, layers, geometry read-back.

* ``undoable(doc, page, label)``: one CorelDRAW command group. On any failure inside it, the group is closed and
  undone (``Document.Undo(1)``; an empty command group is also one undo step, so this never undoes an earlier
  edit) and the page's StaticIDs are compared with the state before. The document must be active (Undo follows
  the active document; ``core.targets.resolve_document(..., activate=True)``).
* ``optimized(app)``: Optimization on and events off for a batch of edits, restored (and the view refreshed) after.
* ``page_ids(doc, page)``: every StaticID on the page's layers (fresh walk).
* ``target_layer(page, name)``: an ordinary, editable layer local to the page, by exact name.
* ``geometry(shape, unit)``: area with holes subtracted, perimeter, subpath count and bounds of a shape's
  display curve. ``Curve.Area`` adds the area of every subpath (a 4x4 square with a 2x2 hole reads 20, not 12),
  so holes are found by nesting: a subpath inside an odd number of other subpaths is a hole.
* ``refusal(view, entry, ...)``: why an object cannot take part in an edit, or None. ``nested_refusals(targets)``:
  targets inside a group (or PowerClip) that is itself a target.
* ``EDIT_BUDGET_SECONDS`` / ``Budget``: long multi-command edits stop (and are undone by ``undoable``) before the
  bridge's 60 s cancel.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from core.structure import CONTAINER_TYPES, EFFECT_GROUP_TYPES, page_layers
from core.units import convert, unit_name

#: Shape types with a vector outline: rectangle, ellipse, curve, polygon, text, custom and perfect shapes.
VECTOR_TYPES = frozenset({1, 2, 3, 4, 6, 21, 26})
TEXT, BITMAP, GROUP = 6, 5, 7
ANY_TYPE = range(0, 1000)  # ``refusal(types=ANY_TYPE)``: every shape type (groups, effects, clips still checked)
#: Edits made of many CorelDRAW commands stop at this age and are undone (the bridge cancels at 60 s).
EDIT_BUDGET_SECONDS = 35.0
_GEOMETRY = ("array(@com.DisplayCurve.Length, @com.DisplayCurve.SubPaths.toarray().convert(array($item.Area, "
             "$item.PositionX, $item.PositionY, $item.SizeWidth, $item.SizeHeight, $item.StartNode.PositionX, "
             "$item.StartNode.PositionY)), @com.LeftX, @com.BottomY, @com.RightX, @com.TopY)")


class RolledBackError(RuntimeError):
    """An edit failed part way and was undone (the message says whether the page is back to its old state)."""


def page_ids(doc, page) -> set[str]:
    """StaticIDs of every shape on the page's layers (master layers included), from a fresh walk."""
    return {entry.shape_id for view in page_layers(doc, page, fresh=True) for entry in view.structure.entries}


class _Step:
    def __init__(self, before):
        self.before = before


@contextmanager
def optimized(app):
    """CorelDRAW's Optimization mode (no screen updates or events) for many edits; always restored and refreshed."""
    previous = (bool(app.Optimization), bool(app.EventsEnabled))
    app.Optimization, app.EventsEnabled = True, False
    try:
        yield
    finally:
        app.Optimization, app.EventsEnabled = previous
        if not previous[0]:
            app.Refresh()


@contextmanager
def undoable(doc, page, label):
    """One undo step; on failure inside the block, undo it and check that the page's IDs are as before."""
    step = _Step(page_ids(doc, page))
    doc.BeginCommandGroup(label)
    try:
        yield step
    except BaseException as exc:
        doc.EndCommandGroup()
        doc.Undo(1)
        after = page_ids(doc, page)
        if after != step.before:
            extra, missing = sorted(after - step.before), sorted(step.before - after)
            raise RolledBackError(f"{exc}. The partial edit was undone, but the page differs (new IDs {extra[:10]}, "
                                  f"missing IDs {missing[:10]}); inspect it") from exc
        raise RolledBackError(f"{exc}. The partial edit was undone; the page is unchanged") from exc
    doc.EndCommandGroup()


def target_layer(page, name):
    """The page's own ordinary layer with exactly this name (editable), as a COM layer."""
    matches = [layer for layer in page.Layers if layer.Name == name]
    if not matches:
        names = [layer.Name for layer in page.Layers if not layer.IsSpecialLayer]
        raise ValueError(f"No layer named {name!r} on page {page.Index}; layers: {names}")
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} layers are named {name!r} on page {page.Index}")
    layer = matches[0]
    if layer.Master or layer.IsSpecialLayer:
        raise ValueError(f"Layer {name!r} is a master or special layer; pass a layer of the page itself")
    if not layer.Editable:
        raise ValueError(f"Layer {name!r} is locked; unlock it first (set_layer_properties editable=true)")
    return layer


def refusal(view, entry, *, consume: bool, groups: bool = False, effects: bool = False, bitmaps: bool = False,
            types=VECTOR_TYPES) -> str | None:
    """Why an object cannot take part, or None.

    consume: the edit deletes or replaces the object (it must be editable and not generated by an effect).
    groups: groups (and effect groups, when ``effects``) are accepted. effects: objects generated by an effect
    are accepted (they are only copied).
    """
    if view.master:
        return "on a master layer (shared by every page)"
    if consume and not view.editable:
        return "on a locked layer"
    if entry.clip is not None:
        return f"inside a PowerClip (container {entry.clip}); extract it first"
    if entry.effect is not None and (consume or not effects):
        return f"generated by an effect (effect group {entry.effect}); use its control shape or separate_effect"
    if entry.type in EFFECT_GROUP_TYPES:
        if consume or not (groups and effects):
            return "an effect group; pass its control shape, or separate_effect first"
        return None
    if entry.type == GROUP:
        return None if groups else "a group; pass its member shapes (inspect_shapes parent_id)"
    if entry.type == BITMAP:
        return None if bitmaps else "a bitmap (no vector outline)"
    if entry.type not in types:
        return f"shape type {entry.type} is not supported here"
    return None


def nested_refusals(targets) -> dict:
    """{id: reason} for targets inside a group or PowerClip that is also a target (the edit would apply twice)."""
    out = {}
    for shape_id, (view, entry) in targets.items():
        for other_id, (other_view, other) in targets.items():
            inside = len(other.path) < len(entry.path) and entry.path[:len(other.path)] == other.path
            if other_id != shape_id and other_view is view and inside:
                out[shape_id] = f"inside {other_id}, which is also a target"
                break
    return out


class Budget:
    """Raises TimeoutError once ``seconds`` have passed (inside ``undoable`` this undoes the edit)."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.deadline = time.perf_counter() + seconds

    def check(self, done: int, total: int, stage: str) -> None:
        if time.perf_counter() > self.deadline:
            raise TimeoutError(f"Stopped after {self.seconds:.0f} s at {stage} ({done} of {total}) to stay under the "
                               "bridge's 60 s limit; split shape_ids into smaller calls")


def holds_powerclip(view, entry) -> bool:
    return any(path == entry.path and clips for path, _, _, clips in view.structure.groups)


def descendants(view, entry):
    """Structure entries below a group in stacking order (the group's subtree)."""
    size = len(entry.path)
    start = view.structure.position[entry.shape_id] + 1
    out = []
    for item in view.structure.entries[start:]:
        if len(item.path) <= size or item.path[:size] != entry.path:
            break
        out.append(item)
    return out


def geometry(shape, document_unit, app=None) -> dict:
    """Area (holes subtracted), perimeter, subpaths and bounds of a shape's display curve, in document units."""
    length, subpaths, left, bottom, right, top = shape.Evaluate(_GEOMETRY)
    subpaths = [tuple(item) for item in subpaths or ()]
    boxes = [(x, y - h, x + w, y) for _, x, y, w, h, _, _ in subpaths]
    area, holes = 0.0, 0
    curve = None
    for index, (sub_area, *_rest, px, py) in enumerate(subpaths):
        depth = 0
        for other, (other_area, *_r) in enumerate(subpaths):
            if other == index or other_area <= sub_area:
                continue
            a, b = boxes[index], boxes[other]
            if a[0] >= b[0] - 1e-9 and a[1] >= b[1] - 1e-9 and a[2] <= b[2] + 1e-9 and a[3] <= b[3] + 1e-9:
                if curve is None:
                    curve = shape.DisplayCurve
                if curve.SubPaths.Item(other + 1).IsPointInside(px, py):
                    depth += 1
        if depth % 2:
            holes += 1
            area -= sub_area
        else:
            area += sub_area
    unit = unit_name(document_unit)
    return {"area": area, "perimeter": length, "subpaths": len(subpaths), "holes": holes,
            "bounds": [left, bottom, right, top], "unit": unit}


def rounded(data: dict, digits=6) -> dict:
    out = {}
    for key, value in data.items():
        if isinstance(value, float):
            out[key] = round(value, digits)
        elif isinstance(value, list):
            out[key] = [round(item, digits) if isinstance(item, float) else item for item in value]
        else:
            out[key] = value
    return out


def to_unit(value, document_unit, unit, power=1):
    """A length (power 1) or area (power 2) from document units to ``unit``."""
    return value * convert(1.0, document_unit, unit) ** power


def is_container(entry) -> bool:
    return entry.type in CONTAINER_TYPES
