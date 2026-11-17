"""Measure the MCP tool catalog: tool count and schema/description size, overall and per module.

Usage (server running on 127.0.0.1:8765): python catalog_size.py [out.json] [--budget]
Token estimate is chars / 4 (rough; JSON-heavy text tokenizes denser than prose).

json_chars is the whole list_tools entry per tool (name, title, description, schemas, annotations, meta).
model_chars is what a host such as Claude Code sends the model per tool: name, description and input schema.
instructions_chars is the server instructions (sent once per session, not per tool).
--budget exits 1 when the catalog exceeds BUDGET (accept_mcp.py runs the same check).
"""
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from fastmcp import Client

URL = "http://127.0.0.1:8765/mcp"
SERVER = Path(__file__).resolve().parents[2] / "server"
# E19 result (67 tools): 55,422 JSON chars (~13.9k tokens; 85,851 before), model-facing 48,808 chars, instructions
# 1,128 chars, largest tool 2,394 chars (find_shapes). E14 (60 tools): 54,070 JSON chars, model-facing 48,192.
# About 10 % headroom; raise it deliberately, with the reason in
# the results file, when an experiment adds capability.
BUDGET = {"json_chars": 61_000, "model_chars": 54_000, "instructions_chars": 1_400, "max_tool_json_chars": 2_700}


def module_map():
    """Map tool name -> module by parsing the registrations in server.py (add(module.fn, ...))."""
    out = {}
    for line in (SERVER / "server.py").read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*add\((?:(\w+)\.)?(\w+)\b", line)
        if match:
            out[match.group(2)] = match.group(1) or "server"
    return out


async def measure(url=URL):
    async with Client(url, timeout=120) as c:
        tools = await c.list_tools()
        instructions = c.instructions or ""
    mods = module_map()
    rows = []
    for t in tools:
        d = t.model_dump(mode="json", exclude_none=True)
        desc = t.description or ""
        schema = json.dumps(t.input_schema, ensure_ascii=False)
        out_schema = json.dumps(t.output_schema or {}, ensure_ascii=False) if t.output_schema else ""
        rows.append({
            "name": t.name, "module": mods.get(t.name, "?"),
            "json_chars": len(json.dumps(d, ensure_ascii=False)),
            "model_chars": len(json.dumps({"name": t.name, "description": desc, "input_schema": t.input_schema},
                                          ensure_ascii=False)),
            "desc_chars": len(desc), "input_schema_chars": len(schema), "output_schema_chars": len(out_schema),
            "params": list((t.input_schema or {}).get("properties", {})),
            "annotations": (t.annotations.model_dump(exclude_none=True) if t.annotations else {}),
            "cjk": any("一" <= ch <= "鿿" for ch in desc),
        })
    per = defaultdict(lambda: {"tools": 0, "json_chars": 0, "model_chars": 0, "desc_chars": 0})
    for r in rows:
        p = per[r["module"]]
        p["tools"] += 1
        for key in ("json_chars", "model_chars", "desc_chars"):
            p[key] += r[key]
    total = sum(r["json_chars"] for r in rows)
    model = sum(r["model_chars"] for r in rows)
    return {
        "tools": len(rows), "json_chars": total, "approx_tokens": total // 4,
        "model_chars": model, "model_approx_tokens": model // 4,
        "instructions_chars": len(instructions),
        "desc_chars": sum(r["desc_chars"] for r in rows),
        "input_schema_chars": sum(r["input_schema_chars"] for r in rows),
        "output_schema_chars": sum(r["output_schema_chars"] for r in rows),
        "max_tool_json_chars": max((r["json_chars"] for r in rows), default=0),
        "cjk_descriptions": [r["name"] for r in rows if r["cjk"]],
        "without_annotations": sum(1 for r in rows if not r["annotations"]),
        "per_module": {k: {**v, "approx_tokens": v["json_chars"] // 4}
                       for k, v in sorted(per.items(), key=lambda kv: -kv[1]["json_chars"])},
        "largest": sorted(rows, key=lambda r: -r["json_chars"])[:10],
    }, rows


def over_budget(summary):
    """The budget lines the catalog exceeds (empty when within budget)."""
    return [f"{key} {summary[key]} > {limit}" for key, limit in BUDGET.items() if summary[key] > limit]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    summary, rows = asyncio.run(measure())
    print(json.dumps({k: v for k, v in summary.items() if k != "largest"}, indent=1, ensure_ascii=False))
    for r in summary["largest"]:
        print(f'{r["json_chars"]:6d} {r["module"]:12s} {r["name"]}')
    if args:
        Path(args[0]).write_text(json.dumps({"summary": summary, "tools": rows}, indent=1, ensure_ascii=False),
                                 encoding="utf-8")
    if "--budget" in sys.argv:
        problems = over_budget(summary)
        print("BUDGET " + ("FAIL: " + "; ".join(problems) if problems else f"OK {BUDGET}"))
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
