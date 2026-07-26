"""Resource content display pipeline.

All resources return raw data. This module provides the formatting
pipeline that shapes and renders that data for agent consumption.

The pipeline is the resource-side counterpart to ``tools/tool_display.py``:
both delegate to ``format.py`` for shared formatting primitives and to
``tools/display.py`` for domain-specific formatters.

Public functions:
    _clean_resource_uri - strip ``{?query}`` params from URI templates for display
    _format_resource_content - unified display pipeline (JSON parse → collapse → format)
    _extract_resource_content - extract text content from ResourceResult
    _make_resource_formatter - resolve a format_hint to a callable formatter
"""

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from mcp.types import TextContent

from gitea_mcp_server.format import _collapse_data, apply_format
from gitea_mcp_server.tools.display import get_formatter, get_formatter_meta

logger = logging.getLogger(__name__)


def _clean_resource_uri(uri: str) -> str:
    """Strip RFC 6570 form-style query parameters from a resource URI for display.

    Resource templates use ``{?param}`` syntax internally so FastMCP routes
    query-string parameters to the handler.  The display-layer URI is cleaned
    to show a clean template without ``{?param}`` — agents discover available
    optional parameters via the ``optional_params`` metadata field instead.

    Example:
        ``gitea://repos/{owner}/{repo}/issues{?state}`` →
        ``gitea://repos/{owner}/{repo}/issues``

    Note:
        The regex only strips ``{?...}`` when it appears at the **end** of the
        URI (``$`` anchor).  This assumes query params are always the last
        segment in a URI template — a convention enforced by convention, not
        code.  If a future URI template places ``{?param}`` before trailing
        path segments, this function must be updated.

    Args:
        uri: The raw URI template from FastMCP registration.

    Returns:
        Cleaned URI with ``{?...}`` suffix removed.
    """
    return re.sub(r"\{\?[^}]+\}$", "", uri)


def _extract_resource_content(contents: list[Any] | None, uri: str) -> str:
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


def _format_resource_content(  # noqa: PLR0913, PLR0911 - 6 independent display axes, 7 return paths handle fmt fallback
    raw: str,
    fmt: str,
    detail: str = "full",
    schema: dict[str, Any] | None = None,
    format_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Unified display pipeline for resource content.

    All resources (auto-generated and custom) return raw data.  This function
    is the single point where that data is shaped and formatted for output.

    The pipeline:
      1. Parse JSON (non-JSON content passes through unchanged).
      2. Delegate to ``apply_format`` for collapse (detail) and rendering
         (format).  Domain-specific formatters are resolved via
         ``format_hint`` and passed as the ``markdown_formatter`` callback.

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
        Formatted content string.
    """
    if fmt == "raw":
        return raw

    # Only json and markdown are valid beyond this point; some older
    # tests pass unknown formats expecting raw passthrough.
    if fmt not in ("json", "markdown"):
        return raw

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Non-JSON content (plain text, markdown) -- pass through.
        if fmt == "json":
            return json.dumps({"result": raw}, indent=2)
        return raw

    # Pre-collapse data for concise so the formatter sees flat strings.
    # This matches the original _format_resource_content contract where
    # child objects at depth >= 1 are collapsed to $ref labels.  The
    # formatter (domain or generic) receives already-shaped data.
    if detail == "concise" and schema is not None and isinstance(data, (dict, list)):
        data = _collapse_data(data, schema, _depth=0, detail="concise")

    markdown_formatter = _make_resource_formatter(format_hint, detail, extra)
    # When data has been pre-collapsed, pass detail="full" to avoid
    # double-collapse — the formatter already sees flat strings.
    result = apply_format(
        data, fmt,
        markdown_formatter=markdown_formatter,
        detail="full" if detail == "concise" else detail,
        schema=schema,
    )
    if result.content:
        for c in result.content:
            if isinstance(c, TextContent):
                return c.text
        return ""
    return ""


__all__ = [
    "_clean_resource_uri",
    "_extract_resource_content",
    "_format_resource_content",
    "_make_resource_formatter",
]
