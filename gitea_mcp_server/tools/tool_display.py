"""Tool result formatting entry point with error recovery.

Mirrors the resource-side pattern from ``tools/resource_display.py``.
Tools get their data already parsed from the HTTP JSON response, so no
JSON parsing or formatter dispatch is needed — only delegation to
``format.py:apply_format`` with an added error recovery layer.

**Error recovery**: Wraps ``apply_format`` in ``try/except (TypeError,
AttributeError, ValueError)``, mirroring the resource-side pattern in
``format_resource_content``.  When a formatting error occurs, the error is
logged at WARNING and a readable fallback is returned — raw data wrapped in a
JSON code fence for markdown, or ``{"result": <safe_data>}`` for JSON output.

Supports the symmetric pattern:
    ``tools/tool_display.py``     - tool result formatting
    ``tools/resource_display.py``  - resource content formatting

Both delegate to ``format.py`` for shared primitives and to
``tools/display.py`` for domain-specific formatters (resources only).
"""

import json
import logging
from typing import Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.format import apply_format

logger = logging.getLogger(__name__)


def format_tool_result(
    data: Any,
    fmt: str,
    detail: str = "full",
    schema: dict[str, Any] | None = None,
) -> ToolResult:
    """Format a tool result for output.

    Tools receive data as already-parsed Python objects (from HTTP JSON),
    so this is a thin wrapper around ``apply_format`` with error recovery.

    Args:
        data: Parsed tool result data (dict or list).
        fmt: Output format -- ``"raw"``, ``"json"``, or ``"markdown"``.
        detail: Output detail -- ``"full"`` (default) or ``"concise"``.
        schema: Optional JSON Schema for ``$ref``-aware collapse.

    Returns:
        ``ToolResult`` with formatted content and structured data.
    """
    try:
        return apply_format(data, fmt, detail=detail, schema=schema)
    except (TypeError, AttributeError, ValueError) as exc:
        logger.warning(
            "Display pipeline recovered from %s: %s. fmt=%s, detail=%s",
            type(exc).__name__,
            exc,
            fmt,
            detail,
        )
        # Best-effort string representation for fallback text.
        try:
            data_str = json.dumps(data, indent=2, default=str)
        except (TypeError, ValueError):
            data_str = str(data)

        if fmt == "json":
            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps({"result": data_str}, indent=2),
                    ),
                ],
                structured_content={"result": data_str},
            )

        # Markdown fallback: wrap in code fence to preserve readability.
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        f"```json\n{data_str}\n```\n\n"
                        f"*Note: formatting failed ({type(exc).__name__}), "
                        "showing raw data.*\n"
                    ),
                ),
            ],
            structured_content={"result": data_str},
        )


__all__ = [
    "format_tool_result",
]
