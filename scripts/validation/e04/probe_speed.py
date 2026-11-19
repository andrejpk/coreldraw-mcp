import sys, time
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine, timed
from core.structure import page_layers, read_entries, query_path
REC = ["$item.Type", "$item.Name", "array($item.LeftX, $item.BottomY, $item.RightX, $item.TopY)", "$item.Visible",
       "$item.Fill.Type", "iif($item.Fill.Type = 1, array($item.Fill.UniformColor.Type, $item.Fill.UniformColor.Name), 0)",
       "$item.Outline.Type", "iif($item.Outline.Type = 0, 0, $item.Outline.Width)", "$item.OverprintFill", "$item.OverprintOutline"]
def go():
    doc = open_mine(WORK + r"\goodnews.cdr")
    page = doc.Pages(1)
    views = page_layers(doc, page)
    v = [x for x in views if x.name == "Layer 1"][0]
    ents = v.structure.entries
    grp = v.structure.groups[0]
    kids = [e for e in ents if e.parent == grp[0]]
    print("kids", len(kids))
    rows, s = timed(lambda: [r for i in range(0, len(kids), 2000) for r in read_entries(v, kids[i:i+2000], REC)])
    print("read_entries", len(rows), s)
    p = query_path(grp[0], v.ordinal)
    leaf = "array($item.StaticID, " + ", ".join(REC) + ")"
    out, s = timed(v.anchor.Evaluate, f"{p}.Shapes.toarray().convert({leaf})")
    print("toarray", len(out), s, out[0])
    print("same", [tuple(r) for r in rows[:3]] == [tuple(r[1:]) for r in out[:3]], rows[0])
    # range: slices of a toarray
    out2, s = timed(v.anchor.Evaluate, f"{p}.Shapes.toarray().convert($item.StaticID)")
    print("ids only", s)
run(go)
