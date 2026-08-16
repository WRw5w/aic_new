"""Strict, fail-closed validation helpers for local MCP servers."""
from __future__ import annotations

from typing import Any


class RuntimeValidationError(ValueError):
    """Safe validation failure with no input or exception details."""


SAFE_INVALID_MESSAGE = "Invalid MCP request"
SAFE_INVALID_PARAMS = "Invalid MCP parameters"
SAFE_INVALID_TOOL = "Invalid MCP tool call"


def _object(value: Any, *, required: set[str], optional: set[str], error: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required | (set(value) & optional):
        raise RuntimeValidationError(error)
    if not required.issubset(value):
        raise RuntimeValidationError(error)
    return value


def exact_object(value: Any, *, required: set[str] = frozenset(), optional: set[str] = frozenset(), error: str = SAFE_INVALID_PARAMS) -> dict[str, Any]:
    return _object(value, required=required, optional=optional, error=error)


def string(value: Any, *, nonempty: bool = False, error: str = SAFE_INVALID_PARAMS) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise RuntimeValidationError(error)
    return value


def integer(value: Any, *, positive: bool = False, error: str = SAFE_INVALID_PARAMS) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or (positive and value <= 0):
        raise RuntimeValidationError(error)
    return value


def number(value: Any, *, positive: bool = False, error: str = SAFE_INVALID_PARAMS) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeValidationError(error)
    if positive and value <= 0:
        raise RuntimeValidationError(error)
    return value


def mcp_message(value: Any) -> dict[str, Any]:
    msg = exact_object(
        value,
        required={"jsonrpc", "method"},
        optional={"id", "params"},
        error=SAFE_INVALID_MESSAGE,
    )
    if msg["jsonrpc"] != "2.0":
        raise RuntimeValidationError(SAFE_INVALID_MESSAGE)
    if "id" in msg and (
        not isinstance(msg["id"], (str, int)) or isinstance(msg["id"], bool)
    ):
        raise RuntimeValidationError(SAFE_INVALID_MESSAGE)
    string(msg["method"], nonempty=True, error=SAFE_INVALID_MESSAGE)
    # MCP request params are optional. Current rmcp clients serialize an
    # omitted RequestOptionalParam as JSON null, which is equivalent to no
    # params rather than an invalid object.
    if "params" in msg and msg["params"] is not None:
        exact_object(
            msg["params"],
            optional=set(msg["params"].keys()),
            error=SAFE_INVALID_MESSAGE,
        )
    return msg


def tool_call_params(value: Any) -> dict[str, Any]:
    params = exact_object(
        value,
        required={"name"},
        optional={"arguments", "_meta", "task"},
        error=SAFE_INVALID_TOOL,
    )
    string(params["name"], nonempty=True, error=SAFE_INVALID_TOOL)
    if "arguments" in params:
        arguments = params["arguments"]
        exact_object(
            arguments,
            optional=set(arguments) if isinstance(arguments, dict) else set(),
            error=SAFE_INVALID_TOOL,
        )
    meta = params.get("_meta")
    if meta is not None:
        exact_object(
            meta,
            optional=set(meta) if isinstance(meta, dict) else set(),
            error=SAFE_INVALID_TOOL,
        )
    # This runtime does not implement MCP task augmentation, but current
    # clients may serialize the optional field explicitly as null.
    if params.get("task") is not None:
        raise RuntimeValidationError(SAFE_INVALID_TOOL)
    return params


def list_request_params(value: Any) -> dict[str, Any]:
    """Validate standard paginated MCP list params without accepting extras."""
    params = exact_object(value, optional={"cursor", "_meta"})
    cursor = params.get("cursor")
    if cursor is not None:
        string(cursor, nonempty=True)
    meta = params.get("_meta")
    if meta is not None:
        exact_object(
            meta,
            optional=set(meta) if isinstance(meta, dict) else set(),
        )
    return params


def validate_tool_arguments(value: Any, *, allowed: set[str], required: set[str] = frozenset()) -> dict[str, Any]:
    args = exact_object(value, required=required, optional=allowed - required, error=SAFE_INVALID_PARAMS)
    return args
