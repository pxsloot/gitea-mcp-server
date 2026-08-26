"""Resource content display pipeline.

All resources return raw data. This module provides the formatting
pipeline that shapes and renders that data for agent consumption.

The pipeline is the resource-side counterpart to the tool result pipeline
(``tools/result_pipeline.py``): both delegate to ``format.py`` for shared
formatting primitives and to ``tools/display.py`` for domain-specific
formatters.

Public functions:
    clean_resource_uri - re-exported from ``uri_utils.py``; strip ``{?query}``
        from URI templates
    format_resource_result - unified dual-channel display pipeline
        (JSON parse → collapse → format → ToolResult); the single writer of
        both channels (content authoritative, structured_content mirror)
    format_resource_content - text-only wrapper over format_resource_result
    extract_resource_content - extract text content from ResourceResult
    _make_resource_formatter - resolve a format_hint to a callable formatter
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.format import apply_format, collapse_data
from gitea_mcp_server.tools.display import get_formatter, get_formatter_meta
from gitea_mcp_server.uri_utils import clean_resource_uri

logger = logging.getLogger(__name__)


def extract_resource_content(contents: list[Any] | None, uri: str) -> str:
    """Extract and convert content from resource result."""
    if not contents:
        msg = f"Resource '{uri}' returned no content"
        raise LookupError(msg) from None
    content = contents[0].content
    if isinstance(content, bytes):
        return content.decode("utf-8")
    if isinstance(content, str):
        return content
    return str(content)


def _make_resource_formatter(
    format_hint: str | None,
    detail: str,
    extra: dict[str, Any] | None,
) -> Callable[[Any], str] | None:
    """Create a markdown formatter callable from a ``format_hint`` name.

    Resolves the registered domain formatter (if any) and binds the
    ``detail`` and ``extra`` arguments so the callable matches the
    ``(data) -> str`` signature expected by ``apply_format``.

    Args:
        format_hint: Registered formatter name, or ``None``.
        detail: Output detail level to pass through.
        extra: Extra context dict for formatters that need it.

    Returns:
        A callable ``(data) -> str``, or ``None`` if no formatter found.
    """
    if not format_hint:
        return None
    fn = get_formatter(format_hint)
    if fn is None:
        return None
    meta = get_formatter_meta(format_hint)
    if meta.get("need_extra"):
        return lambda data: fn(data, detail=detail, extra=extra)
    return lambda data: fn(data, detail=detail)


def format_resource_result(  # noqa: PLR0913 - 6 independent display axes
    raw: str,
    fmt: str,
    detail: str = "full",
    schema: dict[str, Any] | None = None,
    format_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> ToolResult:
    """Unified dual-channel display pipeline for resource content.

    All resources (auto-generated and custom) return raw data.  This function
    is the single point where that data is shaped and formatted for output,
    and the single writer of both channels:

    - ``content`` (the text channel) is authoritative and always present.
    - ``structured_content`` mirrors it: the parsed envelope
      ``{"result": <data>}`` for JSON content, ``{"result": raw}`` for
      non-JSON content and ``format=raw``.

    The pipeline:
      1. Parse JSON (non-JSON content passes through unchanged).
      2. Delegate to ``apply_format`` for collapse (detail) and rendering
         (format).  Domain-specific formatters are resolved via
         ``format_hint`` and passed as the ``markdown_formatter`` callback.

    **Error recovery**: The post-parse pipeline (collapse → apply_format →
    domain formatter) is wrapped in a try/except for
    ``(TypeError, AttributeError, ValueError)``.  When a formatting error
    occurs (e.g. schema/data shape mismatch, domain formatter receiving
    unexpected types, non-JSON-serializable data), the error is logged and
    a readable fallback is returned — the raw data wrapped in a JSON code
    fence for markdown, or as ``{"result": raw}`` for JSON output.  This
    prevents unexpected API data shapes from crashing the resource read
    while preserving visibility into what the API returned.

    Args:
        raw: The raw resource content string (JSON or plain text).
        fmt: Output format -- ``"raw"``, ``"json"``, or ``"markdown"``.
        detail: Output detail -- ``"full"`` (default) or ``"concise"``.
        schema: Optional unresolved response schema for ``$ref``-aware
            collapse when ``detail=concise``.
        format_hint: Optional registered formatter name for domain-specific
            markdown rendering.
        extra: Optional context dict passed to formatters that need
            additional parameters (e.g. ``owner``/``repo`` for labels).

    Returns:
        A dual-channel ``ToolResult`` whose ``content`` is authoritative and
        always present, with ``structured_content`` mirroring it.
    """
    if fmt == "raw":
        return ToolResult(
            content=[TextContent(type="text", text=raw)],
            structured_content={"result": raw},
        )

    # Only json and markdown are valid beyond this point; some older
    # tests pass unknown formats expecting raw passthrough.
    if fmt not in ("json", "markdown"):
        return ToolResult(
            content=[TextContent(type="text", text=raw)],
            structured_content={"result": raw},
        )

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Non-JSON content (plain text, markdown) -- pass through.
        text = json.dumps({"result": raw}, indent=2) if fmt == "json" else raw
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": raw},
        )

    # Pre-collapse data for concise so the formatter sees flat strings.
    # This matches the original format_resource_content contract where
    # child objects at depth >= 1 are collapsed to $ref labels.  The
    # formatter (domain or generic) receives already-shaped data.
    if detail == "concise" and schema is not None and isinstance(data, (dict, list)):
        data = collapse_data(data, schema, _depth=0, detail="concise")

    markdown_formatter = _make_resource_formatter(format_hint, detail, extra)
    # When data has been pre-collapsed, pass detail="full" to avoid
    # double-collapse — the formatter already sees flat strings.
    try:
        return apply_format(
            data,
            fmt,
            markdown_formatter=markdown_formatter,
            detail="full" if detail == "concise" else detail,
            schema=schema,
        )
    except (TypeError, AttributeError, ValueError) as exc:
        # Post-parse pipeline failure (e.g. schema/data mismatch,
        # domain formatter receiving unexpected types, non-JSON-
        # serializable data).  Fall back to a readable representation
        # rather than letting the error propagate to the agent.
        logger.warning(
            "Display pipeline recovered from %s: %s. fmt=%s, format_hint=%s",
            type(exc).__name__,
            exc,
            fmt,
            format_hint,
        )
        if fmt == "json":
            return ToolResult(
                content=[TextContent(type="text", text=json.dumps({"result": raw}, indent=2))],
                structured_content={"result": raw},
            )
        # Markdown fallback: wrap in code fence to preserve readability.
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        f"```json\n{raw}\n```\n\n"
                        f"*Note: formatting failed ({type(exc).__name__}), "
                        "showing raw data.*\n"
                    ),
                )
            ],
            structured_content={"result": raw},
        )


def format_resource_content(  # noqa: PLR0913 - 6 independent display axes
    raw: str,
    fmt: str,
    detail: str = "full",
    schema: dict[str, Any] | None = None,
    format_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Text-only convenience wrapper over :func:`format_resource_result`.

    Returns the ``content`` text of the dual-channel result.  Prefer
    ``format_resource_result`` when you need the full contract (both
    channels); this wrapper exists for callers that only render text.

    Args:
        raw: The raw resource content string (JSON or plain text).
        fmt: Output format -- ``"raw"``, ``"json"``, or ``"markdown"``.
        detail: Output detail -- ``"full"`` (default) or ``"concise"``.
        schema: Optional unresolved response schema for ``$ref``-aware
            collapse when ``detail=concise``.
        format_hint: Optional registered formatter name for domain-specific
            markdown rendering.
        extra: Optional context dict passed to formatters that need
            additional parameters.

    Returns:
        The formatted content text.
    """
    result = format_resource_result(
        raw, fmt, detail=detail, schema=schema, format_hint=format_hint, extra=extra
    )
    if result.content:
        for c in result.content:
            if isinstance(c, TextContent):
                return c.text
    return ""


__all__ = [
    "clean_resource_uri",
    "extract_resource_content",
    "format_resource_content",
    "format_resource_result",
]
