"""In-process: stacked_duplicates on sample pages with timings and pairs."""
import sys, time
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK
from tools.find import find_shapes
for d, p in (("knights.cdr", 1), ("knights.cdr", 2), ("knights.cdr", 3), ("goodnews.cdr", 1), ("goodnews.cdr", 2),
             ("artifacts.cdr", 1)):
    t = time.perf_counter()
    r = find_shapes(WORK + "\\" + d, pages=[p], preset="stacked_duplicates", limit=1000)
    print(d, p, r.data["count"], round(time.perf_counter() - t, 2), [(s["id"], s["duplicate_of"]) for s in r.data["shapes"]][:30])
