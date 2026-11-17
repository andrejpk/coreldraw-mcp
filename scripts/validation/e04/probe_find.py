"""Probe FindShapes name semantics (fingerprint blind spot) on artifacts.cdr and structure.cdr."""
import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed, conn
import pythoncom
from core.targets import open_documents

def go():
    print("open docs:", [d.Title for d in open_documents(conn.app)])
    for name in ("artifacts.cdr", "structure.cdr"):
        doc = open_mine(WORK + "\\" + name)
        page = doc.Pages(1)
        sh = page.Shapes
        out = {}
        for label, args in [("Empty", (pythoncom.Empty, 0, True)), ("star", ("*", 0, True)),
                            ("ArgNotFound", (pythoncom.ArgNotFound, 0, True)),
                            ("emptystr", ("", 0, True)), ("Empty+q true", (pythoncom.Empty, 0, True, "true")),
                            ("ArgNotFound+q", (pythoncom.ArgNotFound, 0, True, "@com.StaticID > 0"))]:
            try:
                out[label] = sh.FindShapes(*args).Count
            except Exception as e:
                out[label] = "ERR " + str(e)[:80]
        try:
            out["all"] = sh.All().Count
            out["allRec"] = sh.All().Shapes.FindShapes(pythoncom.ArgNotFound, 0, True).Count if False else None
        except Exception as e:
            out["all"] = str(e)[:80]
        print(name, out)
run(go)
