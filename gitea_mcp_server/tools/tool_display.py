"""Tool result formatting entry point.

Thin mirror of ``tools/resource_display.py``.  Tools get their data already
parsed from the HTTP JSON response, so no JSON parsing or formatter dispatch
is needed — only delegation to ``format.py:apply_format``.

Supports the symmetric pattern:
    ``tools/tool_display.py``     - tool result formatting
    ``tools/resource_display.py``  - resource content formatting

Both delegate to ``format.py`` for shared primitives and to
``tools/display.py`` for domain-specific formatters (resources only).
"""

from typing import Any

from gitea_mcp_server.format import apply_format


def format_tool_result(
    data: Any,
    fmt: str,
    detail: str = "full",
    schema: dict[str, Any] | None = None,
) -> Any:
    """Format a tool result for output.

    Tools receive data as already-parsed Python objects (from HTTP JSON),
    so this is a thin wrapper around ``apply_format``.

    Args:
        data: Parsed tool result data (dict or list).
        fmt: Output format -- ``"raw"``, ``"json"``, or ``"markdown"``.
        detail: Output detail -- ``"full"`` (default) or ``"concise"``.
        schema: Optional JSON Schema for ``$ref``-aware collapse.

    Returns:
        ``ToolResult`` with formatted content and structured data.
    """
    return apply_format(data, fmt, detail=detail, schema=schema)


__all__ = [
    "format_tool_result",
]
