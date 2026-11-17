import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
import pythoncom
def go():
    doc = open_mine(WORK + r"\structure.cdr")
    page = doc.Pages(1)
    sh = page.Shapes
    a = sh.Item(1)
    out = {}
    types = list(range(0, 30))
    for label, fn in {
        "All": lambda: sh.All().Count,
        "CountAnyOfType": lambda: sh.All().CountAnyOfType(types),
        "SelectableShapes": lambda: page.SelectableShapes.Count,
        "cql Shapes as array": lambda: a.Evaluate("@com.Layer.Shapes.count()"),
        "cql Shapes.All": lambda: a.Evaluate("@com.Layer.Shapes.All.Count"),
        "cql FindShapes": lambda: a.Evaluate("@com.Layer.Shapes.FindShapes().Count"),
        "tree children": lambda: page.TreeNode.Children.Count,
    }.items():
        try:
            out[label] = fn()
        except Exception as e:
            out[label] = "ERR " + str(e)[:150]
    print(out)
run(go)
