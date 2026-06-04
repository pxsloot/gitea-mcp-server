"""Unit tests for FunctionTool result wrapping (x-fastmcp-wrap-result)."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import CallToolResult

class TestFunctionToolResultWrapping:
    """Test that FunctionTool.convert_result() wraps when x-fastmcp-wrap-result is set.

    This mirrors the exact pattern used by ``list_resources`` and
    ``read_resource`` (``@mcp.tool(output_schema={..., "x-fastmcp-wrap-result": True})``).
    """

    MOCK_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {
                    "resources": {"type": "array"},
                    "count": {"type": "integer"},
                },
            },
        },
        "x-fastmcp-wrap-result": True,
    }

    @pytest.mark.asyncio
    async def test_convert_result_wraps_dict_with_x_fastmcp(self):
        """convert_result should wrap return value in {'result': ...}."""
        from fastmcp.tools.base import Tool
        from fastmcp.tools.function_parsing import ParsedFunction

        tool = Tool(
            name="test_list",
            description="Test list",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = {"resources": [{"uri": "gitea://test"}], "count": 1}
        result = tool.convert_result(raw)

        assert isinstance(result, ToolResult)
        assert result.structured_content == {"result": {"resources": [{"uri": "gitea://test"}], "count": 1}}

    @pytest.mark.asyncio
    async def test_convert_result_wraps_array_with_x_fastmcp(self):
        """convert_result should wrap arrays too."""
        from fastmcp.tools.base import Tool

        tool = Tool(
            name="test_array",
            description="Test array",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = [{"id": 1}, {"id": 2}]
        result = tool.convert_result(raw)

        assert isinstance(result, ToolResult)
        assert result.structured_content == {"result": [{"id": 1}, {"id": 2}]}

    @pytest.mark.asyncio
    async def test_convert_result_sets_meta_when_wrapping(self):
        """When wrapping, meta should be set to bypass MCP SDK validation."""
        from fastmcp.tools.base import Tool

        tool = Tool(
            name="test_meta",
            description="Test meta",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = {"resources": [], "count": 0}
        result = tool.convert_result(raw)

        assert result.meta == {"fastmcp": {"wrap_result": True}}

    def test_convert_result_no_wrap_without_flag(self):
        """Without x-fastmcp-wrap-result, structured_content should not be wrapped."""
        from fastmcp.tools.base import Tool

        schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {
                        "resources": {"type": "array"},
                        "count": {"type": "integer"},
                    },
                },
            },
        }

        tool = Tool(
            name="test_nowrap",
            description="Test no wrap",
            parameters={"properties": {}},
            output_schema=schema,
        )

        raw = {"resources": [], "count": 0}
        result = tool.convert_result(raw)

        assert result.structured_content == {"resources": [], "count": 0}
        assert result.meta is None

    @pytest.mark.asyncio
    async def test_to_mcp_result_returns_calltoolresult_when_meta_set(self):
        """When meta is set (wrapping active), to_mcp_result should return
        CallToolResult directly to bypass MCP SDK output validation."""
        from fastmcp.tools.base import Tool

        tool = Tool(
            name="test_calltool",
            description="Test CallToolResult",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = {"resources": [], "count": 0}
        result = tool.convert_result(raw)
        mcp_result = result.to_mcp_result()

        from mcp.types import CallToolResult
        assert isinstance(mcp_result, CallToolResult)
        assert mcp_result.structuredContent == {"result": {"resources": [], "count": 0}}
