"""Standard MCP tool response envelope. See docs/aidlc/SPEC.md sec 8."""

from __future__ import annotations

import uuid
from typing import Any


def ok(data: Any = None, request_id: str | None = None) -> dict[str, Any]:
    return {
        "success": True,
        "error_code": None,
        "error_message": None,
        "retryable": False,
        "request_id": request_id or str(uuid.uuid4()),
        "data": data,
    }


def err(
    error_code: str,
    error_message: str,
    retryable: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": error_code,
        "error_message": error_message,
        "retryable": retryable,
        "request_id": request_id or str(uuid.uuid4()),
        "data": None,
    }
