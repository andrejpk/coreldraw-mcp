"""Which CQL geometry signatures exist for every shape type (duplicate confirmation), and layer/z order."""
import sys

sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, open_mine, run  # noqa: E402

from core.structure import page_layers, read_entries  # noqa: E402


def go():
    for name in ("artifacts.cdr", "text_runs.cdr", "effects.cdr", "structure.cdr"):
        doc = open_mine(WORK + "\\" + name)
        page = doc.Pages(1)
        views = page_layers(doc, page)
        print(name, [(v.ordinal, v.name, v.master) for v in views])
        for view in views:
            for entry in view.structure.entries[:6]:
                row = []
                for expression in ("$item.ZOrder", "$item.RotationAngle", "$item.DisplayCurve.Nodes.Count",
                                   "$item.DisplayCurve.Length", "$item.DisplayCurve.SubPaths.Count",
                                   "$item.DisplayCurve.Nodes.toarray().convert(array($item.PositionX, $item.PositionY)).count()",
                                   "$item.Curve.Nodes.Count", "$item.Effects.Count", "$item.Transparency.Type"):
                    try:
                        row.append(read_entries(view, [entry], [expression])[0][0])
                    except Exception as exc:
                        row.append("ERR " + str(exc)[-60:-30])
                print("  ", view.name, entry.shape_id, entry.type, row)


run(go)
