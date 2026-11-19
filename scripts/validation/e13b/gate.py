r"""E13b shared-instance gate: list open CorelDRAW documents through MCP; exit 1 if any is outside C:\CorelMcpLab."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

LAB = "c:\\corelmcplab\\"
r = call("list_documents")
docs = r["data"]["documents"] if r.get("success") else None
if docs is None:
    print("GATE ERROR", r)
    sys.exit(2)
names = [d.get("document_path") or d.get("title") for d in docs]
outside = [n for d, n in zip(docs, names) if not str(d.get("document_path") or "").lower().startswith(LAB)]
print("open:", names)
if outside:
    print("GATE STOP: documents outside C:\\CorelMcpLab are open:", outside)
    sys.exit(1)
print("GATE OK")
