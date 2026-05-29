"""Search transform and synthetic tools for tool discovery.

Extracted from tool_annotator.py to isolate search-related concerns.
"""

import json
from collections.abc import Sequence
from typing import Annotated, Any

from fastmcp.server.context import Context
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool, ToolResult

from gitea_mcp_server.server_setup.bm25_search import TolerantBM25Search
from gitea_mcp_server.server_setup.tool_errors import (
    _raise_value_error,
    _raise_value_error_from,
)
from gitea_mcp_server.server_setup.tool_examples import _serialize_tool_schema


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
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            assert ctx is not None
            hidden = await transform._get_visible_tools(ctx)
            results = await transform._search(hidden, query)
            rendered = await transform._render_results(results)
            return ToolResult(structured_content={"result": rendered})

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
            return await ctx.fastmcp.call_tool(name, arguments)

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
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            assert ctx is not None
            tools = await transform.get_tool_catalog(ctx)
            for tool in tools:
                if tool.name == name:
                    return ToolResult(structured_content={"result": _serialize_tool_schema(tool)})
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
    "TolerantSearchTransform",
    "_compact_search_serializer",
]
