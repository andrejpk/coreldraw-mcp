import sys, time
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine
from core import structure as st
def go():
    doc = open_mine(WORK + r"\goodnews.cdr")
    page = doc.Pages(1)
    st.invalidate(); st.page_layers(doc, page)
    entry = next(iter(st._cache.values()))
    for rep in range(3):
        t = time.perf_counter(); state = st._page_state(page); a = time.perf_counter() - t
        times = []
        for q, exp in entry.checks:
            t = time.perf_counter(); v = state.anchor.Evaluate(q); times.append((round(time.perf_counter() - t, 3), q[:70], list(v), exp))
        t = time.perf_counter(); st.page_layers(doc, page); total = time.perf_counter() - t
        print("state", round(a, 3), "total", round(total, 3))
        for x in sorted(times, reverse=True)[:4]: print("  ", x)
run(go)
