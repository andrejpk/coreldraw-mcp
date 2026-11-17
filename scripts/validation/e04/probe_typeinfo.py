import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine
import pythoncom

def go():
    doc = open_mine(WORK + r"\artifacts.cdr")
    sh = doc.Pages(1).Shapes
    ti = sh._oleobj_.GetTypeInfo()
    ta = ti.GetTypeAttr()
    for i in range(ta.cFuncs):
        fd = ti.GetFuncDesc(i)
        names = ti.GetNames(fd.memid)
        if names[0] in ("FindShapes", "FindShape", "All", "AllExcluding", "Range"):
            print(names, fd.memid, fd.invkind, [(a[0], a[1]) for a in fd.args], fd.rettype, fd.cParamsOpt)
            for a in fd.args: print("   ", a)
run(go)
