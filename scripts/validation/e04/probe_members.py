import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine
def names(obj):
    ti = obj._oleobj_.GetTypeInfo(); ta = ti.GetTypeAttr()
    return sorted({ti.GetNames(ti.GetFuncDesc(i).memid)[0] for i in range(ta.cFuncs)})
def go():
    doc = open_mine(WORK + r"\artifacts.cdr")
    print("DOC", names(doc))
    print("PAGE", names(doc.Pages(1)))
    print("SHAPES", names(doc.Pages(1).Shapes))
    print("SR", names(doc.Pages(1).Shapes.All()))
run(go)
