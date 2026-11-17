import sys, time
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
def go():
    doc = open_mine(WORK + r"\structure.cdr")
    a = doc.Pages(1).Shapes.Item(1)
    for q in ["@com.Layer.Shapes.count()", "@com.Layer.Shapes.convert($item.StaticID)", "@com.Layer.Shapes.convert($item)",
              "@com.Layer.Shapes.filter($item.StaticID > 0)", "@com.Layer.Shapes.filter(true).count()",
              "@com.Layer.Shapes.item(1).StaticID", "@com.Layer.Shapes.first.StaticID", "@com.Layer.Shapes.toarray()",
              "@com.Layer.Shapes.length", "@com.Layer.Shapes.Count", "@com.Layer.Shapes.convert(1)",
              "@com.Layer.Shapes.max($item.StaticID)", "@com.Layer.Shapes.Item(1).StaticID",
              "array(1,2).convert($item*2)", "@com.Layer.Shapes.exists($item.StaticID > 0)",
              "@com.Layer.Shapes.any($item.StaticID > 0)",
              "@com.Layer.Shapes.reverse()", "@com.Layer.Shapes.sum()"]:
        try:
            print(q, "->", a.Evaluate(q))
        except Exception as e:
            print(q, "ERR", str(e)[60:170])
run(go)
