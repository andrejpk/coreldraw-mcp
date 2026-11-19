r"""Development harness: run only the E07+E06 section of accept_mcp.py (server running). Usage: python accept_section.py <dir>"""
import asyncio, shutil, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mc import call  # noqa: E402
assert subprocess.run([sys.executable, str(HERE.parent / "e13b" / "gate.py")]).returncode == 0
TEMPLATES = Path(r"C:\CorelMcpLab\templates")
FX = str(Path(sys.argv[1]) / "fx")
Path(FX).mkdir(parents=True, exist_ok=True)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))


async def _catalog():
    from fastmcp import Client
    async with Client("http://127.0.0.1:8765/mcp") as c:
        return await c.list_tools()


src = (HERE.parent / "accept_mcp.py").read_text(encoding="utf-8")
snap = src[src.index("def snapshot(path, page=1):"):src.index("SJ, CO, TR, SU, EF, LA, AR =")]
section = src[src.index("# E07+E06:"):src.rindex("print(f")]
exec(snap + "\n" + section, globals())
print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
