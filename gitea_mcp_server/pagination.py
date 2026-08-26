"""Pagination header capture via httpx event hooks and pagination metadata
injection.

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

Module-level state warning
--------------------------
``pagination_ctx`` is a module-level :class:`~contextvars.ContextVar` by
design — it bridges httpx event hooks (which have no access to tool kwargs)
and the ``mcp_builder._ToolWrappingTransform`` pipeline (which has no access
to httpx internals).  This is intentional and preferable to coupling to
FastMCP internals.

However, tests that simulate the event hook must reset ``pagination_ctx``
after use, or rely on ``asyncio_default_test_loop_scope = "function"``
(each async test gets its own event loop, thus its own
``contextvars.Context``).  The suite-level autouse fixture
``_reset_module_contexts`` in ``tests/conftest.py`` provides a deterministic
reset for every test regardless of sync/async status.
"""

import contextvars
from typing import Any

import httpx

PAGINATION_KEYS = ("has_more", "next_offset", "total_count")
"""Keys that carry pagination metadata.

They live in ``structured_content`` and, for ``format=json`` output, in the
text channel beside ``result`` — ``content`` is the authoritative channel
agents read, ``structured_content`` mirrors it.
"""

PAGINATION_SCHEMA_PROPERTIES: dict[str, dict[str, Any]] = {
    "has_more": {
        "type": "boolean",
        "description": "Whether another page is available.",
    },
    "next_offset": {
        "anyOf": [{"type": "integer"}, {"type": "null"}],
        "description": "The next page number, or null when this is the last page.",
    },
    "total_count": {
        "anyOf": [{"type": "integer"}, {"type": "null"}],
        "description": "Total matching items when known.",
    },
}
"""JSON Schema properties for the pagination metadata envelope.

Single source of truth for the agent-facing pagination contract.  Both the
OpenAPI provider (``mcp_builder``, for generated API tools) and the synthetic
tool contract (``synthetic_contract``) declare these keys next to ``result``
in their output schemas, matching the runtime shape (see
:data:`PAGINATION_KEYS`).  ``next_offset`` and ``total_count`` are
nullable because the runtime emits ``null`` for them on the last page and
when the total is unknown.
"""

MESSAGE_SCHEMA_PROPERTY: dict[str, Any] = {
    "anyOf": [{"type": "string"}, {"type": "null"}],
    "description": "Agent-facing message on empty or out-of-range pages.",
}
"""JSON Schema property for the ``message`` key on empty/out-of-range pages.

Declared (nullable) in the output schemas of the paginated tools that emit
it via the result pipeline's ``empty`` shape
(``tools/result_pipeline.py``) — ``search``, ``search_tools``,
``search_resources``, ``search_docs``, ``list_resources``,
``list_hidden_tools``, ``tool_info``, and ``read_doc``.  The pipeline owns
out-of-range handling for every shape, so any paginated tool can emit a
``message``; tools that never paginate must not declare it, keeping schema
and runtime in agreement.
"""

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


__all__ = [
    "MESSAGE_SCHEMA_PROPERTY",
    "PAGINATION_HEADERS",
    "PAGINATION_KEYS",
    "PAGINATION_SCHEMA_PROPERTIES",
    "add_pagination_metadata",
    "capture_pagination_headers",
    "pagination_ctx",
]
