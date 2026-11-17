"""E14 dev checks through MCP: inspect_text / edit_text / create_shape text on copies in runs\\e14\\dev."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call, show  # noqa: E402

GATE = Path(__file__).resolve().parents[1] / "e13b" / "gate.py"
if subprocess.run([sys.executable, str(GATE)]).returncode:
    sys.exit("gate stop")
DEV = Path(r"C:\CorelMcpLab\runs\e14\dev")
DEV.mkdir(parents=True, exist_ok=True)
P = str(DEV / "text_runs.cdr")
shutil.copy(r"C:\CorelMcpLab\templates\text_runs.cdr", P)
show(call("open_document", path=P), 400)
r = call("inspect_text", document_path=P, page_index=1)
show(r, 6000)
ids = {s["text"][:6]: s["id"] for s in r["data"]["shapes"]}
print(ids)
