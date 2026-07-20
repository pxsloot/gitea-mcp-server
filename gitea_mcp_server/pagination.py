"""Pagination header capture via httpx event hooks.

Captures ``X-Total-Count`` / ``X-Total`` from Gitea API responses into a
context variable so the tool customization pipeline can populate
``total_count`` without coupling to FastMCP internals.

Usage::

    client = httpx.AsyncClient(
        ...,
        event_hooks={"response": [capture_pagination_headers]},
    )

    # Later, in transform_fn:
    meta = pagination_ctx.get()
    total_count = meta.get("total_count")  # int or None
"""

import contextvars
from typing import Any

import httpx
from fastmcp.tools.base import ToolResult

PAGINATION_KEYS = ("has_more", "next_offset", "total_count")
"""Keys in structured_content that carry pagination metadata."""

PAGINATION_HEADERS = ("X-Total-Count", "X-Total")
"""Response headers checked for total count, in priority order."""

SUCCESS_STATUS_THRESHOLD = 300
"""Maximum status code considered a successful response for header capture."""

pagination_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "pagination", default={}
)


async def capture_pagination_headers(response: httpx.Response) -> None:
    """httpx event hook: store ``X-Total-Count`` into ``pagination_ctx``.

    Attach to ``AsyncClient(event_hooks={"response": [handler]})``.
    Only captures on successful (2xx) responses. Ignores non-JSON and
    non-paginated responses silently.

    Safe for concurrent requests because ``contextvars`` are scoped per task.
    """
    if response.status_code >= SUCCESS_STATUS_THRESHOLD:
        return

    for header in PAGINATION_HEADERS:
        value = response.headers.get(header)
        if value is not None:
            try:
                pagination_ctx.set({"total_count": int(value)})
            except (ValueError, TypeError):
                continue
            return


def add_pagination_metadata(
    structured_content: dict[str, Any],
    page: int,
    limit: int,
    total_count: int | None = None,
) -> dict[str, Any]:
    """Add ``has_more`` / ``next_offset`` / ``total_count`` to structured_content.

    Args:
        structured_content: Existing structured_content dict (may contain
            ``"result"`` key with the page data).
        page: Current page number (1-based).
        limit: Items per page.
        total_count: Total number of items, if known.  When ``None``, falls
            back to a heuristic: ``has_more = len(result) == limit``.

    Returns:
        A new dict with pagination keys added to the original content.
    """
    enhanced = dict(structured_content)
    result_data = enhanced.get("result")

    if total_count is not None:
        has_more = page * limit < total_count
    elif isinstance(result_data, list):
        has_more = len(result_data) == limit
    else:
        has_more = False

    enhanced["has_more"] = has_more
    enhanced["next_offset"] = page + 1 if has_more else None
    enhanced["total_count"] = total_count
    return enhanced


def apply_pagination(
    result: ToolResult,
    page: int,
    limit: int,
    total_count: int | None = None,
) -> ToolResult:
    """Add pagination metadata to a ``ToolResult``'s ``structured_content``.

    Does **not** modify the ``content`` (markdown/json text).  Agents read
    pagination state from ``structured_content`` (``has_more``,
    ``next_offset``, ``total_count``).

    Args:
        result: A ``ToolResult`` with ``structured_content`` containing
            ``{"result": data}``.
        page: Current page number (1-based).
        limit: Items per page.
        total_count: Total number of items, if known.  When ``None``, falls
            back to a heuristic: ``has_more = len(result) == limit``.

    Returns:
        A new ``ToolResult`` with pagination keys added to
        ``structured_content``.  ``content`` and ``meta`` are preserved.
    """
    structured = result.structured_content or {}
    enhanced = add_pagination_metadata(structured, page, limit, total_count)
    return ToolResult(
        content=result.content,
        structured_content=enhanced,
        meta=result.meta,
    )


__all__ = [
    "PAGINATION_HEADERS",
    "PAGINATION_KEYS",
    "add_pagination_metadata",
    "apply_pagination",
    "capture_pagination_headers",
    "pagination_ctx",
]
