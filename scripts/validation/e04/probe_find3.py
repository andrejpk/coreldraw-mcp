import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
import pythoncom
def go():
    doc = open_mine(WORK + r"\artifacts.cdr")
    sh = doc.Pages(1).Shapes
    out = {}
    for label, args in {"exact": ("zero_size_line", 0, True), "glob": ("zero*", 0, True), "q?": ("zero_size_lin?", 0, True),
                        "type1 empty": (pythoncom.Empty, 1, True), "type1 star": ("*", 1, True),
                        "lower": ("ZERO_SIZE_LINE", 0, True),
                        "q only": (pythoncom.Empty, 0, True, "@name = 'zero_size_line'"),
                        }.items():
        try:
            out[label] = sh.FindShapes(*args).Count
        except Exception as e:
            out[label] = "ERR " + str(e)[:100]
    print(out)
    # Layer-level FindShapes and the doc-level
    lay = doc.Pages(1).Layers("Artifacts")
    out = {}
    for label, fn in {"layer.Shapes Empty": lambda: lay.Shapes.FindShapes(pythoncom.Empty, 0, True).Count,
                      "layer find '*'": lambda: lay.Shapes.FindShapes("*", 0, True).Count,
                      "page.FindShapes?": lambda: doc.Pages(1).FindShapes(pythoncom.Empty, 0, True).Count,
                      "SelectableShapes": lambda: doc.Pages(1).SelectableShapes.Count,
                      "SelectableShapes.All rec": lambda: doc.Pages(1).SelectableShapes.FindShapes(pythoncom.Empty, 0, True).Count,
                      }.items():
        try:
            out[label] = fn()
        except Exception as e:
            out[label] = "ERR " + str(e)[:100]
    print(out)
    # Rename one to empty name, count, restore
    s = sh.FindShape("zero_size_line", 0, 0, True)
    print("found", s.StaticID)
run(go)
