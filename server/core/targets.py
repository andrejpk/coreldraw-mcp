"""Resolve the exact CorelDRAW document a tool acts on. Every tool targets documents through this module.

A ``document_path`` is a document's full file path, compared case-insensitively with normalized separators.
An unsaved document has no path; it is addressed by its exact title (for example ``Untitled-1``).
Resolving never changes the unit, view, page or selection. ``activate=True`` only makes the document the
active one. Use it for every edit: CorelDRAW applies ``Document.Undo`` to the *active* document whatever
document it is called on, and an inactive document has no ``ActiveWindow``. Reads need no activation.
"""

import ntpath
import sys
import time
from contextlib import contextmanager
from typing import NamedTuple

from core.units import unit_name

_ACTIVATE_SECONDS = 5.0


class Target(NamedTuple):
    """An open document and the identity string tools report as ``document_path``."""

    doc: object
    path: str


def normalize_path(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path.replace("/", "\\")))


def document_identity(doc) -> str:
    """The document's full path, or its title when it has never been saved."""
    return doc.FullFileName or doc.Title


def open_documents(app) -> list:
    documents = app.Documents
    return [documents.Item(index) for index in range(1, documents.Count + 1)]


def find_documents(app, document_path: str) -> list:
    """Return the open documents whose path (or, for an unsaved document, title) matches exactly."""
    if ntpath.isabs(document_path):
        wanted = normalize_path(document_path)
        return [doc for doc in open_documents(app) if doc.FullFileName and normalize_path(doc.FullFileName) == wanted]
    wanted = document_path.strip().casefold()
    return [doc for doc in open_documents(app) if not doc.FullFileName and doc.Title.casefold() == wanted]


def is_active(app, doc) -> bool:
    active = app.ActiveDocument
    return active is not None and _same(active, doc)


def _same(first, second) -> bool:
    return normalize_path(document_identity(first)) == normalize_path(document_identity(second))


def activate_document(app, doc) -> None:
    """Make ``doc`` the active document and verify it. Changes nothing else."""
    if is_active(app, doc):
        return
    doc.Activate()
    # Activation completes asynchronously: pump window messages until CorelDRAW reports it.
    deadline = time.monotonic() + _ACTIVATE_SECONDS
    while not is_active(app, doc):
        if time.monotonic() > deadline:
            raise RuntimeError(f"CorelDRAW did not activate {document_identity(doc)!r}")
        if sys.platform == "win32":
            import pythoncom

            pythoncom.PumpWaitingMessages()
        time.sleep(0.01)


def resolve_document(app, document_path: str, *, activate: bool = False) -> Target:
    """Find exactly one open document by full path (or unsaved title). Optionally make it active."""
    if not document_path or not document_path.strip():
        raise ValueError("document_path is required: pass the full path from list_documents")
    if not ntpath.isabs(document_path) and ("\\" in document_path or "/" in document_path):
        raise ValueError("document_path must be an absolute Windows path (or the title of an unsaved document)")
    matches = find_documents(app, document_path)
    if not matches:
        raise ValueError(f"Document is not open: {document_path!r}. Use list_documents or open_document.")
    if len(matches) > 1:
        raise ValueError(f"{document_path!r} matches {len(matches)} open documents; close the duplicates first")
    doc = matches[0]
    if activate:
        activate_document(app, doc)
    return Target(doc, document_identity(doc))


def require_active_document(app, document_path: str) -> Target:
    """Resolve ``document_path`` and refuse unless it is the active document (for active-document tools)."""
    target = resolve_document(app, document_path)
    if not is_active(app, target.doc):
        active = app.ActiveDocument
        raise ValueError(
            f"Document mismatch: {target.path!r} is open but the active document is "
            f"{document_identity(active) if active is not None else None!r}. This tool acts on the active "
            "document; call open_document(path, activate=True) first."
        )
    return target


def active_target(app, document_path: str = "") -> Target:
    """For read-only active-document tools: the named document (which must be active), else the active one."""
    if document_path:
        return require_active_document(app, document_path)
    doc = app.ActiveDocument
    if doc is None:
        raise RuntimeError("CorelDRAW has no active document; open one with open_document")
    return Target(doc, document_identity(doc))


def resolve_page(doc, page_index: int):
    """Return a one-based page of the document, with a clear error when out of range."""
    count = doc.Pages.Count
    if not isinstance(page_index, int) or not 1 <= page_index <= count:
        raise ValueError(f"page_index must be between 1 and {count}")
    return doc.Pages(page_index)


def describe(target: Target) -> dict:
    """The identity fields every result carries."""
    return {"document_path": target.path, "document_unit": unit_name(target.doc.Unit)}


def call_on_active(conn, document_path: str, func, *, read_only: bool = False):
    """Run a legacy active-document operation only when ``document_path`` is the active document.

    Mutating tools require ``document_path``; read-only tools may omit it and read the active document.
    Dict results gain ``document_path`` and ``document_unit``.
    """
    def run():
        app = conn.app
        target = active_target(app, document_path) if read_only else require_active_document(app, document_path)
        result = func()
        return {**describe(target), **result} if isinstance(result, dict) else result

    return conn.safe_call(run)


@contextmanager
def private_document(app, path: str):
    """Open a file nobody has open (refuse otherwise) and always close it afterwards without a prompt."""
    if find_documents(app, path):
        raise ValueError(f"{path!r} is already open; close it before using it as a template")
    doc = app.OpenDocument(path)
    try:
        yield doc
    finally:
        doc.Dirty = False
        doc.Close()


def save_as(app, doc, path: str) -> None:
    """Document.SaveAs through dynamic pywin32 needs the StructSaveAsOptions argument."""
    doc.SaveAs(path, app.CreateStructSaveAsOptions())
