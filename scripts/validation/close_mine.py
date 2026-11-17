"""Close only fixture documents: saved under the scratchpad fx folder, or untitled docs whose
only content is the exploration text 'AB  CD' created by explore2.py."""
import sys; sys.path.insert(0, ".")
from core.connection import get_connection
conn = get_connection()
def mine(d):
    if d.FilePath:
        return "scratchpad" in d.FilePath.lower()
    if d.Pages.Count != 1: return False
    shapes = d.Pages(1).Shapes
    return shapes.Count == 1 and shapes.Item(1).Type == 6 and shapes.Item(1).Text.Story.Text == "AB  CD"
def run():
    app = conn.app; closed, kept = [], []
    for i in range(app.Documents.Count, 0, -1):
        d = app.Documents.Item(i)
        if mine(d):
            closed.append(d.FileName or "untitled fixture"); d.Dirty = False; d.Close()
        else:
            kept.append(d.FileName or "untitled")
    return {"closed": closed, "kept": kept}
print(conn.safe_call(run))
