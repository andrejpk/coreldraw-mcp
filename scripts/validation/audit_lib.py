"""Logged MCP client for agent-eye audits (E01).

Every call is appended to a JSONL log with task label, tool, args, wall time, success, response size and
error, so task tables can be built from the log. Unlike mc.call, args is a dict, so a tool argument named
`name` can be passed. One persistent session is reused for many calls (open with `Session()`).

    from audit_lib import Session
    with Session("C:/CorelMcpLab/runs/e01/calls.jsonl") as s:
        s.task = "T1 kings"
        r = s.call("get_coreldraw_status")
"""
import asyncio
import json
import threading
import time

from fastmcp import Client

URL = "http://127.0.0.1:8765/mcp"


class Session:
    def __init__(self, log_path, url=URL, timeout=900):
        self.log_path, self.url, self.timeout = log_path, url, timeout
        self.task = ""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        self._client = Client(self.url, timeout=self.timeout)
        self._run(self._client.__aenter__())
        return self

    def __exit__(self, *exc):
        self._run(self._client.__aexit__(None, None, None))
        self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def call(self, tool, args=None, note=""):
        args = args or {}
        t = time.perf_counter()
        try:
            r = self._run(self._client.call_tool(tool, args, raise_on_error=False, timeout=self.timeout))
            out = r.structured_content if r.structured_content is not None else {
                "success": not r.is_error, "text": [getattr(x, "text", str(x)) for x in r.content]}
        except Exception as exc:  # transport-level failure
            out = {"success": False, "error": f"transport: {exc!r}"}
        secs = round(time.perf_counter() - t, 3)
        body = json.dumps(out, ensure_ascii=False, default=str)
        rec = {"ts": round(time.time(), 2), "task": self.task, "tool": tool, "args": args, "seconds": secs,
               "success": bool(out.get("success", True)) if isinstance(out, dict) else True,
               "bytes": len(body.encode("utf-8")), "error": out.get("error") if isinstance(out, dict) else None,
               "note": note}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if isinstance(out, dict):
            out["_seconds"] = secs
        return out


def data(r):
    """ToolResult payload: success/message/data/error, whatever the structure."""
    return r.get("data", r) if isinstance(r, dict) else r


def summarize_log(log_path):
    """Per-task: calls, failures, total seconds, bytes."""
    rows = [json.loads(x) for x in open(log_path, encoding="utf-8")]
    out = {}
    for r in rows:
        t = out.setdefault(r["task"], {"calls": 0, "failures": 0, "seconds": 0.0, "bytes": 0, "slowest": ("", 0)})
        t["calls"] += 1
        t["failures"] += 0 if r["success"] else 1
        t["seconds"] = round(t["seconds"] + r["seconds"], 2)
        t["bytes"] += r["bytes"]
        if r["seconds"] > t["slowest"][1]:
            t["slowest"] = (r["tool"], r["seconds"])
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(summarize_log(sys.argv[1]), indent=1))
