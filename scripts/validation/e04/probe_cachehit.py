"""New fingerprint: cache hits on unchanged pages and validation cost, all sample/template copies."""
import sys, time
sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, run, open_mine
from core import structure as st
DOCS = ["kings.cdr", "knights.cdr", "jump.cdr", "goodnews.cdr", "artifacts.cdr", "structure.cdr", "effects.cdr",
        "screen_job.cdr", "text_runs.cdr", "layers.cdr", "colors.cdr", "big_flat.cdr"]
def go():
    for name in DOCS:
        doc = open_mine(WORK + "\\" + name)
        for i in range(1, doc.Pages.Count + 1):
            page = doc.Pages(i)
            st.invalidate()
            t = time.perf_counter(); st.page_layers(doc, page); walk = time.perf_counter() - t
            h = st.stats()["hits"]
            t = time.perf_counter(); st.page_layers(doc, page); check = time.perf_counter() - t
            entry = next(iter(st._cache.values()), None)
            print(f"{name:15} p{i} walk {walk:.3f}s  revalidate {check:.3f}s  hit {st.stats()['hits'] > h}  "
                  f"checks {None if entry is None or entry.checks is None else len(entry.checks)}")
run(go)
