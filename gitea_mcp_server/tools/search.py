"""Search transform and synthetic tools for tool discovery.

BM25 search engine lives in gitea_mcp_server/search.py (flat infra layer).
This module contains Tool-specific search wrappers, the TolerantSearchTransform,
and the shared name-match + BM25 + format pipeline used by both search_tools and search_resources.
Paginated registrations use ``synthetic_contract.SyntheticToolSpec`` +
``register_all_synthetic_tools`` so their validation and output metadata
match generated API tools.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec

from gitea_mcp_server.constants import (
    DETAIL_PARAM_SCHEMA_CONCISE,
    SEARCH_CATEGORY_ALIASES,
    SEARCH_MIN_SCORE,
    SEARCH_NAME_BOOST,
)
from gitea_mcp_server.format import format_tool_info_markdown
from gitea_mcp_server.models import ToolSchemaResult, ToolSearchEntry
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.pagination import MESSAGE_SCHEMA_PROPERTY
from gitea_mcp_server.search import BM25SearchEngine
from gitea_mcp_server.tools.customize import synthetic_annotations
from gitea_mcp_server.tools.errors import (
    raise_value_error,
    raise_value_error_from,
)
from gitea_mcp_server.tools.examples import serialize_tool_schema
from gitea_mcp_server.tools.filter_info import (
    build_filtered_tools_message,
    get_filtered_tool_info,
)
from gitea_mcp_server.tools.result_pipeline import ExecutionResult
from gitea_mcp_server.tools.schemas import (
    is_object_type,
    schema_type_is_array,
    unwrap_result_schema,
)
from gitea_mcp_server.tools.synthetic_contract import (
    SyntheticToolSpec,
    register_all_synthetic_tools,
)

# ============================================================================
# Shared BM25 + format pipeline (used by search_tools and search_resources)
# ============================================================================


def _empty_results_message(query: str, cross_link_hints: dict[str, str] | None) -> str:
    """Build a helpful message when a search returns no results."""
    text = f"No results found for '{query}'."
    if cross_link_hints:
        text += "\n\n**Cross-linking hints:**\n"
        for label, tool in cross_link_hints.items():
            text += f"- For {label}: `{tool}(query)`\n"
    return text


_NAME_MATCH_MIN_TOKENS = 2
"""Minimum query tokens for name-match boosting.  Single-token queries
are handled by BM25, avoiding a flood of results all at score 1.0."""


def _normalize_for_match(text: str, tool_prefix: str) -> tuple[str, list[str]]:
    """Normalize a name or query for name-matching.

    Strips the ``tool_prefix``, lowercases, replaces underscores with
    spaces, then splits into tokens.  Returns ``(normalized_text, tokens)``.
    """
    t = text.lower().replace("_", " ").strip()
    if tool_prefix:
        prefix_norm = tool_prefix.lower().replace("_", " ")
        if prefix_norm and t.startswith(prefix_norm):
            t = t[len(prefix_norm) :].strip()
    return t, t.split()


def _token_prefix_match(tokens: list[str], name_tokens: list[str]) -> bool:
    """Return True if each ``token`` is a prefix of the corresponding ``name_tokens``."""
    return len(tokens) <= len(name_tokens) and all(
        name_tokens[i].startswith(qt) for i, qt in enumerate(tokens)
    )


def _name_matches(query: str, name: str, tool_prefix: str) -> bool:
    """Return True if ``query`` matches ``name`` (exact or token-boundary prefix).

    Normalizes both sides: lowercase, underscores → spaces, strips the
    configured ``tool_prefix`` from both the query and the name.  Then
    splits into tokens and checks whether the query tokens form a prefix
    of the name tokens — each query token must be a prefix of the
    corresponding name token (not crossing token boundaries).

    Uses a **sliding window** over the name tokens: every contiguous
    window of ``len(q_tokens)`` is tried.  This handles domain-prefixed
    tool names like ``"repo_create_pull_request"`` matching
    ``"create pull request"`` where the domain token sits before the
    query-aligned tokens — a fixed position-0 alignment would fail.

    Within each window, two query-token orderings are tried to handle
    verb-first queries (e.g. ``"create issue"`` vs domain-first
    ``"issue_create_issue"``):

    - original order: e.g. ``["create", "pull", "request"]``
    - first-two-swapped: e.g. ``["issue", "create"]``

    Single-token queries are **not** boosted — they return ``False`` so
    BM25 handles them, avoiding a flood of 30+ results all at score 1.0.

    Args:
        query: The search query (e.g. ``"user get current"``).
        name: The item name (e.g. ``"gitea_user_get_current"``).
        tool_prefix: Configured namespace prefix (e.g. ``"gitea_"``).
            Empty string means no prefix.
    """
    q, q_tokens = _normalize_for_match(query, tool_prefix)
    n, n_tokens = _normalize_for_match(name, tool_prefix)

    if not q or len(q_tokens) < _NAME_MATCH_MIN_TOKENS or len(q_tokens) > len(n_tokens):
        return False

    # Exact match after normalisation: the normalised query equals the
    # normalised (stripped) name.
    if q == n:
        return True

    # Sliding window: try every contiguous window of len(q_tokens) in
    # the name tokens.  This handles domain-prefixed tool names like
    # "repo_create_pull_request" matching the query "create pull request"
    # where the domain token sits before the query-aligned tokens ("repo"
    # is not in the query, so a fixed position-0 alignment fails).
    #
    # Within each window, two query-token orderings are tried:
    #   - original: "create pull request" → ["create", "pull", "request"]
    #   - swapped:  verb-first → "create issue" ↔ ["issue", "create"]
    swapped = [q_tokens[1], q_tokens[0], *q_tokens[2:]]
    for start in range(len(n_tokens) - len(q_tokens) + 1):
        window = n_tokens[start : start + len(q_tokens)]
        if _token_prefix_match(q_tokens, window):
            return True
        if _token_prefix_match(swapped, window):
            return True

    return False


def search_and_slice(  # noqa: PLR0913 - 7 params but all are independent config axes
    items: list[Any],
    texts: list[str],
    query: str,
    page: int,
    limit: int,
    min_score: float = SEARCH_MIN_SCORE,
    tool_prefix: str = "",
) -> tuple[list[Any], int]:
    """Rank items by name match + BM25, then slice by page/limit.

    Name matches (exact or token-boundary prefix, with/without
    ``tool_prefix``) are placed first with score 1.0.  Remaining items
    are ranked by BM25.  This fixes the BM25 limitation where
    ``gitea_user_get_current`` ranks below ``gitea_user_current_*``
    for the query ``\"user current\"``.

    Returns ``(page_items, total_count)`` where ``total_count`` is the total
    number of items that matched the query (name match or BM25 above
    ``min_score``), and ``page_items`` are the items on the requested page.
    Each item in ``page_items`` is a shallow copy of the corresponding input
    item with an extra ``score`` key (normalized 0.0-1.0, where 1.0 is the
    top match for this query) so callers/agents can apply their own relevance
    threshold.

    When ``items`` or ``texts`` is empty, returns ``([], 0)``.
    When the page is out of range, returns an empty list with the correct
    ``total_count``.

    Args:
        items: The items to search over.
        texts: Searchable text for each item.
        query: Natural language query.
        page: Page number (1-based).
        limit: Results per page.
        min_score: Minimum normalized BM25 score (0.0-1.0).  Defaults to
            ``SEARCH_MIN_SCORE``.
        tool_prefix: Configured namespace prefix (e.g. ``\"gitea_\"``).
            Used to strip the prefix from item names before name matching.
    """
    if not items or not texts:
        return [], 0

    # Partition: name matches go first (score 1.0), rest go to BM25.
    name_match_indices: list[int] = []
    bm25_indices: list[int] = []
    for i, item in enumerate(items):
        name = item.get("name", "") if isinstance(item, dict) else ""
        if name and _name_matches(query, name, tool_prefix):
            name_match_indices.append(i)
        else:
            bm25_indices.append(i)

    # BM25 on the non-name-match items only.
    engine = BM25SearchEngine()
    bm25_texts = [texts[i] for i in bm25_indices]
    bm25_ranked = engine.search_with_scores(bm25_texts, query, len(bm25_texts), min_score=min_score)

    # Build the combined ranked list: name matches first (score 1.0),
    # then BM25 results (scores 0.0-1.0, naturally below).
    combined: list[tuple[int, float]] = [(i, 1.0) for i in name_match_indices]
    combined.extend((bm25_indices[i], score) for i, score in bm25_ranked)

    total_count = len(combined)

    start = (page - 1) * limit
    end = start + limit
    page_ranked = combined[start:end]
    # Attach the normalized score to each result item so agents can apply
    # their own relevance threshold instead of relying solely on min_score.
    page_items = [{**items[i], "score": round(score, 4)} for i, score in page_ranked]
    return page_items, total_count


# ============================================================================
# Text extraction helpers
# ============================================================================


def extract_searchable_text_enhanced(tool: Tool) -> str:
    """Build BM25 search text from a tool, optimised for discoverability.

    Combines name (repeated ``SEARCH_NAME_BOOST`` times), title,
    description, parameter names/descriptions, tags, and category
    aliases into a single searchable string.
    """
    parts = [tool.name] * SEARCH_NAME_BOOST

    if tool.annotations and tool.annotations.title:
        parts.append(tool.annotations.title)

    if tool.description and tool.description.strip():
        parts.append(tool.description.strip())

    schema = tool.parameters
    if schema:
        properties = schema.get("properties", {})
        for param_name, param_info in properties.items():
            parts.append(param_name)
            if isinstance(param_info, dict):
                desc = param_info.get("description", "")
                if desc:
                    parts.append(desc)

    if tool.tags:
        for tag in tool.tags:
            parts.append(tag)
            if tag in SEARCH_CATEGORY_ALIASES:
                parts.append(SEARCH_CATEGORY_ALIASES[tag])

    return " ".join(parts)


def extract_resource_text(entry: Mapping[str, Any]) -> str:
    """Build searchable text from a resource entry dict."""
    parts = [entry.get("name", "")]
    uri = entry.get("uri", "")
    if uri:
        parts.append(uri)
    desc = entry.get("description", "")
    if desc:
        parts.append(desc)
    for tag in entry.get("tags", []):
        parts.append(tag)
    return " ".join(parts)


class TolerantBM25Search:
    """BM25 search for tools using the generic BM25SearchEngine.

    Delegates indexing and querying to the engine; handles Tool→text extraction.
    """

    def __init__(self) -> None:
        self._engine = BM25SearchEngine()
        self._indexed_tools: Sequence[Tool] = ()

    def search(self, tools: Sequence[Tool], query: str, max_results: int = 10) -> Sequence[Tool]:
        """Search tools by BM25 relevance ranking."""
        texts = [extract_searchable_text_enhanced(t) for t in tools]
        self._indexed_tools = tools
        indices = self._engine.search(texts, query, max_results)
        return [self._indexed_tools[i] for i in indices]


# ============================================================================
# Search Transform + Synthetic Tools (from tool_search.py)
# ============================================================================


def compact_search_serializer(tools: Sequence[Tool]) -> list[ToolSearchEntry]:
    """Serialize tools to compact dicts (name, description, tags, annotations) for search display."""
    result = []
    for tool in tools:
        annotations = None
        if tool.annotations:
            a = tool.annotations
            annotations = {
                "title": a.title,
                "readOnlyHint": a.readOnlyHint,
                "destructiveHint": a.destructiveHint,
                "idempotentHint": a.idempotentHint,
                "openWorldHint": a.openWorldHint,
            }
        item = ToolSearchEntry(
            name=tool.name,
            description=tool.description or "",
            tags=list(tool.tags) if tool.tags else [],
        )
        if annotations:
            item["annotations"] = annotations
        result.append(item)
    return result


class TolerantSearchTransform(BM25SearchTransform):
    """Search transform for lazy-loading tool discovery.

    Unlike the base class, this transform does NOT register synthetic tools
    (search_tools, call_tool, tool_info) - those are normal ``mcp.tool()``
    registrations in ``register_synthetic_tools()``. The transform only
    controls which tools appear in ``list_tools()`` output (pinned set) and
    provides BM25 search over the catalog.

    Tools tagged ``synthetic`` are always pinned in ``list_tools()`` so
    agents can call them without searching — that is the invariant: all
    synthetic tools are always visible.
    """

    def __init__(self, **kwargs: Any) -> None:
        if "search_result_serializer" not in kwargs:
            kwargs["search_result_serializer"] = compact_search_serializer
        super().__init__(**kwargs)
        self._searcher = TolerantBM25Search()

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        pinned = [t for t in tools if "synthetic" in (t.tags or [])]
        return [*pinned]

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        return self._searcher.search(tools, query, self._max_results)

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Resolve all tools through normal provider lookup.

        No special intercepts - synthetic tools are registered as normal
        tools on the provider via ``register_synthetic_tools()``.
        """
        return await call_next(name, version=version)


# ── Synthetic tool implementations (exported for testing) ──────────────


async def _find_tool_by_name(
    name: str,
    ctx: Context,
    tool_prefix: str = "",
) -> Tool | None:
    """Find a tool by name, trying both bare and prefixed forms.

    The GiteaNamespace transform prefixes all tool names (e.g. ``search_tools``
    becomes ``gitea_search_tools``).  When agents pass an unprefixed name to
    ``call_tool``, the lookup fails because the catalog only contains prefixed
    names.  This helper tries both forms and returns the ``Tool`` directly,
    avoiding a redundant second lookup by the caller.

    Returns:
        The ``Tool`` if found, or ``None`` if not found in the registry.
    """
    tool = await ctx.fastmcp.get_tool(name)
    if tool is not None:
        return tool
    if tool_prefix:
        prefixed = f"{tool_prefix}{name}"
        tool = await ctx.fastmcp.get_tool(prefixed)
        if tool is not None:
            return tool
    return None


async def _call_tool_impl(
    name: str,
    arguments: Any,
    ctx: Context,
    tool_prefix: str = "",
    filtered_tools_info: dict[str, Any] | None = None,
) -> ToolResult:
    """Core call_tool implementation.

    Acts as a transparent proxy: resolves the tool name, forwards
    arguments, and returns the inner tool's result unchanged.
    Every tool on this server handles its own ``format`` parameter
    natively, so the proxy does not re-format.

    Filtered-tool error messages (scope, exclusion, deprecation) are
    checked here for proxy calls, and by :class:`FilteredToolMiddleware`
    at the MCP protocol level for direct calls.
    """
    if name == "call_tool" or (tool_prefix and name == f"{tool_prefix}call_tool"):
        msg = (
            "'call_tool' cannot call itself. Pass the *target* tool's name as "
            "`name` (e.g. call_tool(name='gitea_issue_get_issue', ...)). The "
            "proxy is invoked directly, never through itself — do not set "
            "name='call_tool'."
        )
        raise_value_error(msg)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as e:
            msg = f"Invalid JSON in arguments: {e}"
            raise_value_error_from(msg, e)
    if arguments is not None and not isinstance(arguments, dict):
        msg = f"Arguments must be a dict or JSON string, got {type(arguments).__name__}"
        raise_value_error(msg)

    tool = await _find_tool_by_name(name, ctx, tool_prefix)

    if tool is None:
        # Tool not found in the registry — check whether it's a filtered
        # tool (scope-restricted, config-excluded, or deprecated) and give
        # a helpful message.  The FilteredToolMiddleware handles this for
        # direct calls, but the proxy must check too because get_tool()
        # returns None for filtered tools, so we never reach the inner
        # ctx.fastmcp.call_tool() that would trigger the middleware.
        filter_info = get_filtered_tool_info(name, filtered_tools_info, tool_prefix)
        if filter_info is not None:
            msg = build_filtered_tools_message(name, filter_info, filtered_tools_info)
        else:
            msg = f"Tool '{name}' not found. Use `search_tools()` to discover available tools."
        raise_value_error(msg)

    return await ctx.fastmcp.call_tool(tool.name, arguments)


_VALID_CATEGORIES = ["admin", "organization", "user", "issue", "pull_request", "repository", "misc"]


def _format_filtered_tools_note(filtered_tools_info: dict[str, Any] | None) -> str:
    """Return a note about filtered (hidden) tools, or empty string.

    .. note::
        This note reveals enumeration data about tools the agent's token
        cannot reach (scope-restricted, config-excluded, deprecated counts).
        If this becomes a security concern for certain deployments, gate the
        note behind a config flag (e.g. ``show_hidden_tool_counts`` in
        ``mcp_filter.yaml``) rather than removing it — the information is
        valuable for agent UX.
    """
    if not filtered_tools_info:
        return ""
    filtered: dict[str, Any] = filtered_tools_info.get("filtered", {}) or {}
    if not filtered:
        return ""

    counts: dict[str, int] = {"scope": 0, "excluded": 0, "deprecated": 0}
    for info in filtered.values():
        reason: str = info.get("reason", "unknown")
        if reason in counts:
            counts[reason] += 1
    parts: list[str] = []
    if counts["scope"]:
        parts.append(f"{counts['scope']} scope-restricted")
    if counts["excluded"]:
        parts.append(f"{counts['excluded']} config-excluded")
    if counts["deprecated"]:
        parts.append(f"{counts['deprecated']} deprecated")
    if not parts:
        return ""
    return (
        "\n\n**Note:** "
        + ", ".join(parts)
        + " tools are hidden from this listing "
        + "(use `tool_info(name)` to check a specific tool)."
    )


async def _search_tools_impl(  # noqa: PLR0913 - ctx, transform, min_score are framework plumbing
    query: str,
    category: str | None,
    ctx: Context,
    transform: TolerantSearchTransform,
    page: int = 1,
    limit: int = 10,
    min_score: float = SEARCH_MIN_SCORE,
    filtered_tools_info: dict[str, Any] | None = None,
    tool_prefix: str = "",
    fetch_all: bool = False,
) -> ExecutionResult:
    """Core search_tools implementation.

    Fetches the tool catalog via the transform, optionally filters by
    category, then ranks by name match + BM25.  Returns raw data only — an
    :class:`~gitea_mcp_server.tools.result_pipeline.ExecutionResult` — which
    the single result pipeline slices, envelopes, and formats.

    When ``fetch_all=True``, all matching results are returned (the pipeline
    skip-slices); otherwise the out-of-range check here decides between a
    ``list`` result and an ``empty`` result with a message.

    Args:
        query: Natural language query.
        category: Optional category filter.
        ctx: FastMCP context.
        transform: Search transform for tool catalog access.
        page: Page number (1-based).  Ignored when ``fetch_all`` is True.
        limit: Results per page.  Ignored when ``fetch_all`` is True.
        min_score: Minimum normalized BM25 score (0.0-1.0).
        filtered_tools_info: Filter-prediction data for hidden-tool note.
        tool_prefix: Configured namespace prefix (e.g. ``"gitea_"``).
            Used to strip the prefix from tool names before name matching.
        fetch_all: When True, return all matching results without slicing.
    """
    tools = await transform.get_tool_catalog(ctx)
    if category is not None:
        category_lower = category.lower()
        if category_lower not in _VALID_CATEGORIES:
            msg = f"Invalid category '{category}'. Valid categories: {', '.join(_VALID_CATEGORIES)}"
            raise_value_error(msg)
        tools = [t for t in tools if t.tags and category_lower in t.tags]

    texts = [extract_searchable_text_enhanced(t) for t in tools]
    serialized = compact_search_serializer(tools)

    # Get all ranked results (no pre-slicing — the pipeline slices).
    all_items, total_count = search_and_slice(
        serialized,
        texts,
        query,
        1,
        len(serialized) or 1,
        min_score=min_score,
        tool_prefix=tool_prefix,
    )

    cross_link_hints = {
        "workflow guides": "search_docs",
        "data resources": "search_resources",
    }

    if total_count == 0:
        return ExecutionResult(
            data=[],
            total_count=0,
            shape="empty",
            paginated=True,
            message=_empty_results_message(query, cross_link_hints),
        )

    # Check page range before formatting (only when paginating, not fetch_all).
    if not fetch_all:
        start = (page - 1) * limit
        if start >= total_count:
            return ExecutionResult(
                data=[],
                total_count=total_count,
                shape="empty",
                paginated=True,
                message=f"Page {page} is out of range (total results: {total_count}).",
            )

    extras: list[str] = []
    hints = "**Cross-linking hints:**\n"
    for label, tool in cross_link_hints.items():
        hints += f"- For {label}: `{tool}(query)`\n"
    extras.append(hints)

    note = _format_filtered_tools_note(filtered_tools_info)
    if note:
        extras.append(note)

    return ExecutionResult(
        data=all_items,
        total_count=total_count,
        shape="list",
        paginated=True,
        markdown_extras=extras,
    )


async def _tool_info_impl(  # noqa: PLR0913 - name, ctx, transform, tool_prefix, detail, page, limit, openapi_spec, filtered_tools_info
    name: str,
    ctx: Context,
    transform: TolerantSearchTransform,
    tool_prefix: str = "",
    # Keep in sync with DETAIL_PARAM_SCHEMA/DETAIL_PARAM_SCHEMA_CONCISE enum in constants.py
    detail: Literal["concise", "full"] = "concise",
    page: int = 1,
    limit: int = 10,
    openapi_spec: OpenAPISpec | None = None,
    filtered_tools_info: dict[str, Any] | None = None,
) -> ExecutionResult:
    """Core tool_info implementation.

    Accepts both prefixed (``gitea_search_tools``) and bare (``search_tools``)
    tool names.  Tries bare name first, then prepends ``tool_prefix``.

    When ``detail="full"``, the result includes the fully-resolved
    ``output_schema`` alongside the compact ``output_example``.
    Pagination selects the right sub-schema to slice by result type:

    * **Object** results: paginate top-level properties.
    * **Array** results: paginate ``items.properties`` when items are
      objects; preserves the full array structure.
    * **String / other** results: return the full schema unpaginated
      (no meaningful property-level slicing for primitives).

    Returns raw data only — an
    :class:`~gitea_mcp_server.tools.result_pipeline.ExecutionResult` with the
    (pre-sliced) schema and the property count; the single result pipeline
    envelopes and formats it.

    Args:
        openapi_spec: The OpenAPI spec (for ``$ref`` resolution in schemas).
        filtered_tools_info: Filter-prediction data for filtered-tool messages.
        page: Page number for output_schema properties (1-based, detail=full only).
        limit: Properties per page for output_schema (detail=full only).
    """
    tools = await transform.get_tool_catalog(ctx)
    candidates = {name}
    if tool_prefix and not name.startswith(tool_prefix):
        candidates.add(f"{tool_prefix}{name}")
    for tool in tools:
        if tool.name in candidates:
            schema: ToolSchemaResult = serialize_tool_schema(tool, openapi_spec=openapi_spec)
            if detail == "full" and tool.output_schema is not None:
                # FastMCP wraps API tool output_schemas in {"result": {...}}
                # (x-fastmcp-wrap-result). Unwrap to access the actual
                # schema for pagination.
                result_obj = unwrap_result_schema(tool.output_schema) or {}
                # Build the result envelope.  For objects we paginate
                # top-level properties; for arrays we paginate the item
                # properties; for strings/other we return the full schema
                # unchanged (no meaningful pagination).
                if is_object_type(result_obj):
                    result_props = result_obj.get("properties", {})
                    prop_keys = list(result_props.keys())
                    total_props = len(result_props)
                    start = (page - 1) * limit
                    end = start + limit
                    sliced_keys = prop_keys[start:end]
                    result_schema: dict[str, Any] = {
                        "description": result_obj.get("description", ""),
                        "type": "object",
                        "properties": {k: result_props[k] for k in sliced_keys},
                    }
                elif schema_type_is_array(result_obj):
                    items_schema = result_obj.get("items", {})
                    if is_object_type(items_schema):
                        items_props = items_schema.get("properties", {})
                        prop_keys = list(items_props.keys())
                        total_props = len(items_props)
                        start = (page - 1) * limit
                        end = start + limit
                        sliced_keys = prop_keys[start:end]
                        sliced_items = dict(items_schema)
                        sliced_items["properties"] = {k: items_props[k] for k in sliced_keys}
                    else:
                        # Array of primitives or refs — no pagination.
                        sliced_items = items_schema
                        total_props = 1
                    result_schema = {
                        "description": result_obj.get("description", ""),
                        "type": "array",
                        "items": sliced_items,
                    }
                else:
                    # String or other primitive — no meaningful pagination.
                    result_schema = result_obj
                    total_props = 1
                # Rebuild the display schema: replace only the ``result``
                # property with the sliced version and keep the sibling
                # properties (pagination metadata ``has_more`` /
                # ``next_offset`` / ``total_count``, custom descriptions)
                # intact.  Replacing the whole ``properties`` dict here used
                # to drop the pagination envelope that both autogen and
                # synthetic output schemas declare next to ``result``.
                sliced_schema = dict(tool.output_schema)
                properties = dict(sliced_schema.get("properties") or {})
                properties["result"] = result_schema
                sliced_schema["properties"] = properties
                schema["output_schema"] = sliced_schema

                return ExecutionResult(
                    data=schema,
                    total_count=total_props,
                    shape="object",
                    paginated=True,
                    markdown_formatter=format_tool_info_markdown,
                )

            # Concise path returns the full schema unpaginated — still
            # emit the envelope (total_count=None, has_more=False) so the
            # runtime matches the declared schema on every path.
            return ExecutionResult(
                data=schema,
                shape="object",
                paginated=True,
                markdown_formatter=format_tool_info_markdown,
            )

    # Tool not found in the post-filter catalog — check if it's a
    # filtered tool (scope-restricted, config-excluded, or deprecated).
    filter_info = get_filtered_tool_info(name, filtered_tools_info, tool_prefix)
    if filter_info is not None:
        msg = build_filtered_tools_message(name, filter_info, filtered_tools_info)
        raise ValueError(msg) from None

    msg = f"Tool '{name}' not found. Use `search_tools()` to discover available tools."
    raise ValueError(msg) from None


_SEARCH_RESOURCES_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "result": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "mimeType": {"type": "string"},
                    "type": {"type": "string"},
                    "tags": {"type": "array"},
                    "score": {
                        "type": "number",
                        "description": "Normalized relevance score (0.0-1.0). "
                        "1.0 is the top match for this query.",
                    },
                    "required_scope": {"oneOf": [{"type": "string"}, {"type": "null"}]},
                },
                "example": {
                    "uri": "gitea://repos/{owner}/{repo}",
                    "name": "Repository",
                    "description": "Get full repository metadata",
                    "mimeType": "text/markdown",
                    "type": "template",
                    "tags": ["wrapper", "repository"],
                    "score": 1.0,
                    "required_scope": "read:repository",
                },
            },
            "description": "Matching resource definitions ranked by relevance",
        },
        "message": MESSAGE_SCHEMA_PROPERTY,
    },
}


async def _search_resources_impl(  # noqa: PLR0913 - ctx and min_score are framework plumbing
    query: str,
    ctx: Context,
    page: int = 1,
    limit: int = 10,
    min_score: float = SEARCH_MIN_SCORE,
    tool_prefix: str = "",
    fetch_all: bool = False,
) -> ExecutionResult:
    """Core search_resources implementation.

    Fetches all registered MCP resources via ``mcp_list_resources_impl``,
    runs name match + BM25 ranking, and returns raw data only — an
    :class:`~gitea_mcp_server.tools.result_pipeline.ExecutionResult` — which
    the single result pipeline slices, envelopes, and formats.

    When ``fetch_all=True``, returns all matching results without page
    slicing (in-memory search, no loop needed).

    Args:
        query: Natural language query.
        ctx: FastMCP context.
        page: Page number (1-based).  Ignored when ``fetch_all`` is True.
        limit: Results per page.  Ignored when ``fetch_all`` is True.
        min_score: Minimum normalized BM25 score (0.0-1.0).
        tool_prefix: Configured namespace prefix (e.g. ``"gitea_"``).
            Used to strip the prefix from resource names before name matching.
        fetch_all: When True, return all matching results without slicing.
    """
    # Deferred import to avoid circular chain:
    # mcp_tools → tools.examples → tools.__init__ → tools.search → mcp_tools
    from gitea_mcp_server.tools.mcp_tools import mcp_list_resources_impl  # noqa: PLC0415, I001 - deferred to break circular import

    raw = await mcp_list_resources_impl(ctx)
    resources = raw.get("resources", [])
    texts = [extract_resource_text(r) for r in resources]

    # Get all ranked results (no pre-slicing).
    all_items, total_count = search_and_slice(
        resources,
        texts,
        query,
        1,
        len(resources) or 1,
        min_score=min_score,
        tool_prefix=tool_prefix,
    )

    cross_link_hints = {
        "workflow guides": "search_docs",
        "API tools": "search_tools",
    }

    if total_count == 0:
        return ExecutionResult(
            data=[],
            total_count=0,
            shape="empty",
            paginated=True,
            message=_empty_results_message(query, cross_link_hints),
        )

    # Check page range before formatting (only when paginating, not fetch_all).
    if not fetch_all:
        start = (page - 1) * limit
        if start >= total_count:
            return ExecutionResult(
                data=[],
                total_count=total_count,
                shape="empty",
                paginated=True,
                message=f"Page {page} is out of range (total results: {total_count}).",
            )

    extras: list[str] = []
    hints = "**Cross-linking hints:**\n"
    for label, tool in cross_link_hints.items():
        hints += f"- For {label}: `{tool}(query)`\n"
    extras.append(hints)

    return ExecutionResult(
        data=all_items,
        total_count=total_count,
        shape="list",
        paginated=True,
        markdown_extras=extras,
    )


# ── Registration helper ────────────────────────────────────────────────


def register_synthetic_tools(
    mcp: Any,
    transform: TolerantSearchTransform,
    tool_prefix: str = "",
    openapi_spec: OpenAPISpec | None = None,
    filtered_tools_info: dict[str, Any] | None = None,
) -> None:
    """Register synthetic tools (call_tool, search_tools, tool_info, search_resources) on the FastMCP server.

    These tools were previously created dynamically inside TolerantSearchTransform.
    Now they're properly registered via ``mcp.tool()`` so they're findable through
    ``ctx.fastmcp.call_tool()`` and carry the ``synthetic`` tag for agent awareness.

    Args:
        mcp: The FastMCP server instance
        transform: The search transform instance
        tool_prefix: Optional prefix used by GiteaNamespace (e.g. ``"gitea_"``).
            When provided, ``call_tool`` and ``tool_info`` will also accept bare
            (unprefixed) tool names by trying the prefixed variant as a fallback.
        openapi_spec: The OpenAPI spec (for ``$ref`` resolution).
        filtered_tools_info: Filter-prediction data for filtered-tool messages.
    """

    async def search_tools_fn(  # noqa: PLR0913 - ctx is FastMCP DI plumbing
        query: Annotated[str, "Natural language query to search for tools"],
        category: Annotated[
            str | None, f"Optional category to filter by: {', '.join(_VALID_CATEGORIES)}"
        ] = None,
        page: Annotated[int, "Page number (1-based, default 1)"] = 1,
        limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        min_score: Annotated[
            float,
            "Minimum relevance score (0.0-1.0). 0.0 returns everything, "
            "0.1 requires at least 10% as relevant as the top result, "
            "1.0 requires perfect match.",
        ] = SEARCH_MIN_SCORE,
        fetch_all: Annotated[
            bool,
            "When true, return all matching results instead of a single page. "
            "Results are merged into one response (in-memory, no looping needed).",
        ] = False,
        ctx: Context = CurrentContext(),
    ) -> ExecutionResult:
        return await _search_tools_impl(
            query,
            category,
            ctx,
            transform,
            page,
            limit,
            min_score=min_score,
            filtered_tools_info=filtered_tools_info,
            tool_prefix=tool_prefix,
            fetch_all=fetch_all,
        )

    search_tools_spec = SyntheticToolSpec(
        impl=search_tools_fn,
        name="search_tools",
        description="Search for tools by natural language query. Returns matching tool definitions with name, description, tags, and annotations. Use this to discover Gitea API tools available on this server.",
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "score": {
                                "type": "number",
                                "description": "Normalized relevance score (0.0-1.0). "
                                "1.0 is the top match for this query.",
                            },
                            "annotations": {
                                "type": "object",
                                "properties": {
                                    "title": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {"type": "null"},
                                        ],
                                        "description": "Tool title (may be null if not explicitly set)",
                                    },
                                    "readOnlyHint": {"type": "boolean"},
                                    "destructiveHint": {"type": "boolean"},
                                    "idempotentHint": {"type": "boolean"},
                                    "openWorldHint": {"type": "boolean"},
                                },
                            },
                        },
                        "example": {
                            "name": "gitea_issue_list_issues",
                            "description": "List issues in a repository",
                            "tags": ["issue"],
                            "score": 1.0,
                            "annotations": {
                                "title": "List Issues",
                                "readOnlyHint": True,
                                "destructiveHint": False,
                                "idempotentHint": True,
                                "openWorldHint": True,
                            },
                        },
                    },
                    "description": "Matching tool definitions with name, description, tags and annotations",
                },
                "message": MESSAGE_SCHEMA_PROPERTY,
            },
        },
        paginated=True,
    )

    async def call_tool_fn(
        name: Annotated[
            str,
            "The name of the tool to call. Never 'call_tool' itself — the "
            "proxy cannot invoke itself; call it directly instead.",
        ],
        arguments: Annotated[Any, "Arguments to pass to the tool (dict or JSON string)"] = None,
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _call_tool_impl(
            name, arguments, ctx, tool_prefix, filtered_tools_info=filtered_tools_info
        )

    call_tool_spec = SyntheticToolSpec(
        impl=call_tool_fn,
        wrap=False,
        name="call_tool",
        description=(
            "Call a tool by name with arguments. Acts as a proxy to invoke any "
            "registered tool (never itself): pass the target tool's name as "
            "`name` — not 'call_tool'. Use this when you know the tool name and "
            "have the arguments ready."
        ),
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=False, open_world=True),
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "description": "Result of the tool call, wrapped in result for consistency",
                    "example": {"id": 1, "name": "example-repo", "description": "Example output"},
                },
            },
        },
    )

    async def tool_info_fn(
        name: Annotated[str, "The exact name of the tool to inspect"],
        detail: Annotated[
            # Keep in sync with DETAIL_PARAM_SCHEMA/DETAIL_PARAM_SCHEMA_CONCISE enum in constants.py
            Literal["concise", "full"],
            str(DETAIL_PARAM_SCHEMA_CONCISE["description"]),
        ] = "concise",
        page: Annotated[
            int,
            "Page number for output_schema properties (1-based). Only used when detail=full.",
        ] = 1,
        limit: Annotated[
            int,
            "Properties per page for output_schema. Only used when detail=full.",
        ] = 10,
        ctx: Context = CurrentContext(),
    ) -> ExecutionResult:
        return await _tool_info_impl(
            name,
            ctx,
            transform,
            tool_prefix,
            detail=detail,
            page=page,
            limit=limit,
            openapi_spec=openapi_spec,
            filtered_tools_info=filtered_tools_info,
        )

    tool_info_spec = SyntheticToolSpec(
        impl=tool_info_fn,
        name="tool_info",
        description="Get the full schema for a registered tool by exact name. Returns parameter details, output example, annotations, and tags. Use after search_tools to inspect a specific tool before calling it.",
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "parameters": {"type": "object"},
                        "output_example": {
                            "anyOf": [
                                {"type": "object"},
                                {"type": "array"},
                                {"type": "string"},
                            ],
                            "description": "Compact type-summary example (fields with type names for refs)",
                        },
                        "output_schema": {
                            "type": "object",
                            "description": "Fully-resolved output JSON Schema (included only when detail='full')",
                        },
                        "annotations": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {"type": "null"},
                                    ],
                                    "description": "Tool title (may be null if not explicitly set)",
                                },
                                "readOnlyHint": {"type": "boolean"},
                                "destructiveHint": {"type": "boolean"},
                                "idempotentHint": {"type": "boolean"},
                                "openWorldHint": {"type": "boolean"},
                            },
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "version": {"type": "string"},
                    },
                    "description": "Full tool schema",
                    "example": {
                        "name": "gitea_issue_get_issue",
                        "description": "Get a single issue by index",
                        "parameters": {
                            "properties": {
                                "owner": {"type": "string", "description": "owner of the repo"},
                                "repo": {"type": "string", "description": "name of the repo"},
                                "index": {"type": "integer", "description": "index of the issue"},
                            },
                        },
                        "output_example": {
                            "id": 0,
                            "title": "Example Title",
                            "state": "StateType",
                            "body": "Issue body content",
                            "assignee": {"$ref": "User"},
                            "labels": [{"$ref": "Label"}],
                        },
                        "output_schema": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "title": {"type": "string"},
                                "assignee": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "login": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "annotations": {
                            "title": "Get An Issue",
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "idempotentHint": True,
                            "openWorldHint": True,
                        },
                        "tags": ["issue"],
                        "version": "1.0",
                    },
                },
            },
        },
        paginated=True,
        virtual_params={"format"},
    )

    async def search_resources_fn(  # noqa: PLR0913 - min_score is a new config axis
        query: Annotated[str, "Natural language query to search for resources"],
        page: Annotated[int, "Page number (1-based, default 1)"] = 1,
        limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        min_score: Annotated[
            float,
            "Minimum relevance score (0.0-1.0). 0.0 returns everything, "
            "0.1 requires at least 10% as relevant as the top result, "
            "1.0 requires perfect match.",
        ] = SEARCH_MIN_SCORE,
        fetch_all: Annotated[
            bool,
            "When true, return all matching results instead of a single page. "
            "Results are merged into one response (in-memory, no looping needed).",
        ] = False,
        ctx: Context = CurrentContext(),
    ) -> ExecutionResult:
        return await _search_resources_impl(
            query,
            ctx,
            page,
            limit,
            min_score=min_score,
            tool_prefix=tool_prefix,
            fetch_all=fetch_all,
        )

    search_resources_spec = SyntheticToolSpec(
        impl=search_resources_fn,
        name="search_resources",
        description="Search MCP resources by natural language query. "
        "Uses name-match boosting then BM25 to find the most relevant resources matching your query. "
        "Searches across resource URI, name, description, and tags. "
        "Use this when you know what kind of information you want but not the "
        "exact resource URI. For an exhaustive listing, use list_resources instead.",
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
        output_schema=_SEARCH_RESOURCES_OUTPUT_SCHEMA,
        paginated=True,
    )

    register_all_synthetic_tools(
        mcp,
        [
            search_tools_spec,
            call_tool_spec,
            tool_info_spec,
            search_resources_spec,
        ],
    )


__all__ = [
    "TolerantBM25Search",
    "TolerantSearchTransform",
    "compact_search_serializer",
    "extract_resource_text",
    "extract_searchable_text_enhanced",
    "register_synthetic_tools",
    "search_and_slice",
]
