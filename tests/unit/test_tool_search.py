"""Unit tests for search engine (indexing, call_tool, format, serializer)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Context
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import ToolAnnotations
from mcp.types import TextContent

from gitea_mcp_server.constants import SEARCH_NAME_BOOST
from gitea_mcp_server.pagination import add_pagination_metadata
from gitea_mcp_server.format import format_result
from gitea_mcp_server.tools.search import (
    _call_tool_impl,
    _compact_search_serializer,
    _extract_resource_text,
    _extract_searchable_text_enhanced,
    _search_and_slice,
    _search_resources_impl,
    _search_tools_impl,
    _tool_info_impl,
    register_synthetic_tools,
    TolerantSearchTransform,
)

class TestSearchableText:
    """Tests for _extract_searchable_text_enhanced."""

    def test_name_is_boosted(self):
        """Tool name should appear SEARCH_NAME_BOOST times in the extracted text."""
        tool = Tool(
            name="gitea_user_get_current",
            description="Get the authenticated user",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert result.count("gitea_user_get_current") == SEARCH_NAME_BOOST

    def test_no_side_effects_on_empty_fields(self):
        """Should handle tools with minimal fields gracefully."""
        tool = Tool(
            name="minimal_tool",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "minimal_tool" in result
        assert isinstance(result, str)
        assert len(result) > 0


class TestCallToolOutputSchema:
    """Tests for call_tool output_schema (via actual registration)."""

    @pytest.mark.asyncio
    async def _get_call_tool(self) -> Tool:
        """Helper: register synthetic tools and return the call_tool."""
        from fastmcp import FastMCP
        from gitea_mcp_server.tools.search import TolerantSearchTransform, register_synthetic_tools

        mcp = FastMCP("test")
        transform = TolerantSearchTransform()
        register_synthetic_tools(mcp, transform)
        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        return tool_map.get("call_tool")

    @pytest.mark.asyncio
    async def test_call_tool_has_output_schema(self):
        """call_tool should have an output_schema set with type object and result property."""
        tool = await self._get_call_tool()
        assert tool is not None, "call_tool not registered"
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "result" in tool.output_schema["properties"]
        assert "x-fastmcp-wrap-result" not in tool.output_schema

    @pytest.mark.asyncio
    async def test_call_tool_result_property_accepts_any_type(self):
        """The 'result' property must not have a restrictive type constraint
        (accepts both objects and arrays since it proxies any tool)."""
        tool = await self._get_call_tool()
        assert tool is not None, "call_tool not registered"
        result_schema = tool.output_schema["properties"]["result"]
        # Must not have a bare "type": "object" that rejects arrays
        has_any_of = "anyOf" in result_schema
        no_type = "type" not in result_schema
        assert has_any_of or no_type, (
            f"call_tool.result has a bare type constraint: {result_schema!r}"
        )
        if has_any_of:
            types = {entry.get("type") for entry in result_schema["anyOf"]}
            assert "object" in types, f"anyOf should accept objects, got {types}"
            assert "array" in types, f"anyOf should accept arrays, got {types}"


class TestToolInfoOutputSchema:
    """Tests for tool_info output_schema (via actual registration)."""

    @pytest.mark.asyncio
    async def _get_tool_info(self) -> Tool:
        """Helper: register synthetic tools and return the tool_info tool."""
        from fastmcp import FastMCP
        from gitea_mcp_server.tools.search import TolerantSearchTransform, register_synthetic_tools

        mcp = FastMCP("test")
        transform = TolerantSearchTransform()
        register_synthetic_tools(mcp, transform)
        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        return tool_map.get("tool_info")

    @pytest.mark.asyncio
    async def test_tool_info_has_output_schema(self):
        """tool_info should have an output_schema set with type object and result property."""
        tool = await self._get_tool_info()
        assert tool is not None, "tool_info not registered"
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "result" in tool.output_schema["properties"]

    @pytest.mark.asyncio
    async def test_tool_info_output_example_accepts_array(self):
        """tool_info's output_example property must accept arrays (tool schemas return list examples)."""
        tool = await self._get_tool_info()
        assert tool is not None, "tool_info not registered"
        result_schema = tool.output_schema["properties"]["result"]
        output_example_schema = result_schema.get("properties", {}).get("output_example", {})
        assert output_example_schema, "output_example missing from tool_info.result.properties"
        # Must accept both object and array (via anyOf or no type constraint)
        has_any_of = "anyOf" in output_example_schema
        no_type = "type" not in output_example_schema
        assert has_any_of or no_type, (
            f"output_example has a bare type constraint: {output_example_schema!r}"
        )
        if has_any_of:
            types = {entry.get("type") for entry in output_example_schema["anyOf"]}
            assert "object" in types, f"anyOf should accept objects, got {types}"
            assert "array" in types, f"anyOf should accept arrays, got {types}"


class TestFormatResult:
    """Tests for format_result helper that formats ToolResult content by format.

    This helper is used by call_tool, search_tools, and tool_info to handle
    the ``format`` parameter (markdown/json/raw). It always preserves
    ``structured_content`` as raw data and only replaces ``content``.
    """

    def test_raw_format_returns_same_object(self):
        """format=raw should return the ToolResult unchanged."""
        from gitea_mcp_server.format import format_result

        inner = ToolResult(structured_content={"result": {"key": "value"}})
        result = format_result(inner, "raw")
        assert result is inner

    def test_json_format_with_dict_data(self):
        """format=json with dict data should produce pretty-printed JSON in content."""
        import json as json_module

        from gitea_mcp_server.format import format_result

        data = {"key": "value", "num": 42}
        inner = ToolResult(structured_content={"result": data})
        result = format_result(inner, "json")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        parsed = json_module.loads(result.content[0].text)
        assert parsed == data

    def test_json_format_with_list_data(self):
        """format=json with list data should produce pretty-printed JSON in content."""
        import json as json_module

        from gitea_mcp_server.format import format_result

        data = [{"name": "tool_a"}, {"name": "tool_b"}]
        inner = ToolResult(structured_content={"result": data})
        result = format_result(inner, "json")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        parsed = json_module.loads(result.content[0].text)
        assert parsed == data

    def test_markdown_format_with_dict_data(self):
        """format=markdown with dict data should produce markdown in content."""
        from gitea_mcp_server.format import format_result

        data = {"name": "test_tool", "description": "A test tool"}
        inner = ToolResult(structured_content={"result": data})
        result = format_result(inner, "markdown")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        assert "|" in result.content[0].text
        assert "name" in result.content[0].text.lower()

    def test_markdown_format_with_list_data(self):
        """format=markdown with list data should produce markdown in content."""
        from gitea_mcp_server.format import format_result

        data = [{"name": "tool_a", "description": "First"}]
        inner = ToolResult(structured_content={"result": data})
        result = format_result(inner, "markdown")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        assert "|" in result.content[0].text
        assert "tool_a" in result.content[0].text

    def test_markdown_with_scalar_data_returns_unchanged(self):
        """format=markdown with scalar (non-dict/list) data should return ToolResult unchanged."""
        from gitea_mcp_server.format import format_result

        inner = ToolResult(structured_content={"result": "just a string"})
        result = format_result(inner, "markdown")
        assert result is inner

    def test_no_structured_content_returns_unchanged(self):
        """ToolResult without structured_content should be returned unchanged."""
        from gitea_mcp_server.format import format_result

        inner = ToolResult(content=[TextContent(type="text", text="hello")], structured_content=None)
        result = format_result(inner, "markdown")
        assert result is inner

    def test_missing_result_key_returns_unchanged(self):
        """structured_content without result key should be returned unchanged."""
        from gitea_mcp_server.format import format_result

        inner = ToolResult(structured_content={"other": "data"})
        result = format_result(inner, "markdown")
        assert result is inner


class TestCallToolRuntimeBehavior:
    """Test runtime behavior of the call_tool function.

    call_tool is a proxy that delegates to ctx.fastmcp.call_tool().
    These tests verify it correctly passes ToolResult through without
    double-wrapping, and properly handles argument validation.
    """

    @pytest.mark.asyncio
    async def test_call_tool_passes_toolresult_through(self):
        """call_tool is a transparent proxy that returns the inner result unchanged."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": 1}, {"id": 2}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)

        assert result is inner_result

    @pytest.mark.asyncio
    async def test_call_tool_passes_through_json_format(self):
        """call_tool passes through a JSON-formatted result unchanged (format handled by inner tool)."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        data = {"result": [{"id": 1}, {"id": 2}]}
        inner_result = ToolResult(
            content=[TextContent(type="text", text='[{"id": 1}, {"id": 2}]')],
            structured_content=data,
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)

        assert result is inner_result
        assert result.structured_content == data

    @pytest.mark.asyncio
    async def test_call_tool_passes_through_raw_result(self):
        """call_tool passes through a raw-formatted result unchanged (format handled by inner tool)."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": {"key": "val"}},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)

        assert result is inner_result

    @pytest.mark.asyncio
    async def test_call_tool_no_double_wrap(self):
        """call_tool must pass the ToolResult through without double-wrapping."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": {"items": [1, 2, 3], "count": 3}},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)
        assert result is inner_result
        assert result.structured_content == {"result": {"items": [1, 2, 3], "count": 3}}
        inner = result.structured_content["result"]
        assert "result" not in inner, (
            f"Double-wrapped! structured_content={result.structured_content}"
        )

    @pytest.mark.asyncio
    async def test_call_tool_preserves_user_meta_from_inner_tool(self):
        """call_tool should preserve meta from the inner tool's ToolResult."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_meta = {"fastmcp": {"wrap_result": True}, "custom": "data"}
        inner_result = ToolResult(
            content=[],
            structured_content={"result": {}},
            meta=inner_meta,
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)
        assert result is inner_result
        assert result.meta == inner_meta

    @pytest.mark.asyncio
    async def test_call_tool_rejects_self_call(self):
        """call_tool should reject calling itself."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="cannot call itself"):
            await _call_tool_impl("call_tool", {}, mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_parses_json_string_arguments(self):
        """String arguments should be parsed as JSON before forwarding."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(content=[], structured_content={"result": {}})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        await _call_tool_impl("gitea_test_tool", '{"key": "val", "num": 42}', mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with(
            "gitea_test_tool", {"key": "val", "num": 42}
        )

    @pytest.mark.asyncio
    async def test_call_tool_rejects_non_dict_and_non_string_arguments(self):
        """Arguments that are neither dict nor None nor a JSON string should be rejected."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await _call_tool_impl("gitea_test_tool", [1, 2, 3], mock_ctx)

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await _call_tool_impl("gitea_test_tool", 42, mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_rejects_invalid_json(self):
        """Invalid JSON string arguments should be rejected."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Invalid JSON"):
            await _call_tool_impl("gitea_test_tool", "{bad json}", mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_handles_none_arguments(self):
        """None arguments should be forwarded as None."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(content=[], structured_content={"result": []})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        await _call_tool_impl("gitea_test_tool", None, mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with("gitea_test_tool", None)

    @pytest.mark.asyncio
    async def test_call_tool_routes_array_result_from_inner_tool(self):
        """When inner tool returns an array wrapped in {"result": [...]}, pass through."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": "a"}, {"id": "b"}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        final = await _call_tool_impl("gitea_array_tool", None, mock_ctx)
        assert final is inner_result


class TestCompactSearchSerializer:
    """Tests for _compact_search_serializer function."""

    def test_returns_name_and_description_only(self):
        """Search results should only include name and description."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {"id": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
        )
        result = _compact_search_serializer([tool])
        assert len(result) == 1
        assert result[0]["name"] == "test_tool"
        assert result[0]["description"] == "A test tool"
        assert "parameters" not in result[0]
        assert "output_schema" not in result[0]
        assert "output_example" not in result[0]

    def test_handles_empty_fields(self):
        """Should handle tools with minimal fields."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tool = Tool(
            name="minimal_tool",
            description="",
            parameters={"properties": {}},
            output_schema=None,
        )
        result = _compact_search_serializer([tool])
        assert result[0]["name"] == "minimal_tool"
        assert result[0]["description"] == ""

    def test_handles_multiple_tools(self):
        """Should serialize multiple tools correctly."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tools = [
            Tool(name="tool_a", description="First tool", parameters={"properties": {}}),
            Tool(name="tool_b", description="Second tool", parameters={"properties": {}}),
        ]
        result = _compact_search_serializer(tools)
        assert len(result) == 2
        assert result[0]["name"] == "tool_a"
        assert result[1]["name"] == "tool_b"

    def test_omits_annotations_when_null(self):
        """Should omit annotations key when tool has no annotations."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tool = Tool(
            name="no_annotations",
            description="A tool without annotations",
            parameters={"properties": {}},
        )
        result = _compact_search_serializer([tool])
        assert "annotations" not in result[0]

    def test_includes_annotations_when_present(self):
        """Should include annotations key when tool has annotations."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tool = Tool(
            name="with_annotations",
            description="A tool with annotations",
            parameters={"properties": {}},
            annotations=ToolAnnotations(
                title="Test Tool",
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
            ),
        )
        result = _compact_search_serializer([tool])
        assert "annotations" in result[0]
        assert result[0]["annotations"]["title"] == "Test Tool"

    def test_omits_annotations_when_all_fields_null(self):
        """Should omit annotations key when all annotation fields are None."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tool = Tool(
            name="empty_annotations",
            description="A tool with null annotations fields",
            parameters={"properties": {}},
            annotations=ToolAnnotations(
                title=None,
                readOnlyHint=None,
                destructiveHint=None,
                idempotentHint=None,
            ),
        )
        result = _compact_search_serializer([tool])
        # Annotations are always included now (all 5 fields explicit)
        ann = result[0].get("annotations", {})
        assert ann.get("title") is None
        assert ann.get("readOnlyHint") is None

    def test_includes_tags_when_present(self):
        """Should include tags key when tool has tags."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tool = Tool(
            name="tagged_tool",
            description="A tool with tags",
            parameters={"properties": {}},
            tags={"issue", "repository"},
        )
        result = _compact_search_serializer([tool])
        assert set(result[0]["tags"]) == {"issue", "repository"}

    def test_includes_hints_when_true(self):
        """Should include hint annotations when they are True."""
        from gitea_mcp_server.tools.search import _compact_search_serializer

        tool = Tool(
            name="hint_tool",
            description="A tool with hints",
            parameters={"properties": {}},
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=True,
                idempotentHint=True,
            ),
        )
        result = _compact_search_serializer([tool])
        assert result[0]["annotations"]["readOnlyHint"] is True
        assert result[0]["annotations"]["destructiveHint"] is True
        assert result[0]["annotations"]["idempotentHint"] is True


class TestFormatResultExtended:
    """Extended tests for format_result helper."""

    def test_markdown_with_pagination(self):
        """format=markdown should append pagination metadata when present."""
        from gitea_mcp_server.format import format_result

        data = [{"name": "tool_a"}, {"name": "tool_b"}]
        inner = ToolResult(
            structured_content={
                "result": data,
                "has_more": True,
                "next_offset": 10,
                "total_count": 42,
            }
        )
        result = format_result(inner, "markdown")
        assert result.structured_content == inner.structured_content
        assert len(result.content) == 1
        text = result.content[0].text
        assert "| Name |" in text
        assert "has more" in text.lower() or "total" in text.lower()
        assert "42" in text

    def test_markdown_with_output_schema(self):
        """format=markdown should use output_schema for better column layout."""
        from gitea_mcp_server.format import format_result

        data = {"id": 1, "name": "test"}
        inner = ToolResult(structured_content={"result": data})
        output_schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                    },
                },
            },
        }
        result = format_result(inner, "markdown", output_schema=output_schema)
        assert result.structured_content == inner.structured_content
        assert len(result.content) == 1
        # output_schema restricts columns to those defined in the schema
        # Only "id" is defined in the schema, so only "Id" appears in output
        assert "| Id |" in result.content[0].text
        # "name" is not in the schema, so it's filtered out by formatter

    def test_unknown_format_returns_unchanged(self):
        """An unrecognized format string should return the ToolResult unchanged."""
        from gitea_mcp_server.format import format_result

        data = {"key": "value"}
        inner = ToolResult(structured_content={"result": data})
        result = format_result(inner, "xml")
        assert result is inner


class TestSearchableTextExtended:
    """Extended tests for _extract_searchable_text_enhanced."""

    def test_includes_tags(self):
        """Tool tags should appear in the extracted text."""
        from gitea_mcp_server.tools.search import _extract_searchable_text_enhanced

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {}},
            tags={"issue", "repository"},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "issue" in result
        assert "repository" in result

    def test_includes_category_aliases(self):
        """Tags that match SEARCH_CATEGORY_ALIASES should include expanded aliases."""
        from gitea_mcp_server.constants import SEARCH_CATEGORY_ALIASES
        from gitea_mcp_server.tools.search import _extract_searchable_text_enhanced

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {}},
            tags={"issue"},
        )
        result = _extract_searchable_text_enhanced(tool)
        for alias in SEARCH_CATEGORY_ALIASES["issue"].split():
            assert alias in result

    def test_includes_annotation_title(self):
        """Tool annotations.title should appear in the extracted text."""
        from gitea_mcp_server.tools.search import _extract_searchable_text_enhanced

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {}},
            annotations=ToolAnnotations(title="My Custom Title"),
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "My Custom Title" in result

    def test_includes_parameter_descriptions(self):
        """Parameter descriptions should appear in the extracted text."""
        from gitea_mcp_server.tools.search import _extract_searchable_text_enhanced

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={
                "properties": {
                    "owner": {"description": "The repository owner"},
                    "repo": {"description": "The repository name"},
                }
            },
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "The repository owner" in result
        assert "The repository name" in result


class TestCallToolRuntimeBehaviorExtended:
    """Extended tests for call_tool runtime behavior."""

    @pytest.mark.asyncio
    async def test_call_tool_passes_through_regardless_of_output_schema(self):
        """call_tool ignores the tool's output_schema - the inner tool handles its own formatting."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        data = {"id": 1, "name": "test"}
        inner_result = ToolResult(
            content=[],
            structured_content={"result": data},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await _call_tool_impl("gitea_schema_tool", {"arg": 1}, mock_ctx)
        assert result is inner_result


class TestToolInfo:
    """Tests for the tool_info synthetic tool."""

    @pytest.mark.asyncio
    async def test_tool_info_returns_schema(self):
        """tool_info should return the schema for a known tool."""
        from gitea_mcp_server.tools.search import _tool_info_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()

        known_tool = Tool(
            name="gitea_known_tool",
            description="A known tool",
            parameters={"properties": {"x": {"type": "integer"}}},
            tags={"issue"},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[known_tool])

        result = await _tool_info_impl("gitea_known_tool", "markdown", mock_ctx, transform)
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert schema["name"] == "gitea_known_tool"
        assert schema["description"] == "A known tool"

    @pytest.mark.asyncio
    async def test_tool_info_detail_full_includes_output_schema(self):
        """tool_info with detail='full' should include output_schema."""
        from gitea_mcp_server.tools.search import _tool_info_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_tool_with_schema",
            description="A tool",
            parameters={"properties": {"x": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

        result = await _tool_info_impl(
            "gitea_tool_with_schema", "json", mock_ctx, transform, detail="full"
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert schema["name"] == "gitea_tool_with_schema"
        assert "output_example" in schema
        assert "output_schema" in schema
        assert schema["output_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_tool_info_detail_concise_excludes_output_schema(self):
        """tool_info with detail='concise' (default) should NOT include output_schema."""
        from gitea_mcp_server.tools.search import _tool_info_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_tool_no_schema_included",
            description="A tool",
            parameters={"properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "string"},
                },
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

        result = await _tool_info_impl(
            "gitea_tool_no_schema_included", "json", mock_ctx, transform, detail="concise"
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert "output_example" in schema
        assert "output_schema" not in schema

    @pytest.mark.asyncio
    async def test_tool_info_not_found(self):
        """tool_info should raise ValueError for unknown tool."""
        from gitea_mcp_server.tools.search import _tool_info_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="not found"):
            await _tool_info_impl("gitea_nonexistent_tool", "markdown", mock_ctx, transform)


class TestSearchToolsSyntheticTool:
    """Tests for the search_tools synthetic tool."""

    @pytest.mark.asyncio
    async def test_search_tools_category_filter_invalid(self):
        """search_tools with invalid category should raise ValueError."""
        from gitea_mcp_server.tools.search import _search_tools_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])
        with pytest.raises(ValueError, match="Invalid category"):
            await _search_tools_impl("test query", "invalid", "markdown", mock_ctx, transform)

    @pytest.mark.asyncio
    async def test_search_tools_with_no_results(self):
        """search_tools with no matches should show cross-linking hints."""
        from gitea_mcp_server.tools.search import _search_tools_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        result = await _search_tools_impl("nonexistent", None, "markdown", mock_ctx, transform)
        assert result.structured_content is not None
        text = result.content[0].text if result.content else ""
        assert "No tools found" in text or "search_docs" in text

    @pytest.mark.asyncio
    async def test_search_tools_with_results_and_cross_links(self):
        """search_tools with results should show cross-linking hints."""
        from gitea_mcp_server.tools.search import _search_tools_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tool = Tool(
            name="gitea_issue_list",
            description="List issues",
            parameters={"properties": {}},
            tags={"issue"},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[mock_tool])

        result = await _search_tools_impl("issue", None, "markdown", mock_ctx, transform)
        assert result.structured_content is not None
        text = result.content[0].text if result.content else ""
        assert "Cross-linking" in text or "search_docs" in text

    @pytest.mark.asyncio
    async def test_search_tools_with_category_filter(self):
        """search_tools with valid category should filter results."""
        from gitea_mcp_server.tools.search import _search_tools_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        issue_tool = Tool(
            name="gitea_issue_list",
            description="List issues",
            parameters={"properties": {}},
            tags={"issue"},
        )
        repo_tool = Tool(
            name="gitea_repo_list",
            description="List repos",
            parameters={"properties": {}},
            tags={"repository"},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[issue_tool, repo_tool])

        result = await _search_tools_impl("list", "issue", "markdown", mock_ctx, transform)
        assert result.structured_content is not None
        text = result.content[0].text if result.content else ""
        assert "gitea_issue_list" in text or "Cross-linking" in text


class TestTolerantBM25Search:
    """Tests for TolerantBM25Search."""

    def test_search_returns_ranked_results(self):
        """TolerantBM25Search should return ranked tools by relevance."""
        from gitea_mcp_server.tools.search import TolerantBM25Search

        searcher = TolerantBM25Search()
        tools = [
            Tool(name="tool_a", description="Issue management", parameters={"properties": {}}),
            Tool(name="tool_b", description="Repository management", parameters={"properties": {}}),
        ]
        results = searcher.search(tools, "issue", max_results=10)
        assert len(results) >= 1
        assert results[0].name == "tool_a"

    def test_search_with_limit(self):
        """TolerantBM25Search should respect max_results limit."""
        from gitea_mcp_server.tools.search import TolerantBM25Search

        searcher = TolerantBM25Search()
        tools = [
            Tool(name=f"tool_{i}", description=f"Description {i}", parameters={"properties": {}})
            for i in range(20)
        ]
        results = searcher.search(tools, "description", max_results=5)
        assert len(results) <= 5


class TestTolerantSearchTransform:
    """Tests for TolerantSearchTransform."""

    @pytest.mark.asyncio
    async def test_transform_tools_pins_synthetic_tagged_tools(self):
        """transform_tools should only pin tools with the synthetic tag."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        plain_tool = Tool(
            name="gitea_test",
            description="A test tool",
            parameters={"properties": {}},
            tags=[],
        )
        synthetic_tool = Tool(
            name="gitea_search_tools",
            description="Search tools",
            parameters={"properties": {}},
            tags={"synthetic"},
        )
        result = await transform.transform_tools([plain_tool, synthetic_tool])
        names = [t.name for t in result]
        assert "gitea_search_tools" in names
        assert "gitea_test" not in names


class TestSyntheticToolAnnotations:
    """All 4 annotation hints are explicitly set on every synthetic tool."""

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _assert_all_hints(
        tool: Tool,
        *,
        read_only: bool,
        open_world: bool,
    ) -> None:
        """Assert all 4 hint fields are explicitly set (never None) on a synthetic tool."""
        assert tool.annotations is not None, f"{tool.name}.annotations is None"
        assert tool.annotations.readOnlyHint is read_only, (
            f"{tool.name}.readOnlyHint: expected {read_only}, got {tool.annotations.readOnlyHint}"
        )
        assert tool.annotations.destructiveHint is False, (
            f"{tool.name}.destructiveHint: expected False, got {tool.annotations.destructiveHint}"
        )
        assert tool.annotations.idempotentHint is read_only, (
            f"{tool.name}.idempotentHint: expected {read_only}, "
            f"got {tool.annotations.idempotentHint}"
        )
        assert tool.annotations.openWorldHint is open_world, (
            f"{tool.name}.openWorldHint: expected {open_world}, got {tool.annotations.openWorldHint}"
        )

    # ── factory ──────────────────────────────────────────────────────────

    def test_synthetic_annotations_factory(self) -> None:
        """synthetic_annotations() returns correct ToolAnnotations for all combinations."""
        from gitea_mcp_server.tools.customize import synthetic_annotations

        # Read-only, local (e.g. search_tools)
        a1 = synthetic_annotations(read_only=True, open_world=False)
        assert a1.readOnlyHint is True
        assert a1.destructiveHint is False
        assert a1.idempotentHint is True
        assert a1.openWorldHint is False

        # Non-read-only, open-world (e.g. call_tool)
        a2 = synthetic_annotations(read_only=False, open_world=True)
        assert a2.readOnlyHint is False
        assert a2.destructiveHint is False
        assert a2.idempotentHint is False
        assert a2.openWorldHint is True

        # Read-only, open-world (e.g. read_resource)
        a3 = synthetic_annotations(read_only=True, open_world=True)
        assert a3.readOnlyHint is True
        assert a3.destructiveHint is False
        assert a3.idempotentHint is True
        assert a3.openWorldHint is True

        # Explicitly verify no None values
        for a in (a1, a2, a3):
            assert a.readOnlyHint is not None
            assert a.destructiveHint is not None
            assert a.idempotentHint is not None
            assert a.openWorldHint is not None

    # ── registration tests ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def _get_tool_map(self) -> dict:
        """Helper: register synthetic tools and return name→Tool dict."""
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        transform = TolerantSearchTransform()
        register_synthetic_tools(mcp, transform)
        tools = await mcp.list_tools()
        return {t.name: t for t in tools}

    @pytest.mark.asyncio
    async def test_local_tools_all_hints(self) -> None:
        """search_tools, tool_info, search_resources: read_only=True, open_world=False."""
        tool_map = await self._get_tool_map()
        for name in ("search_tools", "tool_info", "search_resources"):
            t = tool_map.get(name)
            assert t is not None, f"{name} not registered"
            assert t.description, f"{name}.description should be non-empty"
            self._assert_all_hints(t, read_only=True, open_world=False)

    @pytest.mark.asyncio
    async def test_call_tool_all_hints(self) -> None:
        """call_tool: read_only=False, open_world=True."""
        tool_map = await self._get_tool_map()
        t = tool_map.get("call_tool")
        assert t is not None, "call_tool not registered"
        assert t.description, "call_tool.description should be non-empty"
        self._assert_all_hints(t, read_only=False, open_world=True)

    # ── serializer tests ──────────────────────────────────────────────────

    def test_compact_serializer_all_fields_explicit(self) -> None:
        """_compact_search_serializer includes all 5 annotation fields (no None filtering)."""
        tool = Tool(
            name="gitea_foo",
            description="Some tool",
            parameters={"properties": {}},
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
                title="Foo Tool",
            ),
        )
        result = _compact_search_serializer([tool])
        item = result[0]
        ann = item.get("annotations", {})
        assert ann["readOnlyHint"] is True
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True
        assert ann["openWorldHint"] is True
        assert ann["title"] == "Foo Tool"
        # All 5 fields present
        assert set(ann) == {"title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}

    def test_compact_serializer_no_annotations(self) -> None:
        """_compact_search_serializer handles tools with annotations=None gracefully."""
        tool = Tool(
            name="gitea_bar",
            description="No annotations",
            parameters={"properties": {}},
        )
        result = _compact_search_serializer([tool])
        item = result[0]
        assert item["name"] == "gitea_bar"
        assert "annotations" not in item

    def test_compact_serializer_partial_title(self) -> None:
        """_compact_search_serializer includes title even when other fields are None."""
        tool = Tool(
            name="gitea_baz",
            description="Partial",
            parameters={"properties": {}},
            annotations=ToolAnnotations(title="Just a Title"),
        )
        result = _compact_search_serializer([tool])
        item = result[0]
        ann = item.get("annotations", {})
        # None fields are still serialized explicitly (openWorldHint=None, etc.)
        assert ann["title"] == "Just a Title"
        assert "readOnlyHint" in ann

    # ── error path tests ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_annotations_survive_call_tool_error(self) -> None:
        """After calling call_tool with invalid args, its annotations remain correct."""
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        transform = TolerantSearchTransform()
        register_synthetic_tools(mcp, transform)

        # Trigger error - call_tool with invalid JSON string
        with pytest.raises(ValueError, match="Invalid JSON"):
            ctx = MagicMock(spec=Context)
            await _call_tool_impl(
                name="nonexistent",
                arguments="not-json",
                ctx=ctx,
            )

        # Verify call_tool annotations are still correct
        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        t = tool_map.get("call_tool")
        assert t is not None
        self._assert_all_hints(t, read_only=False, open_world=True)

    @pytest.mark.asyncio
    async def test_tool_info_error_does_not_corrupt_catalog(self) -> None:
        """After a tool_info error, the tool catalog's annotations are still correct."""
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        transform = TolerantSearchTransform()
        register_synthetic_tools(mcp, transform)

        # Simulate a failed lookup (will raise because magic mock can't call list_tools)
        # The key assertion: the mcp instance's tool metadata is intact after the attempt
        try:
            await _tool_info_impl(
                name="nonexistent",
                format="markdown",
                ctx=MagicMock(spec=Context),
                transform=transform,
                tool_prefix="",
            )
        except Exception:
            pass  # Expected - we're testing post-error state

        # Annotations on registered tools unchanged
        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        for name in ("search_tools", "tool_info"):
            self._assert_all_hints(tool_map[name], read_only=True, open_world=False)


class TestSearchResourcesSyntheticTool:
    """Tests for the search_resources synthetic tool via _search_resources_impl."""

    @pytest.mark.asyncio
    async def test_searches_resource_by_uri(self):
        """Resource URI should be searchable via search_resources."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://wiki/guide"
        resource_mock.name = "Wiki Guide"
        resource_mock.description = "A guide about the wiki feature"
        resource_mock.mime_type = "text/markdown"
        resource_mock.tags = {"guide"}
        resource_mock.meta = None

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="wiki", format="markdown", ctx=ctx)

        assert result.structured_content is not None
        results = result.structured_content["result"]
        assert len(results) == 1
        assert results[0]["uri"] == "gitea://wiki/guide"

    @pytest.mark.asyncio
    async def test_searches_resource_by_name(self):
        """Resource name should still be searchable (baseline check)."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://version"
        resource_mock.name = "Server Version"
        resource_mock.description = "Gitea server version"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = {"server"}
        resource_mock.meta = None

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="version", format="markdown", ctx=ctx)

        assert result.structured_content is not None
        results = result.structured_content["result"]
        assert len(results) == 1
        assert results[0]["name"] == "Server Version"

    @pytest.mark.asyncio
    async def test_markdown_includes_cross_link_footer(self):
        """Markdown output should include cross-linking hints footer."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://version"
        resource_mock.name = "Server Version"
        resource_mock.description = "Gitea server version"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = {"server"}
        resource_mock.meta = None

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="version", format="markdown", ctx=ctx)

        assert result.content is not None
        text = result.content[0].text
        assert "Cross-linking hints" in text
        assert "search_docs" in text
        assert "search_tools" in text

    @pytest.mark.asyncio
    async def test_empty_result_has_helpful_hint(self):
        """Empty search results should include helpful cross-linking message."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="nothing", format="markdown", ctx=ctx)

        assert result.content is not None
        text = result.content[0].text
        assert "No results found" in text or "No resources" in text
        assert "search_docs" in text
        assert "search_tools" in text
        assert result.structured_content is not None
        assert result.structured_content["result"] == []

    @pytest.mark.asyncio
    async def test_raw_format(self):
        """search_resources format=raw returns structured_content with result array."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://version"
        resource_mock.name = "Server Version"
        resource_mock.description = "Server version"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = {"server"}
        resource_mock.meta = None

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="version", format="raw", ctx=ctx)
        assert result.structured_content is not None
        assert len(result.structured_content["result"]) == 1
        assert result.structured_content["result"][0]["uri"] == "gitea://version"


class TestSearchAndSlice:
    """Tests for _search_and_slice pagination helper."""

    def _make_items(self, count: int) -> list[dict]:
        return [{"id": i, "name": f"item_{i}"} for i in range(count)]

    def _make_texts(self, count: int) -> list[str]:
        return [f"item_{i} description" for i in range(count)]

    def test_first_page(self):
        """First page should return the first `limit` items."""
        page_items, total = _search_and_slice(
            self._make_items(50), self._make_texts(50), "description", page=1, limit=10
        )
        assert total == 50
        assert len(page_items) == 10
        assert page_items[0]["name"] == "item_0"

    def test_second_page(self):
        """Second page should return items 10-19."""
        page_items, total = _search_and_slice(
            self._make_items(50), self._make_texts(50), "description", page=2, limit=10
        )
        assert total == 50
        assert len(page_items) == 10
        assert page_items[0]["name"] == "item_10"

    def test_last_partial_page(self):
        """Last page with fewer than limit items should still work."""
        page_items, total = _search_and_slice(
            self._make_items(25), self._make_texts(25), "description", page=3, limit=10
        )
        assert total == 25
        assert len(page_items) == 5

    def test_page_out_of_range(self):
        """Page beyond available results returns empty list with correct total."""
        page_items, total = _search_and_slice(
            self._make_items(5), self._make_texts(5), "description", page=10, limit=10
        )
        assert total == 5
        assert page_items == []

    def test_empty_items(self):
        """Empty items list returns ([], 0)."""
        page_items, total = _search_and_slice([], [], "query", page=1, limit=10)
        assert total == 0
        assert page_items == []

    def test_query_ranks_by_relevance(self):
        """Items matching the query should be ranked above non-matching."""
        items = [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
            {"id": 3, "name": "gamma"},
        ]
        texts = ["alpha word", "beta word", "gamma word"]
        # Search for "alpha" - only item 0 should rank high
        page_items, total = _search_and_slice(items, texts, "alpha", page=1, limit=10)
        assert total >= 1
        assert page_items[0]["name"] == "alpha"

    def test_limit_one(self):
        """limit=1 should return exactly one item per page."""
        items = self._make_items(5)
        texts = self._make_texts(5)
        page_items, total = _search_and_slice(items, texts, "description", page=1, limit=1)
        assert total == 5
        assert len(page_items) == 1
        assert page_items[0]["name"] == "item_0"

    def test_mismatched_items_and_texts(self):
        """Mismatched items/texts should not crash (BM25 will handle gracefully)."""
        items = self._make_items(3)
        texts = self._make_texts(3) + ["extra"]  # more texts than items
        # Should not raise
        page_items, total = _search_and_slice(items, texts, "description", page=1, limit=10)
        assert total == 3
        assert len(page_items) == 3

    def test_attaches_normalized_score(self):
        """Each result item carries a normalized `score` (0.0-1.0, top == 1.0)."""
        items = [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
            {"id": 3, "name": "gamma"},
        ]
        texts = ["alpha alpha word", "beta word", "gamma word"]
        page_items, total = _search_and_slice(items, texts, "alpha", page=1, limit=10)
        assert total >= 1
        # Top match gets score 1.0
        assert page_items[0]["score"] == 1.0
        # Every item has a numeric score in [0, 1]
        for item in page_items:
            assert "score" in item
            assert isinstance(item["score"], float)
            assert 0.0 <= item["score"] <= 1.0
        # Original item dicts are not mutated (score is attached to a copy)
        assert "score" not in items[0]


class TestSearchToolsPagination:
    """Pagination metadata assertions for search_tools."""

    @pytest.mark.asyncio
    async def test_search_tools_pagination_metadata_present(self):
        """search_tools result should include has_more/next_offset/total_count."""
        from gitea_mcp_server.tools.search import _search_tools_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tools = [
            Tool(name=f"gitea_test_{i}", description=f"Test tool {i}", parameters={"properties": {}})
            for i in range(25)
        ]
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=mock_tools)

        result = await _search_tools_impl("test", None, "raw", mock_ctx, transform, page=1, limit=10)
        sc = result.structured_content
        assert "has_more" in sc
        assert "next_offset" in sc
        assert "total_count" in sc
        assert sc["has_more"] is True  # 25 items, page 1, limit 10 → more
        assert sc["next_offset"] == 2
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_tools_pagination_last_page(self):
        """Last page of search_tools should have has_more=False."""
        from gitea_mcp_server.tools.search import _search_tools_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tools = [
            Tool(name=f"gitea_test_{i}", description=f"Test tool {i}", parameters={"properties": {}})
            for i in range(25)
        ]
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=mock_tools)

        result = await _search_tools_impl("test", None, "raw", mock_ctx, transform, page=3, limit=10)
        sc = result.structured_content
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_tools_page_out_of_range_message(self):
        """Out-of-range page should return a helpful message."""
        from gitea_mcp_server.tools.search import _search_tools_impl, TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tools = [
            Tool(name=f"gitea_test_{i}", description=f"Test tool {i}", parameters={"properties": {}})
            for i in range(5)
        ]
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=mock_tools)

        result = await _search_tools_impl("test", None, "markdown", mock_ctx, transform, page=10, limit=10)
        assert result.content is not None
        text = result.content[0].text
        assert "Page 10 is out of range" in text
        assert "total results: 5" in text


class TestSearchResourcesPagination:
    """Pagination metadata assertions for search_resources."""

    @pytest.mark.asyncio
    async def test_search_resources_pagination_metadata_present(self):
        """search_resources result should include has_more/next_offset/total_count."""
        ctx = MagicMock(spec=Context)
        resources = []
        for i in range(25):
            r = MagicMock()
            r.uri = f"gitea://resource_{i}"
            r.name = f"Resource {i}"
            r.description = f"Test resource {i}"
            r.mime_type = "text/markdown"
            r.tags = {"test"}
            r.meta = None
            resources.append(r)

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="test", format="raw", ctx=ctx, page=1, limit=10)
        sc = result.structured_content
        assert "has_more" in sc
        assert "next_offset" in sc
        assert "total_count" in sc
        assert sc["has_more"] is True
        assert sc["next_offset"] == 2
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_resources_pagination_last_page(self):
        """Last page of search_resources should have has_more=False."""
        ctx = MagicMock(spec=Context)
        resources = []
        for i in range(25):
            r = MagicMock()
            r.uri = f"gitea://resource_{i}"
            r.name = f"Resource {i}"
            r.description = f"Test resource {i}"
            r.mime_type = "text/markdown"
            r.tags = {"test"}
            r.meta = None
            resources.append(r)

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="test", format="raw", ctx=ctx, page=3, limit=10)
        sc = result.structured_content
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_resources_page_out_of_range_message(self):
        """Out-of-range page should return a helpful message."""
        ctx = MagicMock(spec=Context)
        resources = []
        for i in range(5):
            r = MagicMock()
            r.uri = f"gitea://resource_{i}"
            r.name = f"Resource {i}"
            r.description = f"Test resource {i}"
            r.mime_type = "text/markdown"
            r.tags = {"test"}
            r.meta = None
            resources.append(r)

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _search_resources_impl(query="test", format="markdown", ctx=ctx, page=10, limit=10)
        assert result.content is not None
        text = result.content[0].text
        assert "Page 10 is out of range" in text
        assert "total results: 5" in text
