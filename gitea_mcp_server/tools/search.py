"""Search transform and synthetic tools for tool discovery.

BM25 search engine lives in gitea_mcp_server/search.py (flat infra layer).
This module contains Tool-specific search wrappers, the TolerantSearchTransform,
and the shared BM25+format pipeline used by both search_tools and search_resources.
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
from mcp.types import TextContent

from gitea_mcp_server.constants import (
    SEARCH_CATEGORY_ALIASES,
    SEARCH_MIN_SCORE,
    SEARCH_NAME_BOOST,
)
from gitea_mcp_server.format import _format_as_markdown, _format_tool_info_markdown, apply_format
from gitea_mcp_server.models import ToolSchemaResult, ToolSearchEntry
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.pagination import PAGINATION_KEYS, add_pagination_metadata, apply_pagination
from gitea_mcp_server.search import BM25SearchEngine
from gitea_mcp_server.tools.customize import synthetic_annotations
from gitea_mcp_server.tools.errors import (
    _raise_value_error,
    _raise_value_error_from,
)
from gitea_mcp_server.tools.examples import _serialize_tool_schema
from gitea_mcp_server.tools.filter_info import (
    build_filtered_tools_message,
    get_filtered_tool_info,
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


def _search_and_slice(  # noqa: PLR0913 - 6 params but all are independent config axes
    items: list[Any],
    texts: list[str],
    query: str,
    page: int,
    limit: int,
    min_score: float = SEARCH_MIN_SCORE,
) -> tuple[list[Any], int]:
    """BM25 rank items, then slice by page/limit.

    Returns ``(page_items, total_count)`` where ``total_count`` is the total
    number of items that matched the query (before slicing), and
    ``page_items`` are the items on the requested page.  Each item in
    ``page_items`` is a shallow copy of the corresponding input item with an
    extra ``score`` key (normalized 0.0-1.0, where 1.0 is the top match for
    this query) so callers/agents can apply their own relevance threshold.

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
    """
    if not items or not texts:
        return [], 0

    engine = BM25SearchEngine()
    # Score everything (len(items) is small - ~200 tools, ~35 resources)
    ranked = engine.search_with_scores(texts, query, len(items), min_score=min_score)
    total_count = len(ranked)

    start = (page - 1) * limit
    end = start + limit
    page_ranked = ranked[start:end]
    # Attach the normalized score to each result item so agents can apply
    # their own relevance threshold instead of relying solely on min_score.
    page_items = [{**items[i], "score": round(score, 4)} for i, score in page_ranked]
    return page_items, total_count


# ============================================================================
# Text extraction helpers
# ============================================================================


def _extract_searchable_text_enhanced(tool: Tool) -> str:
    """Enhanced searchable text extraction for better tool discoverability."""
    parts = [tool.name] * SEARCH_NAME_BOOST

    if tool.annotations and tool.annotations.title:
        parts.append(tool.annotations.title)

    if tool.description:
        parts.append(tool.description)

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


def _extract_resource_text(entry: Mapping[str, Any]) -> str:
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
        texts = [_extract_searchable_text_enhanced(t) for t in tools]
        self._indexed_tools = tools
        indices = self._engine.search(texts, query, max_results)
        return [self._indexed_tools[i] for i in indices]


# ============================================================================
# Search Transform + Synthetic Tools (from tool_search.py)
# ============================================================================


def _compact_search_serializer(tools: Sequence[Tool]) -> list[ToolSearchEntry]:
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
            kwargs["search_result_serializer"] = _compact_search_serializer
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
) -> ToolResult:
    """Core call_tool implementation.

    Acts as a transparent proxy: resolves the tool name, forwards
    arguments, and returns the inner tool's result unchanged.
    Every tool on this server handles its own ``format`` parameter
    natively, so the proxy does not re-format.

    Filtered-tool error messages (scope, exclusion, deprecation) are
    handled by :class:`FilteredToolMiddleware` at the MCP protocol level,
    so the proxy does not duplicate that check.
    """
    if name == "call_tool":
        msg = "'call_tool' cannot call itself - call it directly instead"
        _raise_value_error(msg)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as e:
            msg = f"Invalid JSON in arguments: {e}"
            _raise_value_error_from(msg, e)
    if arguments is not None and not isinstance(arguments, dict):
        msg = f"Arguments must be a dict or JSON string, got {type(arguments).__name__}"
        _raise_value_error(msg)

    tool = await _find_tool_by_name(name, ctx, tool_prefix)

    if tool is None:
        msg = (
            f"Tool '{name}' not found. "
            "Use `search_tools()` to discover available tools."
        )
        _raise_value_error(msg)

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
        "\n\n**Note:** " + ", ".join(parts)
        + " tools are hidden from this listing "
        + "(use `tool_info(name)` to check a specific tool)."
    )


async def _search_tools_impl(  # noqa: PLR0913 - ctx, transform, min_score are framework plumbing
    query: str,
    category: str | None,
    format: str,
    ctx: Context,
    transform: TolerantSearchTransform,
    page: int = 1,
    limit: int = 10,
    min_score: float = SEARCH_MIN_SCORE,
    filtered_tools_info: dict[str, Any] | None = None,
) -> ToolResult:
    """Core search_tools implementation.

    Fetches the tool catalog via the transform, optionally filters by
    category, then BM25 ranks and returns a paginated, formatted result.

    Args:
        query: Natural language query.
        category: Optional category filter.
        format: Output format.
        ctx: FastMCP context.
        transform: Search transform for tool catalog access.
        page: Page number (1-based).
        limit: Results per page.
        min_score: Minimum normalized BM25 score (0.0-1.0).
        filtered_tools_info: Filter-prediction data for hidden-tool note.
    """
    tools = await transform.get_tool_catalog(ctx)
    if category is not None:
        category_lower = category.lower()
        if category_lower not in _VALID_CATEGORIES:
            msg = f"Invalid category '{category}'. Valid categories: {', '.join(_VALID_CATEGORIES)}"
            _raise_value_error(msg)
        tools = [t for t in tools if t.tags and category_lower in t.tags]

    texts = [_extract_searchable_text_enhanced(t) for t in tools]
    serialized = _compact_search_serializer(tools)
    page_items, total_count = _search_and_slice(
        serialized, texts, query, page, limit, min_score=min_score
    )

    cross_link_hints = {
        "workflow guides": "search_docs",
        "data resources": "search_resources",
    }

    if total_count == 0:
        text = _empty_results_message(query, cross_link_hints)
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": [], "_hint": text},
        )

    if not page_items:
        text = f"Page {page} is out of range (total results: {total_count})."
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": [], "_hint": text},
        )

    extras: list[str] = []
    if format == "markdown":
        if cross_link_hints:
            hints = "**Cross-linking hints:**\n"
            for label, tool in cross_link_hints.items():
                hints += f"- For {label}: `{tool}(query)`\n"
            extras.append(hints)

        note = _format_filtered_tools_note(filtered_tools_info)
        if note:
            extras.append(note)

        pagination_table = _format_as_markdown(
            {k: v for k, v in add_pagination_metadata(
                {"result": page_items}, page, limit, total_count
            ).items() if k in PAGINATION_KEYS},
            None,
        )
        extras.append(pagination_table)

    return apply_pagination(
        apply_format(page_items, format, markdown_extras=extras or None),
        page, limit, total_count,
    )


async def _tool_info_impl(  # noqa: PLR0913 - name, format, ctx, transform, tool_prefix, detail
    name: str,
    format: str,
    ctx: Context,
    transform: TolerantSearchTransform,
    tool_prefix: str = "",
    detail: Literal["concise", "full"] = "concise",
    page: int = 1,
    limit: int = 10,
    openapi_spec: OpenAPISpec | None = None,
    filtered_tools_info: dict[str, Any] | None = None,
) -> ToolResult:
    """Core tool_info implementation.

    Accepts both prefixed (``gitea_search_tools``) and bare (``search_tools``)
    tool names.  Tries bare name first, then prepends ``tool_prefix``.

    When ``detail="full"``, the result includes the fully-resolved
    ``output_schema`` alongside the compact ``output_example``.  The
    ``output_schema`` is paginated by its top-level properties when it
    exceeds ``limit`` properties per page.

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
            schema: ToolSchemaResult = _serialize_tool_schema(tool, openapi_spec=openapi_spec)
            if detail == "full" and tool.output_schema is not None:
                # FastMCP wraps API tool output_schemas in {"result": {...}}
                # (x-fastmcp-wrap-result). The actual properties to paginate
                # are under result.properties.
                result_obj = tool.output_schema.get("properties", {}).get("result", {})
                result_props = result_obj.get("properties", {})
                total_props = len(result_props)
                # Slice result properties by page/limit so agents can page
                # through large schemas instead of receiving the full schema.
                start = (page - 1) * limit
                end = start + limit
                prop_keys = list(result_props.keys())
                sliced_keys = prop_keys[start:end]
                sliced_schema = dict(tool.output_schema)
                sliced_schema["properties"] = {
                    "result": {
                        "description": result_obj.get("description", ""),
                        "type": "object",
                        "properties": {
                            k: result_props[k] for k in sliced_keys
                        },
                    }
                }
                schema["output_schema"] = sliced_schema

                result = apply_format(schema, format, markdown_formatter=_format_tool_info_markdown)

                # Add pagination metadata to structured_content so agents can
                # discover total property count and navigate pages.
                result = apply_pagination(result, page, limit, total_props)
            else:
                result = apply_format(schema, format, markdown_formatter=_format_tool_info_markdown)

            return result

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
    },
}


async def _search_resources_impl(  # noqa: PLR0913 - ctx and min_score are framework plumbing
    query: str,
    format: str,
    ctx: Context,
    page: int = 1,
    limit: int = 10,
    min_score: float = SEARCH_MIN_SCORE,
) -> ToolResult:
    """Core search_resources implementation.

    Fetches all registered MCP resources via ``_mcp_list_resources_impl``,
    runs BM25 ranking, and returns a paginated, formatted result.

    Args:
        query: Natural language query.
        format: Output format.
        ctx: FastMCP context.
        page: Page number (1-based).
        limit: Results per page.
        min_score: Minimum normalized BM25 score (0.0-1.0).
    """
    # Deferred import to avoid circular chain:
    # mcp_tools → tools.examples → tools.__init__ → tools.search → mcp_tools
    from gitea_mcp_server.mcp_tools import _mcp_list_resources_impl  # noqa: PLC0415, I001 - deferred to break circular import

    raw = await _mcp_list_resources_impl(ctx)
    resources = raw.get("resources", [])
    texts = [_extract_resource_text(r) for r in resources]
    page_items, total_count = _search_and_slice(
        resources, texts, query, page, limit, min_score=min_score
    )

    cross_link_hints = {
        "workflow guides": "search_docs",
        "API tools": "search_tools",
    }

    if total_count == 0:
        text = _empty_results_message(query, cross_link_hints)
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": [], "_hint": text},
        )

    if not page_items:
        text = f"Page {page} is out of range (total results: {total_count})."
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": [], "_hint": text},
        )

    extras: list[str] = []
    if format == "markdown":
        if cross_link_hints:
            hints = "**Cross-linking hints:**\n"
            for label, tool in cross_link_hints.items():
                hints += f"- For {label}: `{tool}(query)`\n"
            extras.append(hints)

        pagination_table = _format_as_markdown(
            {k: v for k, v in add_pagination_metadata(
                {"result": page_items}, page, limit, total_count
            ).items() if k in PAGINATION_KEYS},
            None,
        )
        extras.append(pagination_table)

    return apply_pagination(
        apply_format(page_items, format, markdown_extras=extras or None),
        page, limit, total_count,
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
        format: Annotated[
            str,
            "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)",
        ] = "markdown",
        page: Annotated[int, "Page number (1-based, default 1)"] = 1,
        limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        min_score: Annotated[
            float,
            "Minimum relevance score (0.0-1.0). 0.0 returns everything, "
            "0.1 requires at least 10% as relevant as the top result, "
            "1.0 requires perfect match.",
        ] = SEARCH_MIN_SCORE,
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _search_tools_impl(
            query, category, format, ctx, transform, page, limit,
            min_score=min_score, filtered_tools_info=filtered_tools_info,
        )

    mcp.tool(
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
            },
        },
    )(search_tools_fn)

    async def call_tool_fn(
        name: Annotated[str, "The name of the tool to call"],
        arguments: Annotated[Any, "Arguments to pass to the tool (dict or JSON string)"] = None,
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _call_tool_impl(name, arguments, ctx, tool_prefix)

    mcp.tool(
        name="call_tool",
        description="Call a tool by name with arguments. Acts as a proxy to invoke any registered tool. Use this when you know the tool name and have the arguments ready.",
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
    )(call_tool_fn)

    async def tool_info_fn(  # noqa: PLR0913 - 6 params: name, format, detail, page, limit, ctx
        name: Annotated[str, "The exact name of the tool to inspect"],
        format: Annotated[
            str,
            "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)",
        ] = "markdown",
        detail: Annotated[
            Literal["concise", "full"],
            "Detail level: 'concise' (default) for compact type-summary output_example; "
            "'full' to also include the resolved output_schema",
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
    ) -> ToolResult:
        return await _tool_info_impl(
            name, format, ctx, transform, tool_prefix,
            detail=detail, page=page, limit=limit,
            openapi_spec=openapi_spec,
            filtered_tools_info=filtered_tools_info,
        )

    mcp.tool(
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
    )(tool_info_fn)

    async def search_resources_fn(  # noqa: PLR0913 - min_score is a new config axis
        query: Annotated[str, "Natural language query to search for resources"],
        format: Annotated[str, "Output format: markdown (default), json, or raw"] = "markdown",
        page: Annotated[int, "Page number (1-based, default 1)"] = 1,
        limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        min_score: Annotated[
            float,
            "Minimum relevance score (0.0-1.0). 0.0 returns everything, "
            "0.1 requires at least 10% as relevant as the top result, "
            "1.0 requires perfect match.",
        ] = SEARCH_MIN_SCORE,
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _search_resources_impl(
            query, format, ctx, page, limit, min_score=min_score
        )

    mcp.tool(
        name="search_resources",
        description="Search MCP resources by natural language query. "
        "Uses BM25 ranking to find the most relevant resources matching your query. "
        "Searches across resource URI, name, description, and tags. "
        "Use this when you know what kind of information you want but not the "
        "exact resource URI. For an exhaustive listing, use list_resources instead.",
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
        output_schema=_SEARCH_RESOURCES_OUTPUT_SCHEMA,
    )(search_resources_fn)


__all__ = [
    "TolerantBM25Search",
    "TolerantSearchTransform",
    "_call_tool_impl",
    "_compact_search_serializer",
    "_extract_resource_text",
    "_extract_searchable_text_enhanced",
    "_find_tool_by_name",
    "_search_and_slice",
    "_search_resources_impl",
    "_search_tools_impl",
    "_tool_info_impl",
    "register_synthetic_tools",
]
