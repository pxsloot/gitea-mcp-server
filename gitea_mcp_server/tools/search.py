"""Search transform and synthetic tools for tool discovery.

Merged from bm25_search.py (BM25 search engine) and tool_search.py (transform + synthetic tools).
"""

import json
import re
from collections.abc import Sequence
from typing import Annotated, Any

from fastmcp.server.context import Context
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.search.bm25 import _BM25Index as _BaseBM25Index
from fastmcp.server.transforms.search.bm25 import _catalog_hash
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.constants import (
    SEARCH_CATEGORY_ALIASES,
    SEARCH_MIN_TOKEN_LENGTH,
    SEARCH_NAME_BOOST,
)
from gitea_mcp_server.resources.format import _format_as_markdown
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
# BM25 Search Engine (from bm25_search.py)
# ============================================================================


def _tokenize_len2(text: str) -> list[str]:
    """Tokenize with support for 2-character tokens like 'pr'."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= SEARCH_MIN_TOKEN_LENGTH]


def _expand_word_aliases(text: str) -> str:
    """Expand common abbreviations and fragments for better search matching."""
    alias_expansions = [
        ("repo", "repo repository repos"),
        ("pr", "pr pull request"),
        ("current", "current authenticated"),
        ("user", "user users account"),
    ]
    text_lower = text.lower()
    parts = [text]
    for word, expansion in alias_expansions:
        if word in text_lower:
            parts.append(expansion)
    return " ".join(parts)


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


class _BM25IndexLen2(_BaseBM25Index):
    """BM25 index that supports 2-character tokens."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        super().__init__(k1, b)

    def build(self, documents: list[str]) -> None:
        self._doc_tokens = [_tokenize_len2(doc) for doc in documents]
        self._doc_lengths = [len(tokens) for tokens in self._doc_tokens]
        self._n = len(documents)
        self._avg_dl = sum(self._doc_lengths) / self._n if self._n else 0.0

        self._df: dict[str, int] = {}
        self._tf = []
        for tokens in self._doc_tokens:
            tf: dict[str, int] = {}
            seen: set[str] = set()
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
                if token not in seen:
                    self._df[token] = self._df.get(token, 0) + 1
                    seen.add(token)
            self._tf.append(tf)


class TolerantBM25Search:
    """BM25 search for tools with tolerant tokenization and alias expansion.

    Pure search logic with no transform concerns. Builds and queries
    a BM25 index from tool metadata.
    """

    def __init__(self) -> None:
        self._last_hash: str = ""
        self._index: _BM25IndexLen2 = _BM25IndexLen2()
        self._indexed_tools: Sequence[Tool] = ()

    def search(self, tools: Sequence[Tool], query: str, max_results: int = 10) -> Sequence[Tool]:
        """Search tools by BM25 relevance ranking."""
        current_hash = _catalog_hash(tools)
        if current_hash != self._last_hash:
            documents = [_extract_searchable_text_enhanced(t) for t in tools]
            new_index = _BM25IndexLen2(self._index.k1, self._index.b)
            new_index.build(documents)
            self._index, self._indexed_tools, self._last_hash = (
                new_index,
                tools,
                current_hash,
            )

        expanded_query = _expand_word_aliases(query)
        indices = self._index.query(expanded_query, max_results)
        return [self._indexed_tools[i] for i in indices]


# ============================================================================
# Search Transform + Synthetic Tools (from tool_search.py)
# ============================================================================


def _compact_search_serializer(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        item: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description or "",
        }
        result.append(item)
    return result


class TolerantSearchTransform(BM25SearchTransform):
    """Search transform with tolerant tool discovery, call_tool proxy, and tool_info.

    Extends BM25SearchTransform with:
    - Tolerant argument handling (JSON string parsing)
    - Compact search results (name + description only)
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

        async def search_tools(
            query: Annotated[str, "Natural language query to search for tools"],
            format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            assert ctx is not None
            hidden = await transform._get_visible_tools(ctx)
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
                            },
                        },
                        "description": "Matching tool definitions (name + description only)",
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
            assert ctx is not None
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

        async def tool_info(
            name: Annotated[str, "The exact name of the tool to inspect"],
            format: Annotated[str, "Output format: markdown (default, human-readable), raw (raw API response), or json (structured data)"] = "markdown",
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            assert ctx is not None
            tools = await transform.get_tool_catalog(ctx)
            for tool in tools:
                if tool.name == name:
                    return _format_result(ToolResult(structured_content={"result": _serialize_tool_schema(tool)}), format)
            msg = f"Tool '{name}' not found"
            _raise_value_error(msg)
            return None  # type: ignore[unreachable]

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
    "_BM25IndexLen2",
    "_compact_search_serializer",
    "_expand_word_aliases",
    "_extract_searchable_text_enhanced",
    "_tokenize_len2",
]
