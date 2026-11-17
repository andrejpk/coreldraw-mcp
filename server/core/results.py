"""Turn bridge results (``conn.safe_call`` / ``conn.read_call``) into ToolResults with consistent feedback.

Every failure has an English ``error`` saying what went wrong and, where useful, the next step. A failure of the
call itself also reports ``data.state``: ``busy`` or ``timeout`` (retry later or poll get_coreldraw_status),
``disconnected``, ``not_running`` or ``unavailable`` (CorelDRAW is not reachable), or ``tool_error`` (the request
failed: fix it), plus the document and page the call targeted.

On the wire (``CorelTool``) a failed ToolResult is an MCP error result (``isError: true``) that still carries the
same structured content and text, so hosts flag the failure and clients that read ``structuredContent`` (or
``success``) keep working.

A successful result dict fails anyway, keeping its data, when the tool reports refused targets (nothing changed),
``verification_error`` (one message) or ``verification_errors`` (a list of read-back differences).
"""

from core.models import ToolResult
from fastmcp.tools.function_tool import FunctionTool


def failure(result: dict, **context) -> ToolResult:
    """A failed bridge call: its error, with the bridge state and the call's target in data."""
    data = {"state": result.get("state") or "tool_error"}
    data.update({key: value for key, value in context.items() if value is not None})
    return ToolResult(success=False, error=result.get("error") or "The CorelDRAW call failed", data=data)


def respond(result: dict, message: str | None = None, **context) -> ToolResult:
    """The ToolResult of a bridge call whose result is a dict (or None)."""
    if not result["success"]:
        return failure(result, **context)
    data = result["result"] or {}
    if data.get("refused"):
        return ToolResult(success=False, data=data, error=(
            f"{len(data['refused'])} target(s) cannot be edited (see refused); nothing changed"))
    if error := data.pop("verification_error", None):
        return ToolResult(success=False, data=data, error=error)
    if problems := data.get("verification_errors"):
        return ToolResult(success=False, data=data, error=(
            f"Read-back differs on {len(problems)} item(s) (see verification_errors); the edit is one undo "
            "step: call undo to revert it"))
    return ToolResult.ok(message, **data)


class CorelTool(FunctionTool):
    """A FunctionTool whose failed ToolResult (success false) is sent with MCP isError true, structured data kept."""

    def convert_result(self, raw_value):
        result = super().convert_result(raw_value)
        if isinstance(raw_value, ToolResult) and not raw_value.success:
            result.is_error = True
        return result
