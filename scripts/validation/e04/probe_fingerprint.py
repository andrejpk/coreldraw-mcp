"""Old (FindShapes) vs new (toarray) structure fingerprint after out-of-band edits that add NAMED nested shapes.
Runs on fresh copies of structure.cdr in the run folder."""
import shutil, sys
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import RUN, run, open_mine, mine, close_mine
from core import structure as new
import structure_e17a as old  # E17a fingerprint (FindShapes), snapshot for comparison
from core.drawing import walk_shapes

def ids(views):
    return sorted(int(e.shape_id) for v in views for e in v.structure.entries)

def truth(doc):
    return sorted(int(s.StaticID) for layer in doc.Pages(1).AllLayers for s, _ in walk_shapes(layer.Shapes)
                  ) 

def scenario(label, edit):
    out = {}
    for name, mod in (("old", old), ("new", new)):
        path = RUN + rf"\fp_{name}_{label}.cdr"
        shutil.copyfile(r"C:\CorelMcpLab\templates\structure.cdr", path)
        def go():
            doc = open_mine(path)
            page = doc.Pages(1)
            filler = doc.Pages(1).AllLayers.Item(4)
            for k in range(80):  # enough plain shapes that the page is cached (few groups per shape)
                filler.CreateRectangle2(5 + k * 0.01, 5, 0.005, 0.005)
            mod.invalidate()
            mod.page_layers(doc, page)
            before = ids(mod.page_layers(doc, page))
            layers = {l.Name: l for l in page.AllLayers}
            named = {s.Name: s for l in layers.values() for s, _ in walk_shapes(l.Shapes) if s.Name}
            added = edit(doc, layers, named)
            hits = mod.stats()["hits"]
            after = ids(mod.page_layers(doc, page))
            return {"added": added, "cache_hit": mod.stats()["hits"] > hits, "added_seen": int(added) in after}
        out[name] = run(go)
    print(label, out)

def into_powerclip(doc, layers, named):
    r = layers["Groups"].CreateRectangle2(0.1, 0.1, 0.2, 0.2); r.Name = "named_new_in_clip"
    r.AddToPowerClip(named["grp_a"], 0)
    return str(r.StaticID)

def swap_in_group(doc, layers, named):
    named["nest_l2_rect"].Delete()
    r = layers["Groups"].CreateRectangle2(0.1, 0.1, 0.2, 0.2); r.Name = "named_new_in_group"
    r.OrderFrontOf(named["nest_l3"])
    return str(r.StaticID)

scenario("into_powerclip", into_powerclip)
scenario("swap_in_group", swap_in_group)
print("closed", close_mine())
