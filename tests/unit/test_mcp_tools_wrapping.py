"""Unit tests for FunctionTool result wrapping (x-fastmcp-wrap-result)."""

from copy import deepcopy

import pytest
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import CallToolResult


class TestFunctionToolResultWrapping:
    """Test that FunctionTool.convert_result() wraps when x-fastmcp-wrap-result is set.

    Uses schemas that mirror the actual output schemas registered on real
    synthetic tools in the codebase:

    - ``_SCHEMA_LIST_RESOURCES`` mirrors ``_LIST_RESOURCES_OUTPUT_SCHEMA``
      from ``mcp_tools.py`` — the ``result`` property is an array of
      resource-entry objects.
    - ``_SCHEMA_API_TOOL`` mirrors the generic API tool pattern — the
      ``result`` property is an object with typed fields (used by
      ``repo_get``, ``issue_get_issue``, ``user_get_current``, and
      every other auto-generated API tool).

    Both schemas carry ``x-fastmcp-wrap-result: True``, which FastMCP
    dynamically injects at runtime for every tool with a non-None
    output schema (via ``_customize_metadata`` in ``mcp_builder.py``
    for API tools, and auto-detection for synthetic tools).
    """

    _SCHEMA_LIST_RESOURCES: dict = {
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
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "required_scope": {"oneOf": [{"type": "string"}, {"type": "null"}]},
                    },
                },
                "description": "List of resource metadata entries",
            },
        },
        "x-fastmcp-wrap-result": True,
    }

    _SCHEMA_API_TOOL: dict = {
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "description": "API resource object",
            },
        },
        "x-fastmcp-wrap-result": True,
    }

    # ------------------------------------------------------------------
    # Wrapping active: x-fastmcp-wrap-result = True
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_convert_result_wraps_dict_with_x_fastmcp(self) -> None:
        """convert_result should wrap a dict return value in {'result': ...}.

        Mirrors the pattern used by API tools (e.g. repo_get, issue_get_issue,
        user_get_current) whose output schema has ``result: object``.
        """

        tool = Tool(
            name="test_api",
            description="Test API tool",
            parameters={"properties": {}},
            output_schema=deepcopy(self._SCHEMA_API_TOOL),
        )

        raw = {"id": 1, "name": "example-repo", "description": "A sample repository"}
        result = tool.convert_result(raw)

        assert isinstance(result, ToolResult)
        assert result.structured_content == {
            "result": {"id": 1, "name": "example-repo", "description": "A sample repository"},
        }

    @pytest.mark.asyncio
    async def test_convert_result_wraps_array_with_x_fastmcp(self) -> None:
        """convert_result should wrap an array return value in {'result': ...}.

        Mirrors the pattern used by list_resources, search_tools, search_docs,
        and search_resources whose output schema has ``result: array``.
        """

        tool = Tool(
            name="test_list",
            description="Test list tool",
            parameters={"properties": {}},
            output_schema=deepcopy(self._SCHEMA_LIST_RESOURCES),
        )

        raw = [
            {"uri": "gitea://version", "name": "Version", "description": "Server version",
             "mimeType": "text/plain", "type": "resource", "tags": ["wrapper", "server"]},
        ]
        result = tool.convert_result(raw)

        assert isinstance(result, ToolResult)
        assert result.structured_content == {
            "result": [
                {"uri": "gitea://version", "name": "Version", "description": "Server version",
                 "mimeType": "text/plain", "type": "resource", "tags": ["wrapper", "server"]},
            ],
        }

    @pytest.mark.asyncio
    async def test_convert_result_sets_meta_when_wrapping(self) -> None:
        """When wrapping, meta should be set to bypass MCP SDK validation."""

        tool = Tool(
            name="test_meta",
            description="Test meta",
            parameters={"properties": {}},
            output_schema=deepcopy(self._SCHEMA_API_TOOL),
        )

        raw = {"id": 1, "name": "test", "description": "A test item"}
        result = tool.convert_result(raw)

        assert result.meta == {"fastmcp": {"wrap_result": True}}

    # ------------------------------------------------------------------
    # Wrapping inactive: no x-fastmcp-wrap-result flag
    # ------------------------------------------------------------------

    def test_convert_result_no_wrap_without_flag(self) -> None:
        """Without x-fastmcp-wrap-result, structured_content should not be wrapped."""

        schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                    "description": "API resource object",
                },
            },
        }

        tool = Tool(
            name="test_nowrap",
            description="Test no wrap",
            parameters={"properties": {}},
            output_schema=schema,
        )

        raw = {"id": 1, "name": "test"}
        result = tool.convert_result(raw)

        assert result.structured_content == {"id": 1, "name": "test"}
        assert result.meta is None

    # ------------------------------------------------------------------
    # MCP transport: meta bypasses SDK validation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_to_mcp_result_returns_calltoolresult_when_meta_set(self) -> None:
        """When meta is set (wrapping active), to_mcp_result should return
        CallToolResult directly to bypass MCP SDK output validation."""

        tool = Tool(
            name="test_calltool",
            description="Test CallToolResult",
            parameters={"properties": {}},
            output_schema=deepcopy(self._SCHEMA_API_TOOL),
        )

        raw = {"id": 1, "name": "test", "description": "A test item"}
        result = tool.convert_result(raw)
        mcp_result = result.to_mcp_result()

        assert isinstance(mcp_result, CallToolResult)
        assert mcp_result.structuredContent == {
            "result": {"id": 1, "name": "test", "description": "A test item"},
        }
