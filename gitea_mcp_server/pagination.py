"""Pagination header capture via httpx event hooks, pagination metadata
injection, and the PaginationRunner for auto-pagination loops.

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

The :class:`PaginationRunner` encapsulates the fetch-loop logic for API
tools that need to iterate over multiple pages via HTTP calls.  It is
used by the ``_fetch_all_loop`` virtual-param hook.
"""

import contextvars
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.constants import FETCH_ALL_MAX_PAGES

_FetchFn = Callable[[dict[str, Any]], Awaitable[ToolResult]]
"""Type alias for a callable that executes one page of an API tool.

An async function that accepts tool kwargs (with updated ``page``)
and returns a ``ToolResult`` from a fresh HTTP call.
"""

PAGINATION_KEYS = ("has_more", "next_offset", "total_count")
"""Keys in structured_content that carry pagination metadata."""

PAGINATION_HEADERS = ("X-Total-Count", "X-Total")
"""Response headers checked for total count, in priority order."""

SUCCESS_STATUS_THRESHOLD = 300
"""Maximum status code considered a successful response for header capture."""

pagination_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "pagination", default={}
)


class PaginationRunner:
    """Loop-based pagination for API tools that fetch data via HTTP.

    Encapsulates the fetch-loop logic: starting from an initial page result,
    reads ``has_more`` / ``next_offset`` / ``total_count`` from
    ``structured_content`` and re-invokes the fetch callable for subsequent
    pages, merging array results into a single list.

    Termination (first wins):

    1. ``has_more`` is ``false`` on the most recent page.
    2. The most recent page returned fewer items than the page size (heuristic
       when ``total_count`` is unknown).
    3. ``FETCH_ALL_MAX_PAGES`` pages have been fetched (safety cap).

    Usage::

        runner = PaginationRunner(fetch_fn)
        merged = await runner.run(first_page_result, kwargs)
    """

    def __init__(
        self,
        fetch_fn: _FetchFn,
        max_pages: int = FETCH_ALL_MAX_PAGES,
    ) -> None:
        """Create a PaginationRunner.

        Args:
            fetch_fn: Async callable ``(kwargs) → ToolResult`` that fetches
                one page.  Called with updated ``kwargs["page"]`` for each
                subsequent page.
            max_pages: Maximum number of pages to fetch (safety cap).
                Defaults to ``FETCH_ALL_MAX_PAGES``.
        """
        self._fetch_fn = fetch_fn
        self._max_pages = max_pages

    async def run(
        self,
        result: ToolResult,
        kwargs: dict[str, Any],
    ) -> ToolResult:
        """Run the pagination loop starting from the initial page result.

        Args:
            result: ``ToolResult`` from the first page (must have
                ``structured_content`` with ``has_more``, ``next_offset``,
                and optionally ``total_count`` already set).
            kwargs: Tool arguments (mutable; ``page`` is updated in-place
                when re-invoking ``self._fetch_fn``).

        Returns:
            A ``ToolResult`` with merged ``result`` array,
            ``has_more=False``, ``next_offset=None``, and the most
            recent ``total_count``.
        """
        structured = result.structured_content
        if not structured:
            return result

        all_data = structured.get("result")
        if not isinstance(all_data, list):
            return result

        # Clone the accumulator so the original ToolResult is unmodified.
        merged_data = list(all_data)
        total_count = structured.get("total_count")
        page_size = kwargs.get("per_page") or kwargs.get("limit", 50)

        # next_offset tells us the next page to fetch (set by
        # add_pagination_metadata).
        page = structured.get("next_offset")
        if page is None:
            # Single page only — nothing to fetch.
            return result

        fetched = 1  # first page already counted
        while fetched < self._max_pages:
            has_more = structured.get("has_more", False)
            if not has_more:
                break

            kwargs["page"] = page
            next_result = await self._fetch_fn(kwargs)
            next_sc = next_result.structured_content or {}
            next_data = next_sc.get("result")

            if isinstance(next_data, list):
                merged_data.extend(next_data)

            # Carry forward the server's total count (last-known wins).
            sc_total = next_sc.get("total_count")
            if sc_total is not None:
                total_count = sc_total

            # Use the response's has_more; fall back to heuristic when
            # total_count is unknown (page shorter than limit → last page).
            next_has_more = next_sc.get("has_more")
            if next_has_more is None and isinstance(next_data, list):
                next_has_more = len(next_data) >= page_size
                next_sc["has_more"] = next_has_more

            page = next_sc.get("next_offset")
            if page is None:
                break

            structured = next_sc
            fetched += 1

        # Build final structured content with all data merged.
        final_structured = dict(structured)
        final_structured["result"] = merged_data
        final_structured["has_more"] = False
        final_structured["next_offset"] = None
        final_structured["total_count"] = total_count

        # content carries the first page's text; apply_format (called
        # after PaginationRunner) regenerates it from the final data.
        return ToolResult(
            content=result.content,
            structured_content=final_structured,
            meta=result.meta,
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
    "PaginationRunner",
    "add_pagination_metadata",
    "apply_pagination",
    "capture_pagination_headers",
    "pagination_ctx",
]
