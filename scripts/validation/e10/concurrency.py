r"""Does render_plates block the bridge while Ghostscript runs? Polls get_coreldraw_status during one render.

    python concurrency.py <doc.cdr> <page> <out_dir>

Prints the render's phase timings and the status timeline (busy = the bridge is serving render_plates' COM work;
ready = other tools can use CorelDRAW, i.e. the Ghostscript and analysis phases do not hold the bridge).
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

document, page, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
result = {}
t0 = time.perf_counter()


def run():
    result["r"] = call("render_plates", document_path=document, page_index=page, output_dir=out)
    result["end"] = time.perf_counter() - t0


thread = threading.Thread(target=run)
thread.start()
timeline = []
while thread.is_alive():
    s = call("get_coreldraw_status")
    state = (s.get("data") or {}).get("state")
    running = ((s.get("data") or {}).get("running") or {}).get("operation")
    timeline.append((round(time.perf_counter() - t0, 2), state, running, s.get("_seconds")))
    time.sleep(0.25)
thread.join()
print("render seconds", result["r"].get("_seconds"), result["r"]["data"]["seconds"])
for row in timeline:
    print(row)
ready_after = [t for t, state, _, _ in timeline if state == "ready"]
print("first ready at", ready_after[:1], "render ended", round(result["end"], 2))
