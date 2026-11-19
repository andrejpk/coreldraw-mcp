"""Can a CQL expression evaluated through Shape.Evaluate change a shape? Probes only one shape of my own
artifacts.cdr copy under the run folder (no application- or document-level members)."""
import sys

sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, mine, open_mine, run  # noqa: E402


def go():
    doc = open_mine(WORK + r"\artifacts.cdr")
    assert mine(doc.FullFileName), doc.FullFileName
    layer = doc.Pages(1).Layers("Artifacts")
    shape = layer.Shapes.Item(1)
    before = (layer.Shapes.Count, shape.Name, shape.LeftX, shape.Fill.Type)
    for query in ["@com.Delete", "@com.Delete()", "@com.Duplicate", "@com.Duplicate()", "@com.Move(1, 1)",
                  "@com.OrderToBack", "@com.Name = 'hacked'", "@com.Fill.ApplyNoFill", "@com.Fill.ApplyNoFill()",
                  "@com.ConvertToCurves", "@com.CreateSelection", "@com.Rotate(45)", "@com.SetPosition(0, 0)",
                  "$a = 1", "@com.Name := 'x'"]:
        try:
            print(f"{query!r:40} -> {shape.Evaluate(query)!r}"[:160])
        except Exception as exc:
            print(f"{query!r:40} ERR {str(exc)[60:170]}")
    after = (layer.Shapes.Count, shape.Name, shape.LeftX, shape.Fill.Type)
    print("before", before, "after", after, "unchanged", before == after)


run(go)
