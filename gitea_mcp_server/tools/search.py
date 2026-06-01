"""Search transform and synthetic tools for tool discovery.

BM25 search engine lives in gitea_mcp_server/search.py (flat infra layer).
This module contains Tool-specific search wrappers and the TolerantSearchTransform.
"""

import json
from collections.abc import Sequence
from typing import Annotated, Any

from fastmcp.server.context import Context
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.constants import SEARCH_CATEGORY_ALIASES, SEARCH_NAME_BOOST
from gitea_mcp_server.format import _format_as_markdown
from gitea_mcp_server.search import BM25SearchEngine
from gitea_mcp_server.tools.errors import (
    _raise_value_error,
    _raise_value_error_from,
)
from gitea_mcp_server.tools.examples import _serialize_tool_schema


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

    if content is not None:
        return ToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content=result.structured_content,
            meta=result.meta,
        )

    return result


# ============================================================================
# Tool-specific text extraction (uses flat BM25 engine from search.py)
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
            "annotations": annotations,
        }
        result.append(item)
    return result


class TolerantSearchTransform(BM25SearchTransform):
    """Search transform with tolerant tool discovery, call_tool proxy, and tool_info.

    Extends BM25SearchTransform with:
    - Tolerant argument handling (JSON string parsing)
    - Compact search results with name, description, tags and annotations
    - tool_info synthetic tool for retrieving full tool schemas
    - Enhanced BM25 search with alias expansion and 2-char token support
    """

    def __init__(self, **kwargs: Any) -> None:
        if "search_result_serializer" not in kwargs:
            kwargs["search_result_serializer"] = _compact_search_serializer
        self._tool_info_name = kwargs.pop("tool_info_name", "tool_info")
        super().__init__(**kwargs)
        self._searcher = TolerantBM25Search()

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        pinned = [t for t in tools if t.name in self._always_visible]
        return [*pinned, self._make_search_tool(), self._make_call_tool(), self._make_tool_info_tool()]

    async def get_tool(
        self, name: str, call_next: Any, *, version: Any = None
    ) -> Tool | None:
        if name == self._tool_info_name:
            return self._make_tool_info_tool()
        return await super().get_tool(name, call_next, version=version)

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        return self._searcher.search(tools, query, self._max_results)

    def _make_search_tool(self) -> Tool:
        transform = self
        _VALID_CATEGORIES = ["admin", "organization", "user", "issue", "pull_request", "repository", "misc"]

        async def search_tools(
            query: Annotated[str, "Natural language query to search for tools"],
            category: Annotated[str | None, f"Optional category to filter by: {', '.join(_VALID_CATEGORIES)}"] = None,
            format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
            ctx: Context | None = None,
        ) -> ToolResult:
            if ctx is None:
                msg = "Context is required"
                _raise_value_error(msg)
            hidden = await transform._get_visible_tools(ctx)
            if category is not None:
                category_lower = category.lower()
                if category_lower not in _VALID_CATEGORIES:
                    msg = f"Invalid category '{category}'. Valid categories: {', '.join(_VALID_CATEGORIES)}"
                    _raise_value_error(msg)
                hidden = [t for t in hidden if t.tags and category_lower in t.tags]
            results = await transform._search(hidden, query)
            rendered = await transform._render_results(results)
            return _format_result(ToolResult(structured_content={"result": rendered}), format)

        return Tool.from_function(
            fn=search_tools,
            name=self._search_tool_name,
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
        )

    def _make_call_tool(self) -> Tool:
        transform = self

        async def call_tool(
            name: Annotated[str, "The name of the tool to call"],
            arguments: Annotated[Any, "Arguments to pass to the tool (dict or JSON string)"] = None,
            format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
            ctx: Context | None = None,
        ) -> ToolResult:
            if name in {transform._call_tool_name, transform._search_tool_name, transform._tool_info_name}:
                msg = f"'{name}' is a synthetic search tool and cannot be called via the call_tool proxy"
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
            if ctx is None:
                msg = "Context is required"
                _raise_value_error(msg)
            result = await ctx.fastmcp.call_tool(name, arguments)
            output_schema = None
            if format == "markdown":
                tool_obj = await ctx.fastmcp.get_tool(name)
                if tool_obj is not None:
                    output_schema = tool_obj.output_schema
            return _format_result(result, format, output_schema)

        return Tool.from_function(
            fn=call_tool,
            name=self._call_tool_name,
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "description": "Result of the tool call, wrapped in result for consistency",
                    },
                },
            },
        )

    def _make_tool_info_tool(self) -> Tool:
        transform = self

        async def tool_info(  # noqa: RET503 — _raise_value_error always raises
            name: Annotated[str, "The exact name of the tool to inspect"],
            format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
            ctx: Context | None = None,
        ) -> ToolResult:
            if ctx is None:
                msg = "Context is required"
                _raise_value_error(msg)
            tools = await transform.get_tool_catalog(ctx)
            for tool in tools:
                if tool.name == name:
                    return _format_result(ToolResult(structured_content={"result": _serialize_tool_schema(tool)}), format)
            msg = f"Tool '{name}' not found"
            _raise_value_error(msg)

        return Tool.from_function(
            fn=tool_info,
            name=self._tool_info_name,
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
        )


__all__ = [
    "TolerantBM25Search",
    "TolerantSearchTransform",
    "_compact_search_serializer",
    "_extract_searchable_text_enhanced",
]
