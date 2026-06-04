"""Unit tests for search engine (indexing, call_tool, format, serializer)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations
from mcp.types import TextContent

from gitea_mcp_server.constants import SEARCH_NAME_BOOST
from gitea_mcp_server.tools.search import (
    _compact_search_serializer,
    _extract_searchable_text_enhanced,
    _format_result,
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
    """Tests for call_tool output_schema."""

    def test_call_tool_has_output_schema(self):
        """_make_call_tool should return a Tool with output_schema set."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "result" in tool.output_schema["properties"]
        # call_tool does NOT set x-fastmcp-wrap-result -- it passes through
        # the inner tool's already-wrapped ToolResult, so the flag would
        # be a no-op (dead code).  Inner tools handle their own wrapping.
        assert "x-fastmcp-wrap-result" not in tool.output_schema

    def test_call_tool_result_property_accepts_any_type(self):
        """The 'result' property must not have a 'type' constraint (accepts arrays, etc.)."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        result_schema = tool.output_schema["properties"]["result"]
        # No "type" key means any JSON value is accepted (objects, arrays, strings, etc.)
        assert "type" not in result_schema, (
            f"Expected result to accept any type, got 'type': {result_schema.get('type')!r}"
        )


class TestFormatResult:
    """Tests for _format_result helper that formats ToolResult content by format.

    This helper is used by call_tool, search_tools, and tool_info to handle
    the ``format`` parameter (markdown/json/raw). It always preserves
    ``structured_content`` as raw data and only replaces ``content``.
    """

    def test_raw_format_returns_same_object(self):
        """format=raw should return the ToolResult unchanged."""
        from gitea_mcp_server.tools.search import _format_result

        inner = ToolResult(structured_content={"result": {"key": "value"}})
        result = _format_result(inner, "raw")
        assert result is inner

    def test_json_format_with_dict_data(self):
        """format=json with dict data should produce pretty-printed JSON in content."""
        import json as json_module

        from gitea_mcp_server.tools.search import _format_result

        data = {"key": "value", "num": 42}
        inner = ToolResult(structured_content={"result": data})
        result = _format_result(inner, "json")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        parsed = json_module.loads(result.content[0].text)
        assert parsed == data

    def test_json_format_with_list_data(self):
        """format=json with list data should produce pretty-printed JSON in content."""
        import json as json_module

        from gitea_mcp_server.tools.search import _format_result

        data = [{"name": "tool_a"}, {"name": "tool_b"}]
        inner = ToolResult(structured_content={"result": data})
        result = _format_result(inner, "json")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        parsed = json_module.loads(result.content[0].text)
        assert parsed == data

    def test_markdown_format_with_dict_data(self):
        """format=markdown with dict data should produce markdown in content."""
        from gitea_mcp_server.tools.search import _format_result

        data = {"name": "test_tool", "description": "A test tool"}
        inner = ToolResult(structured_content={"result": data})
        result = _format_result(inner, "markdown")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        assert "|" in result.content[0].text
        assert "name" in result.content[0].text.lower()

    def test_markdown_format_with_list_data(self):
        """format=markdown with list data should produce markdown in content."""
        from gitea_mcp_server.tools.search import _format_result

        data = [{"name": "tool_a", "description": "First"}]
        inner = ToolResult(structured_content={"result": data})
        result = _format_result(inner, "markdown")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        assert "|" in result.content[0].text
        assert "tool_a" in result.content[0].text

    def test_markdown_with_scalar_data_returns_unchanged(self):
        """format=markdown with scalar (non-dict/list) data should return ToolResult unchanged."""
        from gitea_mcp_server.tools.search import _format_result

        inner = ToolResult(structured_content={"result": "just a string"})
        result = _format_result(inner, "markdown")
        assert result is inner

    def test_no_structured_content_returns_unchanged(self):
        """ToolResult without structured_content should be returned unchanged."""
        from gitea_mcp_server.tools.search import _format_result

        inner = ToolResult(content=[TextContent(type="text", text="hello")], structured_content=None)
        result = _format_result(inner, "markdown")
        assert result is inner

    def test_missing_result_key_returns_unchanged(self):
        """structured_content without result key should be returned unchanged."""
        from gitea_mcp_server.tools.search import _format_result

        inner = ToolResult(structured_content={"other": "data"})
        result = _format_result(inner, "markdown")
        assert result is inner


class TestCallToolRuntimeBehavior:
    """Test runtime behavior of the call_tool function.

    call_tool is a proxy that delegates to ctx.fastmcp.call_tool().
    These tests verify it correctly passes ToolResult through without
    double-wrapping, and properly handles argument validation.
    """

    @pytest.mark.asyncio
    async def test_call_tool_passes_toolresult_through(self):
        """call_tool should reformat result with markdown by default, preserving structured_content."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": 1}, {"id": 2}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx)

        assert result.structured_content == {"result": [{"id": 1}, {"id": 2}]}
        assert len(result.content) == 1
        assert "| id |" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_tool_json_format(self):
        """call_tool with format=json should produce pretty-printed JSON in content."""
        import json as json_module

        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        data = [{"id": 1}, {"id": 2}]

        inner_result = ToolResult(
            content=[],
            structured_content={"result": data},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx, format="json")

        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        parsed = json_module.loads(result.content[0].text)
        assert parsed == data

    @pytest.mark.asyncio
    async def test_call_tool_raw_format(self):
        """call_tool with format=raw should return the inner ToolResult unchanged."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(
            content=[],
            structured_content={"result": {"key": "val"}},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx, format="raw")

        assert result is inner_result

    @pytest.mark.asyncio
    async def test_call_tool_no_double_wrap_through_convert_result(self):
        """convert_result must pass the reformatted ToolResult through unchanged."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(
            content=[],
            structured_content={"result": {"items": [1, 2, 3], "count": 3}},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        raw = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx)
        final = tool.convert_result(raw)

        assert final is raw, "convert_result must pass ToolResult through unchanged"
        assert final.structured_content == {"result": {"items": [1, 2, 3], "count": 3}}
        inner = final.structured_content["result"]
        assert "result" not in inner, (
            f"Double-wrapped! structured_content={final.structured_content}"
        )

    @pytest.mark.asyncio
    async def test_call_tool_preserves_user_meta_from_inner_tool(self):
        """call_tool should preserve meta from the inner tool's ToolResult."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_meta = {"fastmcp": {"wrap_result": True}, "custom": "data"}
        inner_result = ToolResult(
            content=[],
            structured_content={"result": {}},
            meta=inner_meta,
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        raw = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx)
        final = tool.convert_result(raw)

        assert final.meta == inner_meta

    @pytest.mark.asyncio
    async def test_call_tool_rejects_self_call(self):
        """call_tool should reject calling itself or search_tools."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="synthetic search tool"):
            await tool.fn(transform._call_tool_name, {}, ctx=mock_ctx)

        with pytest.raises(ValueError, match="synthetic search tool"):
            await tool.fn(transform._search_tool_name, {}, ctx=mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_parses_json_string_arguments(self):
        """String arguments should be parsed as JSON before forwarding."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(content=[], structured_content={"result": {}})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        await tool.fn("gitea_test_tool", '{"key": "val", "num": 42}', ctx=mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with(
            "gitea_test_tool", {"key": "val", "num": 42}
        )

    @pytest.mark.asyncio
    async def test_call_tool_rejects_non_dict_and_non_string_arguments(self):
        """Arguments that are neither dict nor None nor a JSON string should be rejected."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await tool.fn("gitea_test_tool", [1, 2, 3], ctx=mock_ctx)

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await tool.fn("gitea_test_tool", 42, ctx=mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_rejects_invalid_json(self):
        """Invalid JSON string arguments should be rejected."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Invalid JSON"):
            await tool.fn("gitea_test_tool", "{bad json}", ctx=mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_handles_none_arguments(self):
        """None arguments should be forwarded as None."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(content=[], structured_content={"result": []})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        await tool.fn("gitea_test_tool", None, ctx=mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with("gitea_test_tool", None)

    @pytest.mark.asyncio
    async def test_call_tool_handles_missing_arguments(self):
        """Omitting arguments should forward None."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(content=[], structured_content={"result": []})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        await tool.fn("gitea_test_tool", ctx=mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with("gitea_test_tool", None)

    @pytest.mark.asyncio
    async def test_call_tool_routes_array_result_from_inner_tool(self):
        """When inner tool returns an array wrapped in {"result": [...]}, pass through."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": "a"}, {"id": "b"}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        raw = await tool.fn("gitea_array_tool", ctx=mock_ctx)
        final = tool.convert_result(raw)

        assert final.structured_content == {"result": [{"id": "a"}, {"id": "b"}]}


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
        assert "annotations" not in result[0]

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
    """Extended tests for _format_result helper."""

    def test_markdown_with_pagination(self):
        """format=markdown should append pagination metadata when present."""
        from gitea_mcp_server.tools.search import _format_result

        data = [{"name": "tool_a"}, {"name": "tool_b"}]
        inner = ToolResult(
            structured_content={
                "result": data,
                "has_more": True,
                "next_offset": 10,
                "total_count": 42,
            }
        )
        result = _format_result(inner, "markdown")
        assert result.structured_content == inner.structured_content
        assert len(result.content) == 1
        text = result.content[0].text
        assert "| name |" in text
        assert "has_more" in text.lower() or "total" in text
        assert "42" in text

    def test_markdown_with_output_schema(self):
        """format=markdown should use output_schema for better column layout."""
        from gitea_mcp_server.tools.search import _format_result

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
        result = _format_result(inner, "markdown", output_schema=output_schema)
        assert result.structured_content == inner.structured_content
        assert len(result.content) == 1
        # output_schema restricts columns to those defined in the schema
        # Only "id" is defined in the schema, so only "Id" appears in output
        assert "| Id |" in result.content[0].text
        # "name" is not in the schema, so it's filtered out by formatter

    def test_unknown_format_returns_unchanged(self):
        """An unrecognized format string should return the ToolResult unchanged."""
        from gitea_mcp_server.tools.search import _format_result

        data = {"key": "value"}
        inner = ToolResult(structured_content={"result": data})
        result = _format_result(inner, "xml")
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
    async def test_call_tool_markdown_with_output_schema(self):
        """call_tool with format=markdown should use tool's output_schema for formatting."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        data = {"id": 1, "name": "test"}
        inner_result = ToolResult(
            content=[],
            structured_content={"result": data},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        # Simulate a tool with output_schema
        schema_tool = MagicMock()
        schema_tool.output_schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                },
            },
        }
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=schema_tool)

        result = await tool.fn("gitea_schema_tool", {"arg": 1}, ctx=mock_ctx, format="markdown")
        assert result.structured_content == {"result": data}
        assert len(result.content) == 1
        # Keys are capitalized by the markdown formatter
        assert "| Id |" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_tool_ctx_is_none_raises(self):
        """call_tool with ctx=None should raise ValueError."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        with pytest.raises(ValueError, match="Context is required"):
            await tool.fn("gitea_test_tool", {}, ctx=None)


class TestToolInfo:
    """Tests for the tool_info synthetic tool."""

    @pytest.mark.asyncio
    async def test_tool_info_returns_schema(self):
        """tool_info should return the schema for a known tool."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_tool_info_tool()

        known_tool = Tool(
            name="gitea_known_tool",
            description="A known tool",
            parameters={"properties": {"x": {"type": "integer"}}},
            tags={"issue"},
        )
        mock_ctx = MagicMock()
        # get_tool_catalog calls ctx.fastmcp.list_tools() internally
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[known_tool])

        result = await tool.fn("gitea_known_tool", ctx=mock_ctx)
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert schema["name"] == "gitea_known_tool"
        assert schema["description"] == "A known tool"

    @pytest.mark.asyncio
    async def test_tool_info_not_found(self):
        """tool_info should raise ValueError for unknown tool."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_tool_info_tool()

        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="not found"):
            await tool.fn("gitea_nonexistent_tool", ctx=mock_ctx)

    @pytest.mark.asyncio
    async def test_tool_info_ctx_is_none_raises(self):
        """tool_info with ctx=None should raise ValueError."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tool = transform._make_tool_info_tool()

        with pytest.raises(ValueError, match="Context is required"):
            await tool.fn("gitea_test_tool", ctx=None)
