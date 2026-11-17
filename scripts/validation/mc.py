import asyncio, json, time
from fastmcp import Client
URL = "http://127.0.0.1:8765/mcp"
def call(name, **args):
    async def go():
        async with Client(URL, timeout=900) as c:
            r = await c.call_tool(name, args, raise_on_error=False, timeout=900)
            return r.structured_content if r.structured_content is not None else [getattr(x, "text", x) for x in r.content]
    t = time.perf_counter(); out = asyncio.run(go())
    if isinstance(out, dict):
        out["_seconds"] = round(time.perf_counter() - t, 2)
    return out
def show(x, n=4000):
    print(json.dumps(x, indent=1, ensure_ascii=False, default=str)[:n])
