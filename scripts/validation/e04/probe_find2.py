import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
import pythoncom
DISPID = 1610743817
RET = (26, (29, 50370176))  # placeholder, use VT_DISPATCH instead
def go():
    for name in ("artifacts.cdr", "structure.cdr"):
        doc = open_mine(WORK + "\\" + name)
        sh = doc.Pages(1).Shapes
        out = {}
        tries = {
            "noargs": lambda: sh.FindShapes(),
            "Missing first": lambda: sh.FindShapes(pythoncom.Missing, 0, True),
            "invoke0": lambda: sh._oleobj_.Invoke(DISPID, 0, pythoncom.DISPATCH_METHOD, True),
            "invokeTypes0": lambda: sh._oleobj_.InvokeTypes(DISPID, 0, pythoncom.DISPATCH_METHOD, (9, 0), ()),
            "invokeTypes q": lambda: sh._oleobj_.InvokeTypes(DISPID, 0, pythoncom.DISPATCH_METHOD, (9, 0),
                                                             ((8, 17), (3, 49), (11, 49), (8, 17)),
                                                             pythoncom.Missing, 0, True, "true"),
            "invokeTypes Empty q": lambda: sh._oleobj_.InvokeTypes(DISPID, 0, pythoncom.DISPATCH_METHOD, (9, 0),
                                                             ((12, 17), (3, 49), (11, 49), (8, 17)),
                                                             pythoncom.Empty, 0, True, "@com.StaticID > 0"),
        }
        for label, fn in tries.items():
            try:
                r = fn()
                import win32com.client
                if not hasattr(r, "Count"):
                    r = win32com.client.Dispatch(r)
                out[label] = r.Count
            except Exception as e:
                out[label] = "ERR " + str(e)[:100]
        print(name, out)
run(go)
