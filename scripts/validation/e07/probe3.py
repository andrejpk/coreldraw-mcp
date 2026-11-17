r"""E07 probe 3 (server stopped): which layer draws on top (pixels), local and master layers."""
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_inproc import gate  # noqa: E402
gate()
from core.connection import get_connection  # noqa: E402
from core.targets import activate_document  # noqa: E402
from PIL import Image  # noqa: E402
RUN = Path(r"C:\CorelMcpLab\runs\e07\probe")
P = str(RUN / "stack.cdr")
shutil.copyfile(r"C:\CorelMcpLab\templates\layers.cdr", P)
conn = get_connection()


def run():
    app = conn.app
    doc = app.OpenDocument(P)
    try:
        activate_document(app, doc)
        pg = doc.Pages(1)
        for l in pg.AllLayers:
            if not l.IsSpecialLayer:
                l.Visible = False
        by = {l.Name: l for l in pg.AllLayers if not l.IsSpecialLayer}
        res = {"order": [l.Name for l in pg.AllLayers]}
        colors = {"Locked": (255, 0, 0), "Art": (0, 0, 255), "Master Marks": (0, 255, 0)}
        for i, (name, rgb) in enumerate(colors.items()):
            l = by[name]
            l.Visible = True
            l.Editable = True
            s = l.CreateRectangle2(1 + i * 0.5, 1 + i * 0.5, 3, 3)
            s.Fill.UniformColor.RGBAssign(*rgb)
            s.Outline.SetNoOutline()
        path = str(RUN / "stack.png")
        f = doc.ExportBitmap(path, 802, 1, 4, 0, 0, 20, 20, 1, False, False, True, False, 0, None, None)
        f.Finish()
        img = Image.open(path).convert("RGB")
        w, h = img.size
        x, y = 2.5, 2.5  # inside all three
        res["pixel"] = img.getpixel((int(x / pg.SizeWidth * w), int(h - y / pg.SizeHeight * h)))
        res["size"] = (w, h)
        return res
    finally:
        doc.Dirty = False
        doc.Close()


r = conn.safe_call(run)
print(json.dumps(r, default=str))
