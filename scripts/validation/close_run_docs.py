"""Close only documents saved under the given run folder (Dirty=False first). Usage: close_run_docs.py <run-folder>"""
import ntpath, sys; sys.path.insert(0, ".")
from core.connection import get_connection
conn = get_connection()
RUN = ntpath.normcase(ntpath.normpath(sys.argv[1])) + "\\"


def run():
    app, closed, kept = conn.app, [], []
    for i in range(app.Documents.Count, 0, -1):
        d = app.Documents.Item(i)
        full = d.FullFileName or ""
        if full and ntpath.normcase(ntpath.normpath(full)).startswith(RUN):
            d.Dirty = False; d.Close(); closed.append(full)
        else:
            kept.append(full or "untitled")
    return {"closed": closed, "kept": kept}


print(conn.safe_call(run))
