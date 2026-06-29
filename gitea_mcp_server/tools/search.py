"""Search transform and synthetic tools for tool discovery.

BM25 search engine lives in gitea_mcp_server/search.py (flat infra layer).
This module contains Tool-specific search wrappers, the TolerantSearchTransform,
and the shared BM25+format pipeline used by both search_tools and search_resources.
"""

import json
from collections.abc import Sequence
from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations
from fastmcp.utilities.versions import VersionSpec
from mcp.types import TextContent

from gitea_mcp_server.constants import (
    SEARCH_CATEGORY_ALIASES,
    SEARCH_MAX_RESULTS,
    SEARCH_NAME_BOOST,
)
from gitea_mcp_server.format import _format_as_markdown
from gitea_mcp_server.mcp_tools import _mcp_list_resources_impl
from gitea_mcp_server.search import BM25SearchEngine
from gitea_mcp_server.tools.errors import (
    _raise_value_error,
    _raise_value_error_from,
)
from gitea_mcp_server.tools.examples import _serialize_tool_schema

PAGINATION_KEYS = ("has_more", "next_offset", "total_count")
"""Keys in structured_content that carry pagination metadata."""


def _format_result(
    result: ToolResult,
    fmt: str,
    output_schema: dict[str, Any] | None = None,
) -> ToolResult:
    """Reformat ToolResult content by format.

    ``structured_content`` is always preserved as raw data.
    For non-JSON or binary results, all formats return unchanged.
    """
    if fmt == "raw" or not result.structured_content:
        return result

    data = result.structured_content.get("result")
    if data is None:
        return result

    content: str | None = None

    if fmt == "json":
        content = json.dumps(data, indent=2)

    elif fmt == "markdown" and isinstance(data, (dict, list)):
        inner = (
            output_schema.get("properties", {}).get("result", {})
            if output_schema
            else None
        )
        content = _format_as_markdown(data, inner)

        pagination = {
            k: result.structured_content[k]
            for k in PAGINATION_KEYS
            if k in result.structured_content
        }
        if pagination:
            content += "\n\n---\n"
            content += _format_as_markdown(pagination, None)

    if content is not None:
        return ToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content=result.structured_content,
            meta=result.meta,
        )

    return result


# ============================================================================
# Shared BM25 + format pipeline (used by search_tools and search_resources)
# ============================================================================


def _empty_results_message(query: str, cross_link_hints: dict[str, str] | None) -> str:
    """Build a helpful message when a search returns no results."""
    text = f"No results found for '{query}'.\n\n**Cross-linking hints:**\n"
    if cross_link_hints:
        for label, tool in cross_link_hints.items():
            text += f"- For {label}: `{tool}(query)`\n"
    return text


def _search_and_format(  # noqa: PLR0913 — 6 params for a well-documented internal helper
    items: list[dict[str, Any]],
    texts: list[str],
    query: str,
    fmt: str,
    max_results: int = SEARCH_MAX_RESULTS,
    *,
    cross_link_hints: dict[str, str] | None = None,
) -> ToolResult:
    """BM25 search → format → ToolResult.

    Shared pipeline used by both ``_search_tools_impl`` and
    ``_search_resources_impl``.  Receives pre-serialized items (dicts) and
    their searchable text strings, runs BM25 ranking, formats the output
    (markdown/json/raw), appends a cross-linking footer, and returns a
    ``ToolResult`` with both display content and structured data.

    Args:
        items: Serialized item dicts (aligned with ``texts`` by index).
        texts: Searchable text strings, one per item.
        query: Natural language query.
        fmt: Output format — ``"markdown"``, ``"json"``, or ``"raw"``.
        max_results: Maximum number of results to return.
        cross_link_hints: Mapping of label → tool name for the footer,
            e.g. ``{"workflow guides": "search_docs"}``.

    Returns:
        ToolResult with formatted content and ``structured_content["result"]``
        containing the ranked items.
    """
    if not items or not texts:
        text = _empty_results_message(query, cross_link_hints)
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": []},
        )

    engine = BM25SearchEngine()
    indices = engine.search(texts, query, max_results)
    results = [items[i] for i in indices]

    if not results:
        text = _empty_results_message(query, cross_link_hints)
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": []},
        )

    if fmt == "raw":
        return ToolResult(structured_content={"result": results})

    serialized: str = (
        json.dumps(results, indent=2)
        if fmt == "json"
        else _format_as_markdown(results, None)
    )

    if fmt == "markdown" and cross_link_hints:
        serialized += "\n\n---\n**Cross-linking hints:**\n"
        for label, tool in cross_link_hints.items():
            serialized += f"- For {label}: `{tool}(query)`\n"

    return ToolResult(
        content=[TextContent(type="text", text=serialized)],
        structured_content={"result": results},
    )


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


def _extract_resource_text(entry: dict[str, Any]) -> str:
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


def _compact_search_serializer(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Serialize tools to compact dicts (name, description, tags, annotations) for search display."""
    result = []
    for tool in tools:
        annotations = None
        if tool.annotations:
            a = tool.annotations
            annotations = {
                k: v
                for k, v in {
                    "title": a.title,
                    "readOnlyHint": a.readOnlyHint,
                    "destructiveHint": a.destructiveHint,
                    "idempotentHint": a.idempotentHint,
                }.items()
                if v is not None
            }
        item: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description or "",
            "tags": list(tool.tags) if tool.tags else [],
        }
        if annotations:
            item["annotations"] = annotations
        result.append(item)
    return result


class TolerantSearchTransform(BM25SearchTransform):
    """Search transform for lazy-loading tool discovery.

    Unlike the base class, this transform does NOT register synthetic tools
    (search_tools, call_tool, tool_info) — those are normal ``mcp.tool()``
    registrations in ``register_synthetic_tools()``. The transform only
    controls which tools appear in ``list_tools()`` output (pinned set) and
    provides BM25 search over the catalog.
    """

    def __init__(self, **kwargs: Any) -> None:
        if "search_result_serializer" not in kwargs:
            kwargs["search_result_serializer"] = _compact_search_serializer
        super().__init__(**kwargs)
        self._searcher = TolerantBM25Search()

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        pinned = [t for t in tools if t.name in self._always_visible]
        return [*pinned]

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        return self._searcher.search(tools, query, self._max_results)

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Resolve all tools through normal provider lookup.

        No special intercepts — synthetic tools are registered as normal
        tools on the provider via ``register_synthetic_tools()``.
        """
        return await call_next(name, version=version)


# ── Synthetic tool implementations (exported for testing) ──────────────


async def _call_tool_impl(
    name: str,
    arguments: Any,
    format: str,
    ctx: Context,
) -> ToolResult:
    """Core call_tool implementation."""
    if name == "call_tool":
        msg = "'call_tool' cannot call itself — call it directly instead"
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
    result = await ctx.fastmcp.call_tool(name, arguments)
    output_schema = None
    if format == "markdown":
        tool_obj = await ctx.fastmcp.get_tool(name)
        if tool_obj is not None:
            output_schema = tool_obj.output_schema
    return _format_result(result, format, output_schema)


_VALID_CATEGORIES = ["admin", "organization", "user", "issue", "pull_request", "repository", "misc"]


async def _search_tools_impl(
    query: str,
    category: str | None,
    format: str,
    ctx: Context,
    transform: TolerantSearchTransform,
) -> ToolResult:
    """Core search_tools implementation.

    Fetches the tool catalog via the transform, optionally filters by
    category, then delegates to ``_search_and_format`` for BM25 ranking
    and output formatting.
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
    return _search_and_format(
        items=serialized,
        texts=texts,
        query=query,
        fmt=format,
        cross_link_hints={
            "workflow guides": "search_docs",
            "data resources": "search_resources",
        },
    )


async def _tool_info_impl(
    name: str,
    format: str,
    ctx: Context,
    transform: TolerantSearchTransform,
) -> ToolResult:
    """Core tool_info implementation."""
    tools = await transform.get_tool_catalog(ctx)
    for tool in tools:
        if tool.name == name:
            return _format_result(ToolResult(structured_content={"result": _serialize_tool_schema(tool)}), format)
    msg = f"Tool '{name}' not found"
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
                    "required_scope": {"oneOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
            "description": "Matching resource definitions ranked by relevance",
        },
    },
}


async def _search_resources_impl(
    query: str,
    format: str,
    ctx: Context,
) -> ToolResult:
    """Core search_resources implementation.

    Fetches all registered MCP resources via ``_mcp_list_resources_impl``,
    runs BM25 ranking, and returns formatted results via ``_search_and_format``.
    """
    raw = await _mcp_list_resources_impl(ctx)
    resources = raw.get("resources", [])
    texts = [_extract_resource_text(r) for r in resources]
    return _search_and_format(
        items=resources,
        texts=texts,
        query=query,
        fmt=format,
        cross_link_hints={
            "workflow guides": "search_docs",
            "API tools": "search_tools",
        },
    )


# ── Registration helper ────────────────────────────────────────────────


def register_synthetic_tools(
    mcp: Any,
    transform: TolerantSearchTransform,
) -> None:
    """Register synthetic tools (call_tool, search_tools, tool_info) on the FastMCP server.

    These tools were previously created dynamically inside TolerantSearchTransform.
    Now they're properly registered via ``mcp.tool()`` so they're findable through
    ``ctx.fastmcp.call_tool()`` and carry the ``synthetic`` tag for agent awareness.
    """

    async def search_tools_fn(
        query: Annotated[str, "Natural language query to search for tools"],
        category: Annotated[str | None, f"Optional category to filter by: {', '.join(_VALID_CATEGORIES)}"] = None,
        format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _search_tools_impl(query, category, format, ctx, transform)

    mcp.tool(
        name="search_tools",
        description="Search for tools by natural language query. Returns matching tool definitions with name, description, tags, and annotations. Use this to discover Gitea API tools available on this server.",
        tags={"synthetic"},
        annotations=ToolAnnotations(openWorldHint=False),
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
                            "annotations": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "readOnlyHint": {"type": "boolean"},
                                    "destructiveHint": {"type": "boolean"},
                                    "idempotentHint": {"type": "boolean"},
                                },
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
        format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _call_tool_impl(name, arguments, format, ctx)

    mcp.tool(
        name="call_tool",
        description="Call a tool by name with arguments. Acts as a proxy to invoke any registered tool. Use this when you know the tool name and have the arguments ready.",
        tags={"synthetic"},
        annotations=ToolAnnotations(openWorldHint=True),
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "description": "Result of the tool call, wrapped in result for consistency",
                },
            },
        },
    )(call_tool_fn)

    async def tool_info_fn(
        name: Annotated[str, "The exact name of the tool to inspect"],
        format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _tool_info_impl(name, format, ctx, transform)

    mcp.tool(
        name="tool_info",
        description="Get the full schema for a registered tool by exact name. Returns parameter details, output example, annotations, and tags. Use after search_tools to inspect a specific tool before calling it.",
        tags={"synthetic"},
        annotations=ToolAnnotations(openWorldHint=False),
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "parameters": {"type": "object"},
                        "output_example": {"description": "Example return value (may be object, array, etc.)"},
                        "annotations": {"type": "object"},
                        "tags": {"type": "array"},
                        "version": {"type": "string"},
                    },
                    "description": "Full tool schema",
                },
            },
        },
    )(tool_info_fn)

    async def search_resources_fn(
        query: Annotated[str, "Natural language query to search for resources"],
        format: Annotated[str, "Output format: markdown (default), json, or raw"] = "markdown",
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        return await _search_resources_impl(query, format, ctx)

    mcp.tool(
        name="search_resources",
        description="Search MCP resources by natural language query. "
        "Uses BM25 ranking to find the most relevant resources matching your query. "
        "Searches across resource URI, name, description, and tags. "
        "Use this when you know what kind of information you want but not the "
        "exact resource URI. For an exhaustive listing, use list_resources instead.",
        tags={"synthetic"},
        annotations=ToolAnnotations(openWorldHint=False),
        output_schema=_SEARCH_RESOURCES_OUTPUT_SCHEMA,
    )(search_resources_fn)


__all__ = [
    "TolerantBM25Search",
    "TolerantSearchTransform",
    "_call_tool_impl",
    "_compact_search_serializer",
    "_extract_resource_text",
    "_extract_searchable_text_enhanced",
    "_search_and_format",
    "_search_resources_impl",
    "_search_tools_impl",
    "_tool_info_impl",
    "register_synthetic_tools",
]
