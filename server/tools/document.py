"""Document management: list, open, create, save and close exact documents; edit pages."""

import ntpath
import os
from typing import Literal

from core.connection import get_connection
from core.drawing import CDR_TEXT_SHAPE, walk_shapes
from core.models import ToolResult
from core.targets import (
    Target,
    activate_document,
    call_on_active,
    describe,
    document_identity,
    find_documents,
    is_active,
    normalize_path,
    open_documents,
    resolve_document,
    resolve_page,
    save_as,
)
from core.units import MILLIMETER, convert, unit_code, unit_name

_TOLERANCE_IN = 1e-4


def _layer_entry(layer):
    return {
        "name": layer.Name, "master": bool(layer.Master), "special": bool(layer.IsSpecialLayer),
        "visible": bool(layer.Visible), "printable": bool(layer.Printable), "editable": bool(layer.Editable),
        "shape_count": layer.Shapes.Count,
    }


def _pages(doc, detail=False):
    pages = []
    for index in range(1, doc.Pages.Count + 1):
        page = doc.Pages(index)
        entry = {"index": index, "name": page.Name, "width": page.SizeWidth, "height": page.SizeHeight}
        if detail:
            entry["layers"] = [_layer_entry(layer) for layer in page.AllLayers]
        pages.append(entry)
    return pages


def _document_entry(app, doc, detail=False):
    return {
        "document_path": document_identity(doc), "title": doc.Title, "saved": bool(doc.FullFileName),
        "dirty": bool(doc.Dirty), "unit": unit_name(doc.Unit), "page_count": doc.Pages.Count,
        "active": is_active(app, doc), "active_page": doc.ActivePage.Index, "pages": _pages(doc, detail),
    }


def _result(result, message=None):
    if result["success"]:
        data = result["result"]
        if error := data.pop("verification_error", None):
            return ToolResult(success=False, error=error, data=data)
        return ToolResult.ok(message, **data)
    return ToolResult.fail(result.get("error", "CorelDRAW call failed"))


def list_documents(detail: bool = False) -> ToolResult:
    """List open documents: document_path (pass it to other tools; an unsaved document's title), title,
    saved, dirty, unit ("in", "mm"...), page_count, active, active_page, page sizes in that unit.

    detail=True adds each page's layers (master, special, visible, printable, editable, shape_count).
    """
    conn = get_connection()

    def run():
        app = conn.app
        documents = [_document_entry(app, doc, detail) for doc in open_documents(app)]
        active = next((entry["document_path"] for entry in documents if entry["active"]), None)
        return {"active_document_path": active, "documents": documents}

    return _result(conn.safe_call(run))


def open_document(path: str, activate: bool = True) -> ToolResult:
    """Open an absolute .cdr path, or create a new unsaved document from a .cdt. Never changes the unit.

    An already-open .cdr is returned, not reopened (already_open). activate=False keeps the current
    active document. Returns the list_documents entry.
    """
    conn = get_connection()

    def run():
        app = conn.app
        if not ntpath.isabs(path):
            raise ValueError("path must be an absolute Windows path")
        extension = ntpath.splitext(path)[1].lower()
        if extension not in (".cdr", ".cdt"):
            raise ValueError("path must be a .cdr drawing or a .cdt template")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        previous = app.ActiveDocument
        existing = find_documents(app, path) if extension == ".cdr" else []
        if len(existing) > 1:
            raise ValueError(f"{path!r} is open {len(existing)} times; close the duplicates first")
        if existing:
            doc = existing[0]
        elif extension == ".cdt":
            doc = app.CreateDocumentFromTemplate(path)
        else:
            doc = app.OpenDocument(path)
        if activate:
            activate_document(app, doc)
        elif previous is not None and not is_active(app, previous):
            activate_document(app, previous)
        return {"already_open": bool(existing), "source_path": path, **_document_entry(app, doc)}

    return _result(conn.safe_call(run))


def _file_state(path):
    try:
        stat = os.stat(path)
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _save_as(app, doc, save_as_path, overwrite):
    """SaveAs with options, refusing to clobber files or other open documents. Returns the prior file state."""
    if not ntpath.isabs(save_as_path) or ntpath.splitext(save_as_path)[1].lower() != ".cdr":
        raise ValueError("save_as_path must be an absolute .cdr path")
    if not os.path.isdir(ntpath.dirname(save_as_path)):
        raise ValueError(f"The folder does not exist: {ntpath.dirname(save_as_path)}")
    current = doc.FullFileName
    same_file = bool(current) and normalize_path(current) == normalize_path(save_as_path)
    if not same_file and find_documents(app, save_as_path):
        raise ValueError(f"Another open document uses {save_as_path!r}; close it first")
    before = _file_state(save_as_path)
    if before is not None and not same_file and not overwrite:
        raise ValueError(f"{save_as_path!r} already exists; pass overwrite=True to replace it")
    if same_file:
        doc.Save()
    else:
        save_as(app, doc, save_as_path)
    return before


def _verify_saved(doc, path, before, data):
    after = _file_state(path)
    problems = []
    if after is None or after[0] <= 0:
        problems.append("the file is missing or empty")
    elif before is not None and after[1] == before[1]:
        problems.append("the file was not rewritten")
    if normalize_path(doc.FullFileName or "") != normalize_path(path):
        problems.append(f"the document now refers to {doc.FullFileName!r}")
    if doc.Dirty:
        problems.append("the document still has unsaved changes")
    data.update(bytes=after[0] if after else 0)
    if problems:
        data["verification_error"] = f"Save verification failed: {'; '.join(problems)}"
    return data


def save_document(document_path: str, save_as_path: str | None = None, overwrite: bool = False) -> ToolResult:
    """Save an open document in place, or with save_as_path (absolute .cdr) to a new file it then refers to.

    An existing file needs overwrite=True; a path open in another document is refused. Verifies the
    file on disk, the document's path and its clean state.
    """
    conn = get_connection()

    def run():
        app = conn.app
        target = resolve_document(app, document_path)
        doc = target.doc
        destination = save_as_path or doc.FullFileName
        if not destination:
            raise ValueError("This document has never been saved; pass save_as_path")
        before = _save_as(app, doc, destination, overwrite)
        data = {"previous_path": target.path, "saved_path": destination, "overwrote": before is not None,
                **describe(Target(doc, document_identity(doc)))}
        return _verify_saved(doc, destination, before, data)

    return _result(conn.safe_call(run))


def close_document(document_path: str, discard_changes: bool = False) -> ToolResult:
    """Close exactly this document without a prompt. Unsaved changes are refused unless discard_changes=True."""
    conn = get_connection()

    def run():
        app = conn.app
        target = resolve_document(app, document_path)
        doc = target.doc
        dirty = bool(doc.Dirty)
        if dirty and not discard_changes:
            raise ValueError(f"{target.path!r} has unsaved changes; save it or pass discard_changes=True")
        if dirty:
            doc.Dirty = False
        doc.Close()
        remaining = [document_identity(other) for other in open_documents(app)]
        data = {"closed_path": target.path, "discarded_changes": dirty, "open_documents": remaining}
        if find_documents(app, target.path):
            data["verification_error"] = "The document is still open after Close"
        return data

    return _result(conn.safe_call(run))


def create_document(width: float, height: float, unit: str = "mm", save_as_path: str | None = None) -> ToolResult:
    """Create an active one-page document; unit (in, mm, cm, pt) is its unit and the page size unit.

    With save_as_path (new absolute .cdr) it is saved at once; otherwise its document_path is its title.
    """
    conn = get_connection()
    try:
        code = unit_code(unit)
    except ValueError as exc:
        return ToolResult.fail(str(exc))
    if not (width > 0 and height > 0):
        return ToolResult.fail("width and height must be positive")

    def run():
        app = conn.app
        doc = app.CreateDocument()
        try:
            # A brand-new document takes the requested unit; no existing document is changed.
            doc.Unit = code
            doc.ActivePage.SetSize(width, height)
            data = {}
            if save_as_path:
                before = _save_as(app, doc, save_as_path, overwrite=False)
                _verify_saved(doc, save_as_path, before, data)
                if "verification_error" in data:
                    raise RuntimeError(data["verification_error"])
        except Exception:
            doc.Dirty = False
            doc.Close()
            raise
        return {**_document_entry(app, doc), **data}

    return _result(conn.safe_call(run))


_PAGE_ACTIONS = ("add", "delete", "resize", "activate")


def edit_pages(
    document_path: str,
    action: Literal["add", "delete", "resize", "activate"],
    page_index: int | None = None,
    count: int = 1,
    width: float | None = None,
    height: float | None = None,
    unit: str | None = None,
) -> ToolResult:
    """Edit pages; activates the document, never changes its unit. Returns pages and sizes.

    add: count pages at the end, or after page_index. delete: page_index (not the only page).
    resize: page_index to width x height in unit (in, mm, cm, pt). activate: make page_index active.
    add, delete and resize are one undo step.
    """
    if action not in _PAGE_ACTIONS:
        return ToolResult.fail(f"action must be one of {', '.join(_PAGE_ACTIONS)}")
    if action in ("delete", "resize", "activate") and page_index is None:
        return ToolResult.fail(f"action={action} needs page_index")
    if action == "add" and not 1 <= count <= 100:
        return ToolResult.fail("count must be between 1 and 100")
    if action == "resize":
        if width is None or height is None or unit is None or not (width > 0 and height > 0):
            return ToolResult.fail("resize needs positive width and height and a unit")
        try:
            unit_code(unit)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
    conn = get_connection()

    def run():
        app = conn.app
        # Page edits are undoable, and CorelDRAW's Undo follows the active document, so activate it.
        target = resolve_document(app, document_path, activate=True)
        doc = target.doc
        page = resolve_page(doc, page_index) if page_index is not None else None
        before_count = doc.Pages.Count
        active_before = doc.ActivePage.Index
        data = {}
        if action == "activate":
            page.Activate()
            if doc.ActivePage.Index != page_index:
                data["verification_error"] = f"CorelDRAW did not activate page {page_index}"
        else:
            doc.BeginCommandGroup(f"Pages: {action}")
            try:
                if action == "add":
                    first = doc.InsertPages(count, False, page_index) if page else doc.AddPages(count)
                    data["added_page_indices"] = list(range(first.Index, first.Index + count))
                    if doc.Pages.Count != before_count + count:
                        data["verification_error"] = "The page count did not grow by count"
                    # Adding pages activates the new page; keep the page that was active.
                    shifted = active_before + count if page and active_before > page_index else active_before
                    if doc.ActivePage.Index != shifted:
                        doc.Pages(shifted).Activate()
                elif action == "delete":
                    if before_count <= 1:
                        raise ValueError("Cannot delete the only page")
                    page.Delete()
                    if doc.Pages.Count != before_count - 1:
                        data["verification_error"] = "The page count did not shrink by one"
                else:
                    doc_unit = doc.Unit
                    page.SetSize(convert(width, unit, doc_unit, app), convert(height, unit, doc_unit, app))
                    actual = (convert(page.SizeWidth, doc_unit, unit, app), convert(page.SizeHeight, doc_unit, unit, app))
                    data["requested"] = {"width": width, "height": height, "unit": unit}
                    tolerance = convert(_TOLERANCE_IN, "in", unit)
                    if abs(actual[0] - width) > tolerance or abs(actual[1] - height) > tolerance:
                        data["verification_error"] = f"The page size reads back as {actual[0]} x {actual[1]} {unit}"
            finally:
                doc.EndCommandGroup()
        return {
            "action": action, **describe(target), **data, "page_count": doc.Pages.Count,
            "active_page": doc.ActivePage.Index, "pages": _pages(doc),
        }

    return _result(conn.safe_call(run))


def list_all_text_shapes(
    page_index: int = 0, content_preview_len: int = 40, document_path: str = "",
) -> ToolResult:
    """List editable text shapes on a page (0 = active page), including nested groups.

    document_path defaults to the active document. Sizes are in document_unit.
    """
    conn = get_connection()
    if content_preview_len < 0:
        return ToolResult.fail("content_preview_len must be nonnegative")

    def _list():
        app = conn.app
        if document_path:
            target = resolve_document(app, document_path)
        elif app.ActiveDocument is None:
            raise RuntimeError("CorelDRAW has no active document")
        else:
            target = Target(app.ActiveDocument, document_identity(app.ActiveDocument))
        doc = target.doc
        page = doc.ActivePage if page_index == 0 else resolve_page(doc, page_index)

        items = []
        for shape, parent_id in walk_shapes(page.Shapes):
            if shape.Type != CDR_TEXT_SHAPE:
                continue
            item = {
                "shape_id": str(shape.StaticID), "parent_id": parent_id, "name": shape.Name or "",
                "x": shape.PositionX, "y": shape.PositionY, "width": shape.SizeWidth, "height": shape.SizeHeight,
                "writable": False, "text_type": "unknown", "content_preview": "",
            }
            try:
                frame = shape.Text
                story = frame.Story
                content = story.Text
                item["content_preview"] = content[:content_preview_len]
                if len(content) > content_preview_len:
                    item["content_preview"] += "…"
                text_type = frame.Type
                if text_type in (0, 1, 2, 3):
                    item["text_type"] = "paragraph" if text_type in (1, 3) else "artistic"
                item["writable"] = True
                for prop in ("Font", "Size", "Bold", "Italic"):
                    try:
                        item[prop.lower()] = getattr(story, prop)
                    except Exception:
                        item[prop.lower()] = None
            except Exception as exc:
                item["read_error"] = str(exc)
            items.append(item)

        return {
            **describe(target), "page": page.Index, "total": len(items),
            "writable": sum(item["writable"] for item in items), "shapes": items,
        }

    result = conn.safe_call(_list)
    if result["success"]:
        r = result["result"]
        return ToolResult.ok(f"Page {r['page']}: {r['total']} text shapes, {r['writable']} writable", **r)
    return ToolResult.fail(result.get("error", "Listing text shapes failed"))


def add_guideline(document_path: str, position: float, orientation: str = "horizontal") -> ToolResult:
    """Add a horizontal or vertical guideline at position mm on the active page. Active document only."""
    conn = get_connection()
    orientation_lower = orientation.lower()
    if orientation_lower not in ("horizontal", "vertical"):
        return ToolResult.fail("orientation must be horizontal or vertical")

    def _add():
        doc = conn.app.ActiveDocument
        page = doc.ActivePage
        offset = convert(position, MILLIMETER, doc.Unit, conn.app)
        try:
            if orientation_lower == "horizontal":
                guide = page.ActiveLayer.CreateGuide(0, offset, page.SizeWidth, offset)
            else:
                guide = page.ActiveLayer.CreateGuide(offset, 0, offset, page.SizeHeight)
        except Exception:
            try:
                guides = page.Guides
                guide = guides.Add(offset, 0) if orientation_lower == "horizontal" else guides.Add(offset, 90)
            except Exception:
                raise RuntimeError(f"Could not add a {orientation_lower} guideline") from None

        data = {"position_mm": position, "orientation": orientation_lower, "page_index": page.Index}
        try:
            data["shape_id"] = str(guide.StaticID)
        except Exception:
            pass
        return data

    result = call_on_active(conn, document_path, _add)
    if result["success"]:
        return ToolResult.ok(f"Added {orientation_lower} guideline at {position} mm", **result["result"])
    return ToolResult.fail(result.get("error", "Adding the guideline failed"))
