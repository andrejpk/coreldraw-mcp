"""Layers on exact pages: list them, set their flags or name, create them, and move exact shapes onto them.

Page layers are addressed by page and exact name; master layers (shared by every page) only with master=true.
Edits activate the document, are one undo step, read the result back and report the document's dirty flag
(layer flags and names are saved with the document, so changing them marks it modified).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Literal

from core.connection import get_connection
from core.models import ToolResult
from core.shapeops import (
    ANY_TYPE,
    EDIT_BUDGET_SECONDS,
    Budget,
    nested_refusals,
    page_ids,
    refusal,
    target_layer,
    undoable,
)
from core.structure import invalidate, page_layers, walk_layer
from core.targets import describe, resolve_document, resolve_page
from tools.colors import _com_shape, _edit, _RefusedError

_FLAGS = {"visible": "Visible", "printable": "Printable", "editable": "Editable"}


def _hex(layer):
    try:
        return layer.Color.HexValue
    except Exception:
        return None


def _state(layer) -> dict:
    state = {"name": layer.Name, "master": bool(layer.Master)}
    state.update({key: bool(getattr(layer, attribute)) for key, attribute in _FLAGS.items()})
    if layer.IsSpecialLayer:
        state["special"] = True
    state["locked"] = not state["editable"]
    return state


def _pages(doc, pages):
    count = doc.Pages.Count
    if pages is None:
        return list(range(1, count + 1))
    if not isinstance(pages, list) or not pages or not all(isinstance(p, int) and 1 <= p <= count for p in pages):
        raise ValueError(f"pages must be a list of page indexes between 1 and {count}")
    return sorted(set(pages))


def list_layers(document_path: str, pages: list[int] | None = None) -> ToolResult:
    """List each page's layers (or pages=[...], one-based) of an exact document, front first, masters
    where they stack. Read-only.

    Per layer: name, master (shared by every page), special (guides/desktop/grid), visible, printable,
    editable/locked, color (#RRGGBB), objects (top level), objects_total (with group members and PowerClip
    contents). Also the active layer and the dirty flag.
    """
    conn = get_connection()

    def read():
        target = resolve_document(conn.app, document_path)
        doc = target.doc
        out = []
        for index in _pages(doc, pages):
            page = resolve_page(doc, index)
            layers = []
            for view in page_layers(doc, page):
                layer = view.layer
                entry = {"name": view.name, "master": bool(view.master), "visible": bool(view.visible),
                         "printable": bool(view.printable), "editable": bool(view.editable),
                         "locked": not view.editable, "color": _hex(layer),
                         "objects": sum(1 for e in view.structure.entries if len(e.path) == 1),
                         "objects_total": len(view.structure.entries)}
                if layer.IsSpecialLayer:
                    entry["special"] = True
                layers.append(entry)
            out.append({"page_index": index, "page_name": page.Name, "layers": layers})
        active = doc.ActiveLayer
        return {**describe(target), "dirty": bool(doc.Dirty), "active_page": doc.ActivePage.Index,
                "active_layer": {"name": active.Name, "master": bool(active.Master)} if active is not None else None,
                "pages": out}

    result = conn.read_call(read)
    return ToolResult.ok(**result["result"]) if result["success"] else ToolResult.fail(result["error"])


@contextmanager
def _layer_step(doc, label):
    """One undo step for layer edits; a failure part way is undone."""
    doc.BeginCommandGroup(label)
    try:
        yield
    except BaseException:
        doc.EndCommandGroup()
        doc.Undo(1)
        raise
    doc.EndCommandGroup()


def _find_layer(doc, page, name, master):
    """The page's own layer (or, with master, the document's master layer) with exactly this name."""
    local = [layer for layer in page.Layers if layer.Name == name and not layer.Master]
    shared = [layer for layer in doc.MasterPage.Layers if layer.Name == name]
    matches = shared if master else local
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} layers are named {name!r}; rename one in CorelDRAW first")
    if not master and shared:
        raise ValueError(f"{name!r} is a master layer (shared by every page); pass master=true")
    if master and local:
        raise ValueError(f"{name!r} is a layer of page {page.Index}, not a master layer; omit master")
    names = [layer.Name for layer in (doc.MasterPage.Layers if master else page.Layers)]
    where = "" if master else f" on page {page.Index}"
    raise ValueError(f"No {'master ' if master else ''}layer named {name!r}{where}; layers: {names}")


def _layer_edit(document_path, page_index, body) -> ToolResult:
    conn = get_connection()

    def run():
        doc, actual_path = resolve_document(conn.app, document_path, activate=True)
        page = resolve_page(doc, page_index)
        try:
            return {"document_path": actual_path, "page_index": page_index, **body(conn.app, doc, page)}
        finally:
            invalidate(actual_path)

    result = conn.safe_call(run)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if data.get("verification_errors"):
        return ToolResult(success=False, data=data, error="CorelDRAW did not apply every change (see "
                                                          "verification_errors); the edit is one undo step (undo)")
    return ToolResult.ok(**data)


def set_layer_properties(
    document_path: str,
    page_index: int,
    layer_name: str,
    visible: bool | None = None,
    printable: bool | None = None,
    editable: bool | None = None,
    rename: str | None = None,
    master: bool = False,
) -> ToolResult:
    """Set visible, printable, editable (false locks) and/or rename one exact layer; one undo step.

    layer_name is a layer of page page_index; a master layer only with master=true (changes every page). Special
    layers (guides, desktop, grid) take only visible and printable. Returns flags before and after (read back)
    and the dirty flag (any change marks the document modified).
    """
    changes = {key: value for key, value in (("visible", visible), ("printable", printable), ("editable", editable))
               if value is not None}
    if any(not isinstance(value, bool) for value in changes.values()):
        return ToolResult.fail("visible, printable and editable are true or false")
    if not changes and rename is None:
        return ToolResult.fail("Pass at least one of visible, printable, editable or rename")
    if rename is not None and (not isinstance(rename, str) or not rename.strip()):
        return ToolResult.fail("rename must be a non-empty name")

    def body(app, doc, page):
        layer = _find_layer(doc, page, layer_name, master)
        if layer.IsSpecialLayer and (rename is not None or "editable" in changes):
            raise ValueError(f"{layer_name!r} is a special CorelDRAW layer; only visible and printable can change")
        if rename is not None and rename != layer_name:
            taken = {other.Name for other in page.AllLayers} | {other.Name for other in doc.MasterPage.Layers}
            if rename in taken:
                raise ValueError(f"A layer named {rename!r} already exists on page {page_index}")
        before = _state(layer)
        dirty_before = bool(doc.Dirty)
        with _layer_step(doc, "MCP set_layer_properties"):
            for key, value in changes.items():
                setattr(layer, _FLAGS[key], value)
            if rename is not None:
                layer.Name = rename
        after = _state(layer)
        problems = [{"property": key, "expected": value, "actual": after[key]} for key, value in changes.items()
                    if after[key] != value]
        if rename is not None and after["name"] != rename:
            problems.append({"property": "name", "expected": rename, "actual": after["name"]})
        data = {"layer": after, "before": before, "changed": sorted(k for k in after if after[k] != before.get(k)),
                "applies_to": "every page" if master else f"page {page_index}", "dirty": bool(doc.Dirty),
                "dirty_before": dirty_before}
        if problems:
            data["verification_errors"] = problems
        return data

    return _layer_edit(document_path, page_index, body)


def create_layer(
    document_path: str,
    page_index: int,
    name: str,
    master: bool = False,
    visible: bool = True,
    printable: bool = True,
    editable: bool = True,
    color: str | None = None,
) -> ToolResult:
    """Create a layer on top of page page_index, or a master layer (master=true, every page), with its
    flags and an optional color "#RRGGBB". One undo step; the active layer stays. Refuses a name already used on
    the page. Returns the layer read back.
    """
    if not isinstance(name, str) or not name.strip():
        return ToolResult.fail("name must be a non-empty layer name")
    if color is not None and not (isinstance(color, str) and len(color) == 7 and color.startswith("#")
                                  and all(c in "0123456789abcdefABCDEF" for c in color[1:])):
        return ToolResult.fail('color is "#RRGGBB"')

    def body(app, doc, page):
        taken = {other.Name for other in page.AllLayers} | {other.Name for other in doc.MasterPage.Layers}
        if name in taken:
            raise ValueError(f"A layer named {name!r} already exists on page {page_index}")
        active = doc.ActiveLayer
        with _layer_step(doc, "MCP create_layer"):
            layer = (doc.MasterPage if master else page).CreateLayer(name)
            for key, value in (("visible", visible), ("printable", printable), ("editable", editable)):
                setattr(layer, _FLAGS[key], bool(value))
            if color is not None:
                layer.Color = app.CreateRGBColor(int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
            now = doc.ActiveLayer
            if active is not None and now is not None and (now.Name, now.Master) != (active.Name, active.Master):
                active.Activate()
        state = _state(layer)
        state["color"] = _hex(layer)
        names = [other.Name for other in page.AllLayers]
        problems = [{"property": key, "expected": value, "actual": state[key]}
                    for key, value in (("visible", visible), ("printable", printable), ("editable", editable))
                    if state[key] != bool(value)]
        if color is not None and (state["color"] or "").upper() != color.upper():
            problems.append({"property": "color", "expected": color, "actual": state["color"]})
        if name not in names:
            problems.append({"problem": f"the layer is not on page {page_index}"})
        if active is not None and doc.ActiveLayer.Name != active.Name:
            problems.append({"problem": "the active layer changed", "active_layer": doc.ActiveLayer.Name})
        data = {"layer": state, "stack_index": names.index(name) + 1 if name in names else None,
                "applies_to": "every page" if master else f"page {page_index}", "dirty": bool(doc.Dirty)}
        if problems:
            data["verification_errors"] = problems
        return data

    return _layer_edit(document_path, page_index, body)


def move_shapes_to_layer(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    layer_name: str,
    position: Literal["top", "bottom"] = "top",
) -> ToolResult:
    """Move exact top-level shapes to the top or bottom of another layer of the same page; one undo step.

    Relative stacking order is kept and the active layer does not change. Another page's layer, a master layer
    or a locked layer is refused, as are sources on locked/master layers, inside groups, effect objects and
    PowerClip contents. Returns each shape's previous layer and stacking index (1 = front), read back.
    """
    if position not in ("top", "bottom"):
        return ToolResult.fail("position must be top or bottom")

    def body(app, doc, page, targets):
        layer = _move_target(doc, page, layer_name)
        nested = nested_refusals(targets)
        refused = []
        for shape_id, (view, entry) in targets.items():
            reason = (refusal(view, entry, consume=True, groups=True, bitmaps=True, types=ANY_TYPE)
                      or nested.get(shape_id)
                      or ("inside a group; move the group, or ungroup first" if entry.parent is not None else None))
            if reason:
                refused.append({"id": shape_id, "layer": view.name, "reason": reason})
        if refused:
            raise _RefusedError(refused)
        order = sorted(shape_ids, key=lambda i: (targets[i][0].ordinal or 0, targets[i][0].structure.position[i]))
        shapes = {shape_id: _com_shape(view, entry, shape_id) for shape_id, (view, entry) in targets.items()}
        active = doc.ActiveLayer
        active_key = (active.Name, bool(active.Master)) if active is not None else None
        # CorelDRAW silently keeps shapes of a hidden layer where they are: show those layers during the move.
        hidden = {view.ordinal: view.layer for view, _ in targets.values() if not view.visible}
        budget = Budget(EDIT_BUDGET_SECONDS)
        with undoable(doc, page, "MCP move_shapes_to_layer") as step:
            for source in hidden.values():
                source.Visible = True
            try:
                for done, shape_id in enumerate(reversed(order) if position == "top" else order):
                    budget.check(done, len(order), "moving")
                    shape = shapes[shape_id]
                    if targets[shape_id][0].name != layer_name:
                        shape.MoveToLayer(layer)
                    if position == "top":
                        shape.OrderToFront()
                    else:
                        shape.OrderToBack()
            finally:
                for source in hidden.values():
                    source.Visible = False
            if active is not None and (doc.ActiveLayer.Name, bool(doc.ActiveLayer.Master)) != active_key:
                active.Activate()
        tops = [e.shape_id for e in walk_layer(layer).entries if len(e.path) == 1]
        expected = order
        actual = tops[:len(order)] if position == "top" else tops[len(tops) - len(order):]
        problems = []
        if actual != expected:
            problems.append({"problem": f"the shapes are not at the {position} of {layer_name!r} in their order",
                             "expected": expected, "actual": actual})
        if page_ids(doc, page) != step.before:
            problems.append({"problem": "objects were added or removed"})
        now = doc.ActiveLayer
        if active_key is not None and (now.Name, bool(now.Master)) != active_key:
            problems.append({"problem": "the active layer changed", "active_layer": now.Name})
        data = {"layer": layer_name, "position": position, "layer_object_count": len(tops),
                "shapes": [{"id": i, "from_layer": targets[i][0].name,
                            "index": tops.index(i) + 1 if i in tops else None} for i in shape_ids]}
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)


def _move_target(doc, page, name):
    if not any(layer.Name == name for layer in page.Layers):
        elsewhere = [other.Index for other in (doc.Pages(i) for i in range(1, doc.Pages.Count + 1))
                     if other.Index != page.Index and any(layer.Name == name for layer in other.Layers)]
        if any(layer.Name == name for layer in doc.MasterPage.Layers):
            raise ValueError(f"{name!r} is a master layer; move_shapes_to_layer moves shapes between layers of one "
                             "page only")
        if elsewhere:
            raise ValueError(f"Layer {name!r} is not on page {page.Index} (it is on page {elsewhere}); "
                             "move_shapes_to_layer does not move shapes to another page")
    return target_layer(page, name)
