import sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, open_mine, run
def go():
    doc = open_mine(WORK + r"\structure.cdr")
    page = doc.Pages(1)
    a = page.AllLayers.Item(4).Shapes.Item(1)  # PowerClip layer
    b = page.AllLayers.Item(5).Shapes.Item(1)  # Groups layer
    print(page.AllLayers.Item(4).Name, page.AllLayers.Item(5).Name, "layer4 shape in front of layer5 shape:", a.OrderIsInFrontOf(b))
    print("Layers(1)", page.Layers.Item(1).Name, "Layers(last)", page.Layers.Item(page.Layers.Count).Name)
    l1 = page.AllLayers.Item(5)
    s1, s2 = l1.Shapes.Item(1), l1.Shapes.Item(2)
    print("item1 in front of item2", s1.OrderIsInFrontOf(s2))
run(go)
