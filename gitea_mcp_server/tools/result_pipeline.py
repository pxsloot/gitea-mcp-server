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
``format=markdown`` the text is a rendering of the **page data** (the
envelope's ``result``, not the executor's full data) while
``structured_content`` carries the envelope.

Executors may attach a per-result ``schema`` (``ExecutionResult.schema``) for
``$ref``-aware collapse when the tool-level schema does not describe the
result (e.g. ``read_resource``, whose schema varies per URI); it takes
precedence over the tool-level schema in :func:`render`.  A per-result
``markdown_formatter`` — a :data:`~gitea_mcp_server.format.MarkdownFormatter`,
whose contract is stated canonically in ``format.py`` — is dispatched through
``call_markdown_formatter``.  When it is absent, ``_resolve_formatter``
consults the **type-bound** domain formatter for the result's response type
(``tools/display.py``, ``register_formatter(types=...)``, #760) before
falling back to the generic renderer — so a tool and its resource sibling
render the same domain view.  The type name is first-class metadata
(``ExecutionResult.response_type`` / :func:`render`'s *response_type*),
stamped pre-wrap on the spec operation and propagated through ``tool.meta``
and resource content meta.  Formatter context (``owner``/``repo``/``type``)
flows as display *input* through ``ExecutionResult.extra`` (resource content
meta) or :func:`render`'s *extra* argument (derived by the contract spine
from the call args).  When ``detail="concise"`` and a schema is
available, the pipeline pre-collapses the page (schema-aware ``$ref``
collapse) before calling the formatter, so formatters receive
already-collapsed data and must not re-collapse.  Root-list items are
*summarized*, not label-replaced: the collapse resolves a root list's item
``$ref`` one level via the server's OpenAPI spec (``render(openapi_spec=...)``),
so each item keeps its scalar fields and only its nested ``$ref``-backed
fields collapse to ``$ref:TypeName`` labels (#759).

Result shapes (``ExecutionResult.shape``):

- ``"list"`` — array data; the pipeline slices by ``page``/``limit`` (or
  ``fetch_all`` skip-slice) and emits the pagination envelope.
- ``"object"`` — dict data; unpaginated, or pre-sliced by the executor (e.g.
  ``tool_info``'s schema-property pages).  When ``paginated`` the envelope is
  emitted with the executor-supplied ``total_count``.  An out-of-range page
  on a pre-sliced object result emits the message envelope — the pipeline
  owns out-of-range handling for every shape, not just ``list``.
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
from typing import TYPE_CHECKING, Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent
from pydantic import ConfigDict
from pydantic.json_schema import SkipJsonSchema  # noqa: TC002 - runtime use via get_type_hints

from gitea_mcp_server.constants import DEFAULT_PAGE_SIZE
from gitea_mcp_server.format import (
    MarkdownFormatter,
    call_markdown_formatter,
    collapse_data,
    resolve_formatter,
)
from gitea_mcp_server.pagination import add_pagination_metadata

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)

_VALID_FORMATS = frozenset({"raw", "json", "markdown"})

# Last-resort fallback when the data is nested too deeply for even ``repr()``
# to render — the C stack overflows (environment-dependent: an 8 MB stack
# overflows at ~30k nesting levels, a 16 MB stack at ~60k).  The recovery
# path must never crash the tool, so this fixed string is the final answer.
_DEEP_NESTING_FALLBACK = "<data too deeply nested to display>"


@dataclass
class ExecutionResult:
    """Raw executor output — data only, no display.

    Executors (autogen HTTP pipeline and synthetic impls) return this instead
    of a ``ToolResult``; :func:`render` turns it into the agent-facing result.

    ``markdown_formatter`` is a :data:`~gitea_mcp_server.format.MarkdownFormatter`
    (the contract is stated canonically in ``format.py``), dispatched through
    ``call_markdown_formatter``; when absent the pipeline consults
    :func:`_resolve_formatter`'s tiers — the type-bound domain formatter for
    the result's ``response_type`` (#760), then ``format_as_markdown`` bound
    to the result schema.  The field is wrapped in ``SkipJsonSchema``:
    FastMCP derives a tool's output JSON Schema from its return annotation,
    and pydantic cannot generate a schema for a ``Callable`` — the wrapper
    keeps the field typed for mypy / ``get_type_hints`` while excluding it
    from the generated schema.  When ``detail="concise"`` and a schema is
    available, the pipeline pre-collapses the page (schema-aware ``$ref``
    collapse) *before* calling the formatter — the formatter receives
    already-collapsed data and must not re-collapse.
    """

    __pydantic_config__ = ConfigDict(arbitrary_types_allowed=True)

    data: Any
    total_count: int | None = None
    shape: str = "object"
    paginated: bool = False
    message: str | None = None
    markdown_extras: list[str] | None = None
    markdown_formatter: SkipJsonSchema[MarkdownFormatter | None] = field(default=None, repr=False)
    extra: dict[str, Any] | None = None
    """Formatter context (``owner``/``repo``/``type``) — display *input*, not
    display logic.  The resource executor supplies it from content metadata;
    the contract spine derives it from the tool call's path/query args.  It
    takes precedence over :func:`render`'s *extra* argument the same way
    ``schema`` takes precedence over the tool-level schema, and is forwarded
    to the markdown formatter via ``call_markdown_formatter`` (only
    formatters declaring ``extra`` receive it).
    """
    response_type: str | None = None
    """Response type name for type-bound formatter resolution (#760).

    The pre-wrap ``x-response-type`` stamp, propagated from ``tool.meta``
    (autogen tools) or resource content meta (``read_resource``).  It is the
    middle tier of :func:`~gitea_mcp_server.format.resolve_formatter`;
    ``None`` means unbound — the generic renderer.  :func:`render` prefers
    this over the tool-level ``response_type`` argument, the same precedence
    rule as ``schema`` and ``extra``.
    """
    schema: dict[str, Any] | None = None
    """Schema describing *data* for ``$ref``-aware collapse (``detail=concise``).

    Executor-supplied, per-result — used when the tool-level schema does not
    describe this result (e.g. ``read_resource``, whose schema varies per
    URI).  ``render()`` prefers this over the tool-level ``schema`` argument.
    """


def render(  # noqa: PLR0913 - the pipeline is the single display path; every display axis must be a parameter because executors return raw data only and never render
    result: ExecutionResult,
    *,
    fmt: str,
    detail: str = "full",
    page: int = 1,
    limit: int = DEFAULT_PAGE_SIZE,
    fetch_all: bool = False,
    schema: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    response_type: str | None = None,
    openapi_spec: OpenAPISpec | None = None,
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
            collapse when ``detail="concise"``.  When the ``ExecutionResult``
            carries its own ``schema`` (executor-supplied, e.g. per-URI for
            ``read_resource``), that takes precedence over this argument.
        extra: Optional formatter context (``owner``/``repo``/``type``) —
            display *input* derived by the contract spine from the tool
            call's args (#760).  When the ``ExecutionResult`` carries its
            own ``extra`` (resource content meta), that takes precedence.
        response_type: Optional response type name for type-bound formatter
            resolution (#760) — read by the contract spine from
            ``tool.meta["response_type"]``.  When the ``ExecutionResult``
            carries its own ``response_type`` (per-URI ``read_resource``),
            that takes precedence.
        openapi_spec: Post-conversion OpenAPI 3.1 spec enabling root-list
            item summaries under ``detail="concise"`` (#759) — the collapse
            resolves a root list's item ``$ref`` one level so items keep
            their scalar fields.  ``None`` keeps the whole-item label
            fallback (synthetic tools with inline schemas need nothing).

    Returns:
        A ``ToolResult`` whose ``content`` (the text channel) is authoritative
        and always present, with ``structured_content`` mirroring it.  For
        ``format=json``/``raw`` the text is the serialized envelope dict; for
        ``format=markdown`` the text is a rendering of the page data — or,
        for empty/out-of-range results, the message (so the text channel
        never disagrees with the envelope).
    """
    if fmt not in _VALID_FORMATS:
        msg = f"Unsupported format '{fmt}'. Use 'markdown', 'json', or 'raw'."
        raise ValueError(msg)

    # The executor may supply a per-result schema (e.g. read_resource's
    # per-URI response schema); it wins over the tool-level schema.
    effective_schema = result.schema if result.schema is not None else schema
    # Same precedence for formatter context: per-result extra (resource meta)
    # wins over the spine-derived call-arg extra.
    effective_extra = result.extra if result.extra is not None else extra
    # And the type-binding key: per-result response_type (read_resource's
    # per-URI type) wins over the tool-level one from tool.meta.
    effective_response_type = (
        result.response_type if result.response_type is not None else response_type
    )

    envelope, effective_shape = _paginate(result, page=page, limit=limit, fetch_all=fetch_all)
    return _format(
        envelope,
        result,
        fmt=fmt,
        detail=detail,
        schema=effective_schema,
        extra=effective_extra,
        response_type=effective_response_type,
        effective_shape=effective_shape,
        openapi_spec=openapi_spec,
    )


def _paginate(  # noqa: PLR0911 - each shape has distinct pagination semantics (list slices, object envelopes, empty/binary are special); extracting them would scatter the shape logic the pipeline exists to centralize
    result: ExecutionResult,
    *,
    page: int,
    limit: int,
    fetch_all: bool,
) -> tuple[dict[str, Any], str]:
    """Shape + paginate: build the envelope dict for *result*.

    Returns ``(envelope, effective_shape)`` — the ``{"result": ...}``
    envelope with pagination keys added, plus the shape the formatter should
    treat the result as.  ``effective_shape`` is ``"empty"`` whenever the
    envelope represents an empty or out-of-range result (so the formatter
    renders the message instead of the data) and ``result.shape`` otherwise.
    """
    data = result.data
    shape = result.shape

    if shape == "empty":
        if result.paginated:
            return (
                {
                    "result": data if isinstance(data, list) else [],
                    "message": result.message or "No results found.",
                    "has_more": False,
                    "next_offset": None,
                    "total_count": result.total_count,
                },
                "empty",
            )
        return {"result": data}, "empty"

    if shape == "list":
        items = data if isinstance(data, list) else []
        total = result.total_count
        if total == 0 or (total is None and not items):
            # Empty result set — emit the empty envelope with the message.
            return (
                {
                    "result": [],
                    "message": result.message or "No results found.",
                    "has_more": False,
                    "next_offset": None,
                    "total_count": total,
                },
                "empty",
            )
        if fetch_all:
            # In-memory skip-slice: everything, no more pages.
            return (
                {
                    "result": items,
                    "has_more": False,
                    "next_offset": None,
                    "total_count": total,
                },
                "list",
            )
        start = (page - 1) * limit
        if total is not None and start >= total:
            return (
                {
                    "result": [],
                    "message": result.message
                    or f"Page {page} is out of range (total results: {total}).",
                    "has_more": False,
                    "next_offset": None,
                    "total_count": total,
                },
                "empty",
            )
        page_items = items[start : start + limit]
        # When the total is unknown, add_pagination_metadata falls back to
        # the "full page means more" heuristic (len == limit).
        return add_pagination_metadata({"result": page_items}, page, limit, total), "list"

    # object / scalar / text — no slicing; envelope only when paginated.
    if result.paginated:
        # Pre-sliced object results (e.g. ``read_doc``'s guide lines,
        # ``tool_info``'s schema properties): the executor sliced already, so
        # an out-of-range page yields empty content — emit the message
        # envelope instead of silent empty data.  The result keeps its object
        # shape (the schema declares it); only the message is added.
        #
        # ``total == 0`` is *not* out of range for objects: unlike a list,
        # the object data is the content itself (e.g. ``tool_info`` on a
        # free-form object schema with no declared properties), so page 1
        # must return it, not an out-of-range message.
        total = result.total_count
        if total is not None and total > 0 and (page - 1) * limit >= total:
            return (
                {
                    "result": data,
                    "message": result.message
                    or f"Page {page} is out of range (total results: {total}).",
                    "has_more": False,
                    "next_offset": None,
                    "total_count": total,
                },
                "empty",
            )
        return add_pagination_metadata({"result": data}, page, limit, total), shape
    if shape == "binary":
        return {"result": None, "content_info": data}, "binary"
    return {"result": data}, shape


def _resolve_formatter(
    result: ExecutionResult,
    schema: dict[str, Any] | None,
    response_type: str | None = None,
) -> MarkdownFormatter:
    """Return the result's formatter, the type-bound domain formatter, or the
    schema-bound generic fallback.

    Delegates the three-tier choice to
    :func:`~gitea_mcp_server.format.resolve_formatter` (explicit per-result →
    type-bound domain formatter → generic).  Keeping the policy in the format
    layer means the pipeline never imports the domain formatter module
    (``tools/display.py``) — Result → Format, not Result → Display.

    The explicit tier is the ``ExecutionResult.markdown_formatter`` (the
    resource surface's ``format_hint`` resolution); the type-bound tier uses
    ``response_type``; the pipeline is a single, uniform formatter call site.
    """
    return resolve_formatter(
        schema, explicit=result.markdown_formatter, response_type=response_type
    )


def _format(  # noqa: PLR0913 - the pipeline is the single display path; every display axis (envelope, result, fmt, detail, schema, extra, response_type, effective_shape, openapi_spec) must be a parameter because executors return raw data only and never render
    envelope: dict[str, Any],
    result: ExecutionResult,
    *,
    fmt: str,
    detail: str,
    schema: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
    response_type: str | None = None,
    effective_shape: str,
    openapi_spec: OpenAPISpec | None = None,
) -> ToolResult:
    """Format the envelope dict into a dual-channel ``ToolResult``.

    ``content`` is always set explicitly (deterministic raw — no reliance on
    FastMCP auto-populating it from ``structured_content``).  Formatting
    errors are recovered with a readable fallback (the error-recovery layer
    that used to live in ``format_tool_result``).

    ``effective_shape`` (from :func:`_paginate`) is ``"empty"`` whenever the
    envelope represents an empty or out-of-range result — the message is
    rendered in every format, including markdown, instead of the (empty)
    data, so the text channel never disagrees with the envelope.

    The markdown path renders the **envelope's page** (``envelope["result"]``),
    not the executor's full ``result.data`` — so the text channel agrees with
    ``structured_content`` on paginated list tools.  When
    ``detail="concise"`` and a schema is available, the page is collapsed
    once (schema-aware ``$ref`` collapse; root-list items are summarized via
    *openapi_spec*, #759) for json and markdown, and the envelope's
    ``result`` is updated so ``structured_content`` mirrors the collapsed
    text — the two channels never disagree.  ``format=raw`` stays
    uncollapsed: raw is the unprocessed-data contract.  The formatter receives
    the collapsed page; the ``MarkdownFormatter`` contract (``format.py``)
    governs what it may declare.
    """
    try:
        # Collapse the page once for json/markdown when detail=concise and a
        # schema is available.  Updating the envelope's ``result`` keeps
        # ``structured_content`` in sync with the collapsed text — the two
        # channels never disagree.  ``format=raw`` stays uncollapsed: raw is
        # the unprocessed-data contract.
        page_data = envelope["result"]
        if fmt != "raw" and detail == "concise" and schema is not None:
            page_data = collapse_data(
                page_data,
                schema,
                _depth=0,
                detail="concise",
                openapi_spec=openapi_spec,
            )
            envelope["result"] = page_data

        if fmt in ("raw", "json"):
            text = json_module.dumps(envelope, indent=2)
        elif effective_shape in ("empty", "binary"):
            # The envelope carries the defaulted message for empty/out-of-range
            # results; fall back to the executor's message for binary and
            # non-paginated empty results.
            text = result.message or envelope.get("message") or ""
        else:
            # Render the page (the envelope's result), not the executor's
            # full data — the text channel must agree with the envelope.
            # The formatter (result-specific, type-bound, or the schema-bound
            # generic fallback) is resolved in one place by
            # _resolve_formatter; the dispatch helper forwards only the
            # kwargs it declares, here ``extra`` (formatter context).
            text = call_markdown_formatter(
                _resolve_formatter(result, schema, response_type=response_type),
                page_data,
                extra=extra,
            )
            if result.markdown_extras:
                text += "\n\n---\n\n" + "\n\n---\n\n".join(result.markdown_extras)
    except (TypeError, AttributeError, ValueError, KeyError, IndexError, RecursionError) as exc:
        logger.warning(
            "Display pipeline recovered from %s: %s. fmt=%s, detail=%s",
            type(exc).__name__,
            exc,
            fmt,
            detail,
        )
        try:
            data_str = json_module.dumps(envelope, indent=2, default=str)
        except (TypeError, ValueError, RecursionError):
            try:
                data_str = str(envelope)
            except RecursionError:
                # Even repr() overflows the C stack on pathologically deep
                # data (observed on CI: 8 MB stack, ~30k nesting levels).
                # Emit a fixed, honest fallback instead of crashing the tool.
                data_str = _DEEP_NESTING_FALLBACK
        if fmt in ("json", "raw"):
            # Deterministic raw: the recovered text is still valid JSON,
            # mirroring structured_content.
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
