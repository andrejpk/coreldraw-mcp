"""In-process: every preset on the given run-folder copies (default artifacts.cdr), with timings."""
import json
import sys
import time

sys.path.insert(0, r"C:\CorelMcpLab\wt\e04\scripts\validation\e04")
from lab import WORK, open_mine, run  # noqa: E402

from tools.find import find_shapes  # noqa: E402

PRESETS = ["zero_size", "invisible", "stacked_duplicates", "off_page", "hairline", "empty_text", "tiny",
           "hidden_printable_content", "registration_on_art"]
names = sys.argv[1:] or ["artifacts.cdr"]
for name in names:
    path = WORK + "\\" + name
    pages = run(lambda path=path: open_mine(path).Pages.Count)
    for page in range(1, pages + 1):
        for preset in PRESETS:
            t = time.perf_counter()
            r = find_shapes(path, pages=[page], preset=preset, limit=12)
            seconds = time.perf_counter() - t
            if not r.success:
                print(name, page, preset, "FAIL", r.error)
                continue
            d = r.data
            print(f"{name} p{page} {preset:25} {seconds:6.2f}s count {d['count']:5} {d['counts']} "
                  f"{json.dumps(d['summary'])[:120]}")
            for s in d["shapes"][:12 if name == "artifacts.cdr" else 3]:
                print("     ", json.dumps(s, ensure_ascii=False)[:200])
