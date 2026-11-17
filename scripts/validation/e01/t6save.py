import sys, json, shutil; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import Session
D, OUT, TASK = sys.argv[1], sys.argv[2], sys.argv[3]
with Session(r"C:\CorelMcpLab\runs\e01\calls.jsonl") as s:
    s.task = TASK
    i = s.call("get_document_info")["data"]; print("active", i["document_path"])
    assert i["document_path"].lower() == D.lower()
    r = s.call("save_document", {}, note="workaround: SaveAs broken, save in place (run-folder copy)"); print(json.dumps(r, ensure_ascii=False))
    if r.get("success"): shutil.copyfile(D, OUT); print("copied to", OUT)
