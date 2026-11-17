"""E13 in-process lab helpers (run from server/ with no MCP server running).

open_mine(path) opens a copy under the run folder (or returns it if already open); close_mine() closes only
documents under RUN (Dirty=False first). Never touches other documents.
"""
import sys
import time

sys.path.insert(0, ".")
from core.connection import get_connection  # noqa: E402
from core.targets import find_documents, normalize_path, open_documents  # noqa: E402

RUN = r"C:\CorelMcpLab\runs\e13"
WORK = RUN + r"\probes"
conn = get_connection()


def mine(path):
    return normalize_path(path).startswith(normalize_path(RUN))


def run(fn):
    r = conn.safe_call(fn)
    if not r["success"]:
        raise RuntimeError(r["error"])
    return r["result"]


def open_mine(path):
    assert mine(path), path
    docs = find_documents(conn.app, path)
    if docs:
        return docs[0]
    return conn.app.OpenDocument(path)


def close_mine():
    def go():
        closed = []
        for doc in open_documents(conn.app):
            if doc.FullFileName and mine(doc.FullFileName):
                doc.Dirty = False
                closed.append(doc.FullFileName)
                doc.Close()
        return closed
    return run(go)


def timed(fn, *a, **k):
    t = time.perf_counter()
    out = fn(*a, **k)
    return out, round(time.perf_counter() - t, 3)


def run_long(fn, timeout=900.0):
    """Lab only: one bridge operation with a longer time limit (slow direct-COM ground truth)."""
    r = conn._com_thread.call(lambda: conn._execute(fn, (), {}), timeout=timeout, queue_timeout=2.0, name="lab")
    if not r["success"]:
        raise RuntimeError(r["error"])
    return r["result"]


def methods(obj):
    """Names (with argument names for methods) from an object's type info."""
    ti = obj._oleobj_.GetTypeInfo()
    attr = ti.GetTypeAttr()
    out = []
    for i in range(attr.cFuncs):
        fd = ti.GetFuncDesc(i)
        names = ti.GetNames(fd.memid)
        out.append(names[0] + ("(" + ",".join(names[1:]) + ")" if fd.invkind == 1 else ""))
    return sorted(set(out))


def fresh_copy(template, name):
    """Copy a template into WORK under a new name (overwrites) and return its path."""
    import shutil
    path = WORK + "\\" + name
    shutil.copyfile(template, path)
    return path


def close_path(path):
    def go():
        for doc in find_documents(conn.app, path):
            doc.Dirty = False
            doc.Close()
    run(go)
