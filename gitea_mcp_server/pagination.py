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


__all__ = [
    "PAGINATION_HEADERS",
    "capture_pagination_headers",
    "pagination_ctx",
]
