"""Measure the MCP tool catalog: tool count and schema/description size, overall and per module.

Usage (server running on 127.0.0.1:8765): python catalog_size.py [out.json]
Token estimate is chars / 4 (rough; JSON-heavy text tokenizes denser than prose).
"""
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from fastmcp import Client

URL = "http://127.0.0.1:8765/mcp"
SERVER = Path(__file__).resolve().parents[2] / "server"


def module_map():
    """Map tool name -> module by parsing server.py add_tool lines."""
    out = {}
    for line in (SERVER / "server.py").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "mcp.add_tool(" not in line:
            continue
        arg = line.split("mcp.add_tool(", 1)[1]
        if arg.startswith("Tool.from_function("):
            arg = arg.split("(", 1)[1]
        arg = arg.split(",")[0].rstrip(")")
        if "." in arg:
            mod, fn = arg.split(".", 1)
        else:
            mod, fn = "server", arg
        out[fn] = mod
    return out


async def main():
    async with Client(URL, timeout=120) as c:
        tools = await c.list_tools()
    mods = module_map()
    rows = []
    for t in tools:
        d = t.model_dump(mode="json", exclude_none=True)
        full = json.dumps(d, ensure_ascii=False)
        desc = t.description or ""
        schema = json.dumps(t.inputSchema, ensure_ascii=False)
        out_schema = json.dumps(getattr(t, "outputSchema", None) or {}, ensure_ascii=False)
        params = list((t.inputSchema or {}).get("properties", {}))
        rows.append({
            "name": t.name, "module": mods.get(t.name, "?"), "json_chars": len(full),
            "desc_chars": len(desc), "input_schema_chars": len(schema), "output_schema_chars": len(out_schema),
            "params": params, "cjk": any("一" <= ch <= "鿿" for ch in desc),
        })
    per = defaultdict(lambda: {"tools": 0, "json_chars": 0, "desc_chars": 0})
    for r in rows:
        p = per[r["module"]]
        p["tools"] += 1
        p["json_chars"] += r["json_chars"]
        p["desc_chars"] += r["desc_chars"]
    total = sum(r["json_chars"] for r in rows)
    summary = {
        "tools": len(rows), "json_chars": total, "approx_tokens": total // 4,
        "desc_chars": sum(r["desc_chars"] for r in rows),
        "output_schema_chars": sum(r["output_schema_chars"] for r in rows),
        "cjk_descriptions": [r["name"] for r in rows if r["cjk"]],
        "per_module": {k: {**v, "approx_tokens": v["json_chars"] // 4}
                       for k, v in sorted(per.items(), key=lambda kv: -kv[1]["json_chars"])},
        "largest": sorted(rows, key=lambda r: -r["json_chars"])[:10],
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "largest"}, indent=1, ensure_ascii=False))
    for r in summary["largest"]:
        print(f'{r["json_chars"]:6d} {r["module"]:12s} {r["name"]}')
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps({"summary": summary, "tools": rows}, indent=1, ensure_ascii=False),
                                     encoding="utf-8")


asyncio.run(main())
