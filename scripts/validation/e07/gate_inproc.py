r"""In-process shared-instance gate (server stopped): print open documents; exit 1 if any is outside C:\CorelMcpLab."""
import sys

sys.path.insert(0, ".")
from core.connection import get_connection  # noqa: E402

LAB = "c:" + chr(92) + "corelmcplab" + chr(92)


def gate():
    def run():
        app = get_connection().app
        return [app.Documents.Item(i).FullFileName or app.Documents.Item(i).Title
                for i in range(1, app.Documents.Count + 1)]
    r = get_connection().read_call(run)
    if not r["success"]:
        print("GATE ERROR", r)
        sys.exit(2)
    outside = [n for n in r["result"] if not n.lower().startswith(LAB)]
    print("open:", r["result"])
    if outside:
        print("GATE STOP: documents outside", LAB, "are open:", outside)
        sys.exit(1)
    print("GATE OK")


if __name__ == "__main__":
    gate()
