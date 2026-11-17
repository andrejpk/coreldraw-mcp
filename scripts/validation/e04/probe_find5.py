import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
import pythoncom
def go():
    doc = open_mine(WORK + r"\artifacts.cdr")
    sh = doc.Pages(1).Shapes
    out = {}
    for label, fn in {
        "FindShape sid 4": lambda: sh.FindShape(pythoncom.Empty, 0, 4, True).Name,
        "FindShape q": lambda: sh.FindShape(pythoncom.Empty, 0, 0, True, "@com.StaticID = 7").Name,
        "FindShape sid 99": lambda: sh.FindShape(pythoncom.Empty, 0, 99, True),
        "cql count fn": lambda: sh.Item(1).Evaluate("@com.Layer.Shapes.Count"),
        "cql sum": lambda: sh.Item(1).Evaluate("array(1,2,3).sum()"),
        "cql max": lambda: sh.Item(1).Evaluate("array(1,5,3).max()"),
        "cql count": lambda: sh.Item(1).Evaluate("array(1,5,3).count()"),
        "cql filter count": lambda: sh.Item(1).Evaluate("array(1,5,3).filter($item > 2).count()"),
        "doc props": lambda: [p for p in ("ShapeCount",) if hasattr(doc, p)],
    }.items():
        try:
            out[label] = fn()
        except Exception as e:
            out[label] = "ERR " + str(e)[:120]
    print(out)
run(go)
