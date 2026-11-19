import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
import pythoncom, win32com.client
D = 1610743817
def fs(sh, *args, types=((8, 17), (3, 49), (11, 49), (8, 17))):
    r = sh._oleobj_.InvokeTypes(D, 0, pythoncom.DISPATCH_METHOD, (9, 0), types[:len(args)], *args)
    return win32com.client.Dispatch(r)
def go():
    for name in ("artifacts.cdr", "structure.cdr"):
        doc = open_mine(WORK + "\\" + name)
        sh = doc.Pages(1).Shapes
        out = {}
        for label, fn in {"None dyn": lambda: sh.FindShapes(None, 0, True).Count,
                          "None typed": lambda: fs(sh, None, 0, True).Count,
                          "None typed q": lambda: fs(sh, None, 0, True, "@com.StaticID > 0").Count,
                          "None typed q5": lambda: fs(sh, None, 0, True, "@com.StaticID > 5").Count,
                          }.items():
            try:
                out[label] = fn()
            except Exception as e:
                out[label] = "ERR " + str(e)[:120]
        print(name, out)
run(go)
