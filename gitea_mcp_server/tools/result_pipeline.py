"""Single result pipeline for every tool.

Executors return raw data only — a small :class:`ExecutionResult` (data,
total_count, result shape).  One result pipeline then applies:

    raw result → shape → paginate → format → ToolResult

- **shape**    — wrap in ``{"result": ...}``; classify the result shape
- **paginate** — slice, envelope (``has_more``/``next_offset``/``total_count``),
  ``fetch_all``
- **format**   — json/markdown/raw + detail + formatter + error recovery

The pipeline is the **single writer of both channels**: ``content`` (the text
channel) is authoritative and always present; ``structured_content`` is an
optional mirror that duplicates it.  For ``format=json``/``raw`` the text is
the serialized envelope dict — the two channels never disagree.  For
``format=markdown`` the text is a rendering of the page data while
``structured_content`` carries the envelope.

Result shapes (``ExecutionResult.shape``):

- ``"list"`` — array data; the pipeline slices by ``page``/``limit`` (or
  ``fetch_all`` skip-slice) and emits the pagination envelope.
- ``"object"`` — dict data; unpaginated, or pre-sliced by the executor (e.g.
  ``tool_info``'s schema-property pages).  When ``paginated`` the envelope is
  emitted with the executor-supplied ``total_count``.
- ``"scalar"`` — primitive data; unpaginated.
- ``"text"`` — text/plain response (diffs, patches, base64-decoded content);
  wrapped in ``{"result": text}``.
- ``"empty"`` — no-content (204/205) or empty/out-of-range page; carries an
  agent-facing ``message``.
- ``"binary"`` — binary response; ``content_info`` metadata instead of bytes.

Pagination facts are single-source: ``page``/``limit`` naming, the default
page size (``constants.DEFAULT_PAGE_SIZE``), the cap
(``constants.PAGE_SIZE_MAX``), and the envelope computation
(``pagination.add_pagination_metadata``).
"""

from __future__ import annotations

import json as json_module
import logging
from dataclasses import dataclass, field
from typing import Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent
from pydantic import ConfigDict

from gitea_mcp_server.constants import DEFAULT_PAGE_SIZE
from gitea_mcp_server.format import collapse_data, format_as_markdown
from gitea_mcp_server.pagination import add_pagination_metadata

logger = logging.getLogger(__name__)

_VALID_FORMATS = frozenset({"raw", "json", "markdown"})


@dataclass
class ExecutionResult:
    """Raw executor output — data only, no display.

    Executors (autogen HTTP pipeline and synthetic impls) return this instead
    of a ``ToolResult``; :func:`render` turns it into the agent-facing result.

    ``markdown_formatter`` is a callable — pydantic cannot build a
    ``TypeAdapter`` for it (FastMCP validates tool return annotations), so
    the field is typed ``Any`` and excluded from serialization.
    """

    __pydantic_config__ = ConfigDict(arbitrary_types_allowed=True)

    data: Any
    total_count: int | None = None
    shape: str = "object"
    paginated: bool = False
    message: str | None = None
    markdown_extras: list[str] | None = None
    markdown_formatter: Any = field(default=None, repr=False)


def render(  # noqa: PLR0913 - the pipeline is the single display path; every display axis must be a parameter because executors return raw data only and never render
    result: ExecutionResult,
    *,
    fmt: str,
    detail: str = "full",
    page: int = 1,
    limit: int = DEFAULT_PAGE_SIZE,
    fetch_all: bool = False,
    schema: dict[str, Any] | None = None,
) -> ToolResult:
    """Render an ``ExecutionResult`` into a dual-channel ``ToolResult``.

    The single display path for every tool: shape → paginate → format.

    Args:
        result: The raw executor output.
        fmt: Output format — ``"raw"``, ``"json"``, or ``"markdown"``.
        detail: Output detail — ``"full"`` (default) or ``"concise"``.
        page: Page number (1-based).  Ignored when ``fetch_all`` is True.
        limit: Items per page.  Ignored when ``fetch_all`` is True.
        fetch_all: When True, return all items without page slicing
            (in-memory skip-slice — no HTTP loop).
        schema: Optional JSON Schema describing *data* for ``$ref``-aware
            collapse when ``detail="concise"``.

    Returns:
        A ``ToolResult`` whose ``content`` (the text channel) is authoritative
        and always present, with ``structured_content`` mirroring it.  For
        ``format=json``/``raw`` the text is the serialized envelope dict; for
        ``format=markdown`` the text is a rendering of the page data.
    """
    if fmt not in _VALID_FORMATS:
        msg = f"Unsupported format '{fmt}'. Use 'markdown', 'json', or 'raw'."
        raise ValueError(msg)

    envelope = _paginate(result, page=page, limit=limit, fetch_all=fetch_all)
    return _format(envelope, result, fmt=fmt, detail=detail, schema=schema)


def _paginate(  # noqa: PLR0911 - each shape has distinct pagination semantics (list slices, object envelopes, empty/binary are special); extracting them would scatter the shape logic the pipeline exists to centralize
    result: ExecutionResult,
    *,
    page: int,
    limit: int,
    fetch_all: bool,
) -> dict[str, Any]:
    """Shape + paginate: build the envelope dict for *result*.

    Returns the ``{"result": ...}`` envelope with pagination keys added
    according to the result's shape and pagination state.
    """
    data = result.data
    shape = result.shape

    if shape == "empty":
        if result.paginated:
            return {
                "result": data if isinstance(data, list) else [],
                "message": result.message or "No results found.",
                "has_more": False,
                "next_offset": None,
                "total_count": result.total_count,
            }
        return {"result": data}

    if shape == "list":
        items = data if isinstance(data, list) else []
        total = result.total_count
        if total == 0 or (total is None and not items):
            # Empty result set — emit the empty envelope with the message.
            return {
                "result": [],
                "message": result.message or "No results found.",
                "has_more": False,
                "next_offset": None,
                "total_count": total,
            }
        if fetch_all:
            # In-memory skip-slice: everything, no more pages.
            return {
                "result": items,
                "has_more": False,
                "next_offset": None,
                "total_count": total,
            }
        start = (page - 1) * limit
        if total is not None and start >= total:
            return {
                "result": [],
                "message": result.message
                or f"Page {page} is out of range (total results: {total}).",
                "has_more": False,
                "next_offset": None,
                "total_count": total,
            }
        page_items = items[start : start + limit]
        # When the total is unknown, add_pagination_metadata falls back to
        # the "full page means more" heuristic (len == limit).
        return add_pagination_metadata({"result": page_items}, page, limit, total)

    # object / scalar / text — no slicing; envelope only when paginated.
    if result.paginated:
        return add_pagination_metadata({"result": data}, page, limit, result.total_count)
    if shape == "binary":
        return {"result": None, "content_info": data}
    return {"result": data}


def _format(
    envelope: dict[str, Any],
    result: ExecutionResult,
    *,
    fmt: str,
    detail: str,
    schema: dict[str, Any] | None,
) -> ToolResult:
    """Format the envelope dict into a dual-channel ``ToolResult``.

    ``content`` is always set explicitly (deterministic raw — no reliance on
    FastMCP auto-populating it from ``structured_content``).  Formatting
    errors are recovered with a readable fallback (the error-recovery layer
    that used to live in ``format_tool_result``).
    """
    data = result.data
    shape = result.shape
    try:
        if fmt == "raw":
            text = json_module.dumps(envelope, indent=2)
        elif fmt == "json":
            if detail == "concise" and schema is not None:
                envelope["result"] = collapse_data(
                    envelope["result"], schema, _depth=0, detail="concise"
                )
            text = json_module.dumps(envelope, indent=2)
        elif shape in ("empty", "binary") and result.message:
            text = result.message
        else:
            formatter = result.markdown_formatter or (
                lambda d: format_as_markdown(d, schema, detail=detail)
            )
            text = formatter(data)
            if result.markdown_extras:
                text += "\n\n---\n\n" + "\n\n---\n\n".join(result.markdown_extras)
    except (TypeError, AttributeError, ValueError) as exc:
        logger.warning(
            "Display pipeline recovered from %s: %s. fmt=%s, detail=%s",
            type(exc).__name__,
            exc,
            fmt,
            detail,
        )
        try:
            data_str = json_module.dumps(envelope, indent=2, default=str)
        except (TypeError, ValueError):
            data_str = str(envelope)
        if fmt == "json":
            text = json_module.dumps({"result": data_str}, indent=2)
            envelope = {"result": data_str}
        else:
            text = (
                f"```json\n{data_str}\n```\n\n"
                f"*Note: formatting failed ({type(exc).__name__}), "
                "showing raw data.*\n"
            )
            envelope = {"result": data_str}

    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=envelope,
    )


__all__ = [
    "ExecutionResult",
    "render",
]
