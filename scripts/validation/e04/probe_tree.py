import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine
def names(obj):
    ti = obj._oleobj_.GetTypeInfo(); ta = ti.GetTypeAttr()
    out = []
    for i in range(ta.cFuncs):
        fd = ti.GetFuncDesc(i); n = ti.GetNames(fd.memid)
        out.append((n[0], fd.invkind, n[1:]))
    return sorted(set((a, tuple(c)) for a, b, c in out))
def go():
    doc = open_mine(WORK + r"\structure.cdr")
    tm = doc.TreeManager
    print("TM", names(tm))
    root = doc.TreeRoot
    print("NODE", names(root))
    pn = doc.Pages(1).TreeNode
    print("page node", pn.Name if hasattr(pn, "Name") else pn)
    import pythoncom
    print("AdviseEvents", [x for x in names(doc) if x[0] in ("AdviseEvents", "UnadviseEvents")])
    print("SR CountAnyOfType", [x for x in names(doc.Pages(1).Shapes.All()) if x[0] in ("CountAnyOfType", "FindAnyOfType")])
run(go)
