"""textlist.py <out.json>  (run with cwd = a server dir; old or new code)."""
import json, os, sys, time
sys.path.insert(0, ".")
from core.connection import get_connection
from core.targets import find_documents
from tools import document
conn = get_connection()
S = r"C:\CorelMcpLab\runs\e17a\s"
JOBS = [("53210 - ME-MI Good News Club - SCREENS.cdr", [2]), ("51019 - Kings High School 2021 Shirt Order - SCREENS.cdr", [1, 2, 3]),
        ("53973 - Jump with the Stars Staff and Participants 2026 - SCREENS.cdr", [1, 2, 3]), ("Knights Striped HEad.cdr", [1, 2, 3]),
        ("text_runs.cdr", [1]), ("screen_job.cdr", [1, 2]), ("structure.cdr", [1]), ("artifacts.cdr", [1])]
out = {}
for name, pages in JOBS:
    path = os.path.join(S, name)
    conn.safe_call(lambda: None if find_documents(conn.app, path) else conn.app.OpenDocument(path))
    pc = conn.safe_call(lambda: find_documents(conn.app, path)[0].Pages.Count)["result"]
    for p in pages:
        if p > pc: continue
        t = time.perf_counter(); r = document.list_all_text_shapes(page_index=p, document_path=path)
        out[f"{name}:{p}"] = {"seconds": round(time.perf_counter() - t, 2), "success": r.success,
                              "data": r.data if r.success else r.error}
        print(name, p, out[f"{name}:{p}"]["seconds"], r.success, (r.data or {}).get("total"), flush=True)
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), default=str)
