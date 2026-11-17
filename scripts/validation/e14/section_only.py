"""Run only the E14 section of accept_mcp.py (dev loop). Usage from server/: python section_only.py <dir>"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
source = (HERE / "accept_mcp.py").read_text(encoding="utf-8")
preamble = source[:source.index("# 0. Document management")]
helpers = source[source.index("async def _catalog():"):source.index("names = {tool.name for tool in asyncio.run(_catalog())}")]
near = source[source.index("def near(a, b"):source.index("def stack_of(")]
section = source[source.index("# 20. E14:"):source.index('print(f"\\n{sum(ok for _, ok in results)}')]
sys.argv = [sys.argv[0], sys.argv[1], "{}"]
(Path(sys.argv[1]) / "fx").mkdir(parents=True, exist_ok=True)
code = preamble + "\nimport asyncio\nimport subprocess\nfrom fastmcp import Client\n" + helpers + near + section + \
    '\nprint(f"\\n{sum(ok for _, ok in results)}/{len(results)} passed")\n'
exec(compile(code, "accept_e14", "exec"))
