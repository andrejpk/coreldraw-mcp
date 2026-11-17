"""Page structure cache: every shape's StaticID, type, index path and parent on a page's layers.

Why. Two costs dominate reads of a large drawing. Every COM access to a ``Shapes`` collection costs about
13 ms on a large document, while a whole ``Shape.Evaluate`` (CorelDRAW query language, CQL) call costs about
1 ms. And a CQL path into a large group is slow: CorelDRAW walks the group's child list for ``Shapes.Count``
and ``Shapes.Item(k)``, so on a 16,000-child group each ``...Shapes.Item(1).Shapes.Item(k)`` costs 0.3-0.5 ms,
and a record that repeats the path for every property costs 10-15 ms. So this module:

* reads through one anchor shape per page (any shape of ``page.Shapes``) with paths that start at
  ``@com.Page.AllLayers.Item(ordinal)``, so all layers are read in the same few CQL calls;
* reads siblings in blocks of up to 256: one ``Item`` resolution for the block's first shape, then
  ``Next(0, false, false)`` (next sibling) chains combined with ``array(...).convert(...)``, which binds each
  shape to ``$item`` so that its properties are read without repeating the path;
* caches the walked structure between calls and validates it cheaply on every call.

Cache API (used by ``inspect_shapes``, the exact-ID tools and ``list_all_text_shapes``):

``page_layers(doc, page, layer_name=None, fresh=False) -> list[LayerView]``
    Every layer of ``page.AllLayers``, in order (or only the layers with that exact name), each with its
    ``LayerStructure``. An empty page gives empty structures.
``LayerView``
    ``ordinal`` (one-based index in ``page.AllLayers``), ``name``, ``master``, ``visible``, ``printable``,
    ``structure``, ``details()`` (the ``inspect_shapes`` layer dict) and ``layer`` (the COM layer, fetched on
    first use). COM objects are valid only during the current bridge operation.
``LayerStructure``
    ``entries``: ``Entry(path, parent, shape_id, parent_id, type)`` in the order of
    ``core.drawing.walk_shapes`` (stacking order, each group followed by its descendants). ``path`` is the
    tuple of one-based ``Shapes.Item`` indices from the layer (a negative index ``-k`` is
    ``PowerClip.Shapes.Item(k)``, a PowerClip content) and ``parent`` is the parent's path (None at the top
    level). IDs are strings. ``clip`` is the StaticID of the innermost PowerClip container that holds the shape
    (None outside PowerClips); ``effect`` is the StaticID of the effect group (contour, drop shadow, blend,
    extrude, bevel...) whose generated objects include the shape. ``position`` maps a shape ID to its index in
    ``entries``; ``groups`` lists ``(path, shape_id, child_count, powerclip_count)`` for every shape with
    children. Groups, effect groups and PowerClip contents are all walked.
``read_entries(view, entries, expressions) -> list[tuple]``
    One batched CQL read for many entries of one layer. ``expressions`` use ``$item`` for the shape, for
    example ``["$item.Fill.Type", "$item.Outline.Width"]``. Returns one tuple per entry, in order, after
    checking that each path still holds the cached StaticID; raises ``StaleStructureError`` when it does not.
    CorelDRAW errors (an unreadable property anywhere in the batch) pass through, so callers can fall back
    to smaller reads.
``resolve_shapes(doc, page, shape_ids) -> list[Shape]``
    COM shape objects for exact IDs on the page's layers, through the cache, each StaticID verified. A miss
    or a mismatch forces a fresh walk before it fails.
``walk_layer(layer)``, ``layer_view(layer, structure)``
    An uncached structure of one COM layer (for verification after an edit), and a view of that layer for
    ``read_entries`` without a page walk.
``invalidate(document_path=None)``, ``stats()``
    Drop cached pages; read hit, walk and stale counters.

Validity. A cached page is reused only when all of these hold:

* the bridge ``generation`` is unchanged: no operation that may edit a document ran through this server
  since the walk (every ``safe_call`` counts; reads use ``read_call``);
* every layer has the same name and top-level shape count at the same position in ``page.AllLayers``;
* the page's recursive shape count (``Page.Shapes.FindShapes`` with Recursive) is unchanged, and no shape on
  it has a StaticID above the largest one walked (new shapes, including pasted, duplicated and newly grouped
  ones, get new, higher IDs; an undone delete restores an old ID and changes the count);
* every cached group path still holds the same group ID with the same child count;
* every path read through ``read_entries`` or ``resolve_shapes`` still holds the cached StaticID.

Together these catch edits made in the CorelDRAW UI or by another client, which bypass the server. Pages
with many groups relative to their shapes are walked again instead of being verified group by group.
Callers retry once with ``fresh=True`` after ``StaleStructureError``.
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import NamedTuple

from core.connection import get_connection
from core.drawing import CDR_GROUP_SHAPE
from core.targets import document_identity, normalize_path

if sys.platform == "win32":
    from pythoncom import Empty
else:  # CorelDRAW automation is Windows only
    Empty = None

_NEXT = ".Next(0, false, false)"  # the next sibling in the same parent; null after the last
_MAX_BLOCK_POWER = 8  # blocks of up to 256 siblings: ~m/2 Next steps per shape, one Item resolution per block
_MAX_ITEMS_PER_QUERY = 4096  # shapes per Shape.Evaluate call (each call stays well under a second)
_MAX_PAGES = 16
#: Effect groups: their ``Shapes`` are the objects CorelDRAW generated for the effect (blend 10, extrude 11,
#: contour 13, bevel 15, drop shadow 16, artistic media 18, custom effect 22).
EFFECT_GROUP_TYPES = frozenset({10, 11, 13, 15, 16, 18, 22})
#: Shape types whose ``Shapes`` hold children (groups and effect groups). They have no fill or outline of their own.
CONTAINER_TYPES = EFFECT_GROUP_TYPES | {CDR_GROUP_SHAPE}
_HAS_CHILDREN = " or ".join(f"$item.Type = {kind}" for kind in sorted(CONTAINER_TYPES))
_CHILD_COUNT = f"iif({_HAS_CHILDREN}, $item.Shapes.Count, 0)"
_CLIP_COUNT = "iif($item.PowerClip.Shapes.Count > 0, $item.PowerClip.Shapes.Count, 0)"  # null without a PowerClip
_WALK_LEAF = f"array($item.StaticID, $item.Type, {_CHILD_COUNT}, {_CLIP_COUNT})"
_GROUP_LEAF = f"array($item.StaticID, {_CHILD_COUNT}, {_CLIP_COUNT})"
_LAYER_LEAF = "array($item.Name, $item.Shapes.Count, $item.Master, $item.Visible, $item.Printable, $item.Editable)"


class StaleStructureError(RuntimeError):
    """The page changed since its structure was read; walk it again."""


class Entry(NamedTuple):
    path: tuple
    parent: tuple | None
    shape_id: str
    parent_id: str | None
    type: int
    clip: str | None = None  # innermost PowerClip container holding this shape
    effect: str | None = None  # effect group whose generated objects include this shape


@dataclass
class LayerStructure:
    entries: list[Entry]
    groups: list[tuple[tuple, str, int, int]]  # (path, shape ID, child count, PowerClip content count)
    position: dict[str, int] = field(default_factory=dict)
    duplicates: set[str] = field(default_factory=set)

    def __post_init__(self):
        for index, entry in enumerate(self.entries):
            if entry.shape_id in self.position:
                self.duplicates.add(entry.shape_id)
            self.position.setdefault(entry.shape_id, index)


@dataclass
class LayerView:
    page: object
    anchor: object
    ordinal: int | None  # None: paths start at the anchor's own layer
    name: str
    master: bool
    visible: bool
    printable: bool
    structure: LayerStructure
    _layer: object = None
    editable: bool = True

    @property
    def layer(self):
        if self._layer is None:
            self._layer = self.page.AllLayers.Item(self.ordinal)
        return self._layer

    def details(self) -> dict:
        return {"name": self.name, "master": bool(self.master), "visible": bool(self.visible),
                "printable": bool(self.printable), "locked": not self.editable}


@dataclass
class _PageEntry:
    generation: int
    layers: list  # [(name, top-level count)]
    recursive_count: int
    max_id: int
    structures: list  # LayerStructure per layer, in AllLayers order


_cache: OrderedDict = OrderedDict()
_stats = {"hits": 0, "walks": 0, "stale": 0, "invalidations": 0}


def stats() -> dict:
    """Counters since the server started: cache hits, page walks, stale detections, invalidations."""
    return dict(_stats, pages=len(_cache))


def invalidate(document_path: str | None = None) -> None:
    """Forget cached structures (all, or those of one document)."""
    _stats["invalidations"] += 1
    if document_path is None:
        _cache.clear()
        return
    wanted = normalize_path(document_path)
    for key in [key for key in _cache if key[0] == wanted]:
        del _cache[key]


# --- CQL helpers ---------------------------------------------------------------------------------------------

def _step(index) -> str:
    return f"PowerClip.Shapes.Item({-index})" if index < 0 else f"Shapes.Item({index})"


def query_path(path, ordinal: int | None = None) -> str:
    """CQL for the shape at an index path: from a page anchor on layer ``ordinal``, else from a shape on the layer.

    A negative index ``-k`` steps into PowerClip contents (``PowerClip.Shapes.Item(k)``).
    """
    start = "@com.Layer." if ordinal is None else f"@com.Page.AllLayers.Item({ordinal})."
    return start + ".".join(_step(index) for index in path)


def evaluate(anchor, expressions):
    """Evaluate many CQL expressions in one call; ``anchor`` is the object the paths start from."""
    values = anchor.Evaluate("array(" + ", ".join(expressions) + ")")
    if len(values) != len(expressions):
        raise RuntimeError("CorelDRAW returned an incomplete query result")
    return values


def _block_query(start, power, leaf):
    """CQL that applies ``leaf`` to 2**power consecutive siblings, starting with the shape at CQL ``start``."""
    expression = leaf
    for level in range(1, power + 1):
        expression = f"array($item, $item{_NEXT * (1 << (level - 1))}).convert({expression})"
    return f"array({start}).convert({expression})"


def _flatten(value, depth, out):
    if depth == 0:
        out.append(value)
        return
    for item in value:
        _flatten(item, depth - 1, out)


def _blocks(first, count):
    """Split siblings first..first+count-1 into power-of-two blocks of at most 2**_MAX_BLOCK_POWER."""
    while count:
        power = min(count.bit_length() - 1, _MAX_BLOCK_POWER)
        yield first, power
        first += 1 << power
        count -= 1 << power


def _read_runs(anchor, runs, leaf):
    """Apply ``leaf`` to runs of consecutive siblings [(ordinal, parent_path, first_index, count, clip)], in order.

    ``clip`` marks a run of PowerClip contents (path index ``-k``).
    """
    blocks = [(ordinal, parent, -start if clip else start, power) for ordinal, parent, first, count, clip in runs
              for start, power in _blocks(first, count)]
    values, batch, size = [], [], 0

    def flush():
        if batch:
            result = evaluate(anchor, [_block_query(query_path((*parent, start), ordinal), power, leaf)
                                       for ordinal, parent, start, power in batch])
            for (_, _, _, power), block in zip(batch, result, strict=True):
                _flatten(block[0], power, values)
            batch.clear()

    for block in blocks:
        if size + (1 << block[3]) > _MAX_ITEMS_PER_QUERY:
            flush()
            size = 0
        batch.append(block)
        size += 1 << block[3]
    flush()
    return values


def _runs(paths):
    """Group index paths into runs of consecutive siblings: [(parent, first, count, clip)]."""
    runs = []
    for parent, clip, index in sorted((path[:-1], path[-1] < 0, abs(path[-1])) for path in paths):
        if runs and runs[-1][0] == parent and runs[-1][3] == clip and runs[-1][1] + runs[-1][2] == index:
            runs[-1][2] += 1
        else:
            runs.append([parent, index, 1, clip])
    return [tuple(run) for run in runs]


# --- walking and validation ----------------------------------------------------------------------------------

def _walk(anchor, tops):
    """Walk layers [(ordinal, top-level count)] from an anchor, breadth first: {ordinal: LayerStructure}.

    An ordinal of None means the anchor's own layer.
    """
    _stats["walks"] += 1
    children = {}  # (ordinal, parent path) -> [(path index, StaticID, type, child count, PowerClip count)]
    pending = [(ordinal, (), count, False) for ordinal, count in tops if count]
    while pending:
        values = _read_runs(anchor, [(ordinal, parent, 1, count, clip) for ordinal, parent, count, clip in pending],
                            _WALK_LEAF)
        position, following = 0, []
        for ordinal, parent, count, clip in pending:
            kids = values[position:position + count]
            position += count
            for index, (static_id, kind, size, clips) in enumerate(kids, 1):
                path_index = -index if clip else index
                children.setdefault((ordinal, parent), []).append((path_index, static_id, kind, size or 0, clips or 0))
                if size:
                    following.append((ordinal, (*parent, path_index), size, False))
                if clips:
                    following.append((ordinal, (*parent, path_index), clips, True))
        pending = following

    structures = {}
    for ordinal, _ in tops:
        entries, groups = [], []

        def visit(parent, parent_id, clip_id, effect_id, ordinal=ordinal, entries=entries, groups=groups):
            # Each shape is followed by its children, then by its PowerClip contents.
            kids = sorted(children.get((ordinal, parent), ()), key=lambda kid: (kid[0] < 0, abs(kid[0])))
            for index, static_id, kind, size, clips in kids:
                path, shape_id = (*parent, index), str(static_id)
                entries.append(Entry(path, parent or None, shape_id, parent_id, kind, clip_id, effect_id))
                if size or clips:
                    groups.append((path, shape_id, size, clips))
                if size:
                    visit(path, shape_id, clip_id, shape_id if kind in EFFECT_GROUP_TYPES else effect_id)
                if clips:
                    visit(path, shape_id, shape_id, effect_id)

        visit((), None, None, None)
        structures[ordinal] = LayerStructure(entries, groups)
    return structures


def layer_view(layer, structure: LayerStructure | None = None) -> LayerView:
    """A view of one COM layer (paths relative to its first shape) for reading entries without a page walk."""
    return LayerView(layer.Page, layer.Shapes.Item(1), None, layer.Name, layer.Master, layer.Visible,
                     layer.Printable, structure, layer, layer.Editable)


def walk_layer(layer) -> LayerStructure:
    """An uncached structure of one COM layer."""
    shapes = layer.Shapes
    count = shapes.Count
    if not count:
        return LayerStructure([], [])
    return _walk(shapes.Item(1), [(None, count)])[None]


class _PageState(NamedTuple):
    shapes: object  # page.Shapes (COM)
    anchor: object  # a shape on one of the page's own layers, or None
    layers: list  # [(name, top-level count, master, visible, printable, editable)] in AllLayers order


def _local_anchor(shapes):
    """A shape on one of the page's own layers. Page.Shapes lists master layers too, and from a master-layer
    shape ``@com.Page`` is not this page."""
    count = shapes.Count
    if not count:
        return None
    last = shapes.Last  # the page's own layers follow the master layers in Page.Shapes
    if not last.Evaluate("@com.Layer.Master"):
        return last
    for index in range(1, count + 1):
        candidate = shapes.Item(index)
        if not candidate.Evaluate("@com.Layer.Master"):
            return candidate
    return None


def _page_state(page) -> _PageState:
    shapes = page.Shapes
    anchor = _local_anchor(shapes)
    if anchor is not None:
        count = anchor.Evaluate("@com.Page.AllLayers.Count")
        items = ", ".join(f"@com.Page.AllLayers.Item({ordinal})" for ordinal in range(1, count + 1))
        rows = anchor.Evaluate(f"array({items}).convert({_LAYER_LEAF})")
        return _PageState(shapes, anchor, [tuple(row) for row in rows])
    # Nothing on the page's own layers: read the layer list directly (master layers may still have shapes).
    layers = [(layer.Name, layer.Shapes.Count, layer.Master, layer.Visible, layer.Printable, layer.Editable)
              for layer in page.AllLayers]
    return _PageState(shapes, None, layers)


def _master_only_views(page, state, layer_name):
    """Views for a page whose shapes are all on master layers: walked layer by layer, not cached."""
    views = []
    for ordinal, (name, count, master, visible, printable, editable) in enumerate(state.layers, 1):
        if layer_name is not None and name != layer_name:
            continue
        layer = page.AllLayers.Item(ordinal)
        if count:
            views.append(layer_view(layer, walk_layer(layer)))
            views[-1].ordinal = None
        else:
            views.append(LayerView(page, None, ordinal, name, master, visible, printable, LayerStructure([], []), layer,
                                   editable))
    return views


def _recursive_count(state):
    return state.shapes.FindShapes(Empty, 0, True).Count


def _newer_count(state, max_id):
    return state.shapes.FindShapes(Empty, 0, True, f"@com.StaticID > {max_id}").Count


def _still_valid(state, cached) -> bool:
    if [(row[0], row[1]) for row in state.layers] != cached.layers:
        return False
    if _recursive_count(state) != cached.recursive_count or _newer_count(state, cached.max_id):
        return False
    # Recursive counts (FindShapes with an empty name) see only unnamed shapes and no PowerClip contents, so the
    # container check below is what catches nested edits among named shapes.
    groups = [(ordinal, group) for ordinal, structure in enumerate(cached.structures, 1) for group in structure.groups]
    if not groups:
        return True
    if len(groups) * 10 > sum(len(structure.entries) for structure in cached.structures):
        return False  # verifying many groups one path at a time costs about as much as a walk
    try:
        values = evaluate(state.anchor, [f"array({query_path(path, ordinal)}).convert({_GROUP_LEAF})"
                                         for ordinal, (path, _, _, _) in groups])
    except Exception:
        return False  # a cached path no longer resolves
    return all(value[0] and str(value[0][0]) == shape_id and value[0][1] == size and value[0][2] == clips
               for value, (_, (_, shape_id, size, clips)) in zip(values, groups, strict=True))


def page_layers(doc, page, layer_name: str | None = None, *, fresh: bool = False) -> list[LayerView]:
    """The page's layers (``page.AllLayers`` order, or those with that exact name) with valid structures."""
    generation = get_connection().generation
    key = (normalize_path(document_identity(doc)), page.Index)
    state = _page_state(page)
    if state.anchor is None:
        _cache.pop(key, None)
        return _master_only_views(page, state, layer_name)
    cached = _cache.get(key)
    if cached is not None and not fresh and cached.generation == generation and _still_valid(state, cached):
        _stats["hits"] += 1
        structures = cached.structures
        _cache.move_to_end(key)
    else:
        _cache.pop(key, None)
        recursive = _recursive_count(state)
        tops = [(ordinal, row[1]) for ordinal, row in enumerate(state.layers, 1)]
        walked = _walk(state.anchor, tops)
        structures = [walked.get(ordinal) or LayerStructure([], []) for ordinal, _ in tops]
        max_id = max((int(entry.shape_id) for structure in structures for entry in structure.entries), default=0)
        # Cache only a walk that no edit overlapped (counts and newest ID unchanged across it).
        if _recursive_count(state) == recursive and not _newer_count(state, max_id):
            _cache[key] = _PageEntry(generation, [(row[0], row[1]) for row in state.layers], recursive, max_id,
                                     structures)
            while len(_cache) > _MAX_PAGES:
                _cache.popitem(last=False)
    return [LayerView(page, state.anchor, ordinal, row[0], row[2], row[3], row[4], structure, None, row[5])
            for ordinal, (row, structure) in enumerate(zip(state.layers, structures, strict=True), 1)
            if layer_name is None or row[0] == layer_name]


# --- reading shapes ------------------------------------------------------------------------------------------

def read_entries(view: LayerView, entries, expressions) -> list[tuple]:
    """Read CQL ``expressions`` (over ``$item``) for each entry of one layer, verifying cached StaticIDs."""
    if not entries:
        return []
    leaf = "array(" + ", ".join(["$item.StaticID", *expressions]) + ")"
    runs = _runs([entry.path for entry in entries])
    values = _read_runs(view.anchor, [(view.ordinal, *run) for run in runs], leaf)
    by_path, position = {}, 0
    for parent, first, count, clip in runs:
        for offset in range(count):
            by_path[(*parent, -(first + offset) if clip else first + offset)] = values[position]
            position += 1
    results = []
    for entry in entries:
        static_id, *rest = by_path[entry.path]
        if str(static_id) != entry.shape_id:
            _stats["stale"] += 1
            raise StaleStructureError(f"Shape {entry.shape_id} is no longer at its cached position")
        results.append(tuple(rest))
    return results


def resolve_path(layer, path):
    """The COM shape at an index path (no verification)."""
    shape = layer.Shapes.Item(path[0])
    for index in path[1:]:
        shape = shape.PowerClip.Shapes.Item(-index) if index < 0 else shape.Shapes.Item(index)
    return shape


def _locate(views, shape_ids):
    found = {}
    for view in views:
        structure = view.structure
        for shape_id in shape_ids:
            if shape_id in structure.position:
                if shape_id in found or shape_id in structure.duplicates:
                    raise ValueError(f"Ambiguous shape ID {shape_id}")
                found[shape_id] = (view, structure.entries[structure.position[shape_id]])
    return found


def resolve_shapes(doc, page, shape_ids: list[str]) -> list:
    """COM shapes for unique exact StaticIDs on the page's layers (master layers included), in request order."""
    requested = set(shape_ids)
    if not requested or len(requested) != len(shape_ids):
        raise ValueError("Provide one or more unique shape IDs")
    for fresh in (False, True):
        views = page_layers(doc, page, fresh=fresh)
        found = _locate(views, shape_ids)
        if requested - found.keys() and not fresh:
            continue  # confirm with a fresh walk before reporting an ID as missing
        shapes = {}
        for shape_id, (view, entry) in found.items():
            shape = resolve_path(view.layer, entry.path)
            if str(shape.StaticID) != shape_id:
                _stats["stale"] += 1
                break
            shapes[shape_id] = shape
        else:
            if missing := requested - shapes.keys():
                raise ValueError(f"Shape IDs not found on requested page: {', '.join(sorted(missing))}")
            return [shapes[shape_id] for shape_id in shape_ids]
    raise StaleStructureError("The page changed while its shapes were being located; retry")
