"""E13 probe: ShapeRange/Shape/Outline/Effect methods from the type library; behaviors on a screen_job copy."""
import sys
sys.path.insert(0, ".")
sys.path.insert(0, r"C:\CorelMcpLab\wt\e13\scripts\validation\e13")
from lab import conn, open_mine, run, WORK
import pythoncom


def methods(obj):
    ti = obj._oleobj_.GetTypeInfo()
    attr = ti.GetTypeAttr()
    out = []
    for i in range(attr.cFuncs):
        fd = ti.GetFuncDesc(i)
        names = ti.GetNames(fd.memid)
        out.append(names[0] + ("(" + ",".join(names[1:]) + ")" if fd.invkind == 1 else ""))
    return sorted(set(out))


def go():
    doc = open_mine(WORK + r"\screen_job.cdr")
    page = doc.Pages(1)
    s = page.Shapes.Item(1)
    rng = conn.app.CreateShapeRange()
    out = {}
    out["ShapeRange"] = [m for m in methods(rng) if "(" in m]
    out["Shape"] = [m for m in methods(s) if any(k in m for k in ("Weld", "Trim", "Intersect", "Boundary", "Contour", "Break", "Curve", "Duplicate", "Move", "Order", "Combine", "Convert", "Separate", "Clone", "Copy"))]
    out["Outline"] = [m for m in methods(s.Outline) if "(" in m]
    out["Layer"] = [m for m in methods(page.ActiveLayer) if "Create" in m or "Paste" in m or "Move" in m]
    return out


import json
print(json.dumps(run(go), indent=0))
