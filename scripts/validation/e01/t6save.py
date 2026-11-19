import sys, json, shutil; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import RUN, Session
D, OUT, TASK = sys.argv[1], sys.argv[2], sys.argv[3]
with Session(RUN + r"\calls.jsonl") as s:
    s.task = TASK
    r = s.call("save_document", {"document_path": D, "save_as_path": OUT}); print(json.dumps(r, ensure_ascii=False))
    print("on disk", __import__("os").path.getsize(OUT) if r.get("success") else None)
