import sys, time, json; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import RUN, Session
t = time.time()
with Session(RUN + r"\calls.jsonl") as s:
    s.task = "wedge recovery"
    while True:
        r = s.call("get_coreldraw_status")
        if (r.get("data") or {}).get("state") == "ready": break
        print(round(time.time() - t), json.dumps(r)[:200]); time.sleep(15)
print("ready after", round(time.time() - t), "s", r)
