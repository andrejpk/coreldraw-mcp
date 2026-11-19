"""The result model every tool returns."""

from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """success; message (optional summary); data (the result, or failure details); error (what went wrong and
    what to do next). core.results builds these from bridge results."""

    success: bool
    message: str | None = None
    data: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def ok(cls, msg: str | None = None, **data) -> "ToolResult":
        return cls(success=True, message=msg, data=data if data else None)

    @classmethod
    def fail(cls, err: str, msg: str | None = None) -> "ToolResult":
        return cls(success=False, error=err, message=msg)
