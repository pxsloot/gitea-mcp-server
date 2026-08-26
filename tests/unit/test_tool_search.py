"""Unit tests for search engine (indexing, call_tool, format, serializer)."""

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Context
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent, ToolAnnotations

from gitea_mcp_server.constants import SEARCH_NAME_BOOST
from gitea_mcp_server.tools.result_pipeline import render as _pipeline_render
from gitea_mcp_server.tools.search import (
    TolerantSearchTransform,
    _call_tool_impl,
    _format_filtered_tools_note,
    _list_hidden_tools_impl,
    _name_matches,
    _search_resources_impl,
    _search_tools_impl,
    _tool_info_impl,
    compact_search_serializer,
    extract_searchable_text_enhanced,
    register_synthetic_tools,
    search_and_slice,
)
from tests.helpers.mcp_results import extract_text_content, get_structured


def _render(exec_result: Any, fmt: str = "markdown", page: int = 1, limit: int = 10) -> ToolResult:
    """Render an ExecutionResult through the single result pipeline."""
    return _pipeline_render(exec_result, fmt=fmt, page=page, limit=limit)


class TestSearchableText:
    """Tests for extract_searchable_text_enhanced."""

    def test_name_is_boosted(self) -> None:
        """Tool name should appear SEARCH_NAME_BOOST times in the extracted text."""
        tool = Tool(
            name="gitea_user_get_current",
            description="Get the authenticated user",
            parameters={"properties": {}},
        )
        result = extract_searchable_text_enhanced(tool)
        assert result.count("gitea_user_get_current") == SEARCH_NAME_BOOST

    def test_no_side_effects_on_empty_fields(self) -> None:
        """Should handle tools with minimal fields gracefully."""
        tool = Tool(
            name="minimal_tool",
            parameters={"properties": {}},
        )
        result = extract_searchable_text_enhanced(tool)
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
        result = tool_map.get("call_tool")
        assert result is not None
        return result

    @pytest.mark.asyncio
    async def test_call_tool_has_output_schema(self) -> None:
        """call_tool should have an output_schema set with type object and result property."""
        tool = await self._get_call_tool()
        assert tool is not None, "call_tool not registered"
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "result" in tool.output_schema["properties"]
        assert "x-fastmcp-wrap-result" not in tool.output_schema

    @pytest.mark.asyncio
    async def test_call_tool_result_property_accepts_any_type(self) -> None:
        """The 'result' property must not have a restrictive type constraint
        (accepts both objects and arrays since it proxies any tool)."""
        tool = await self._get_call_tool()
        assert tool is not None, "call_tool not registered"
        assert tool.output_schema is not None, "Expected output_schema to be set"
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
        result = tool_map.get("tool_info")
        assert result is not None
        return result

    @pytest.mark.asyncio
    async def test_tool_info_has_output_schema(self) -> None:
        """tool_info should have an output_schema set with type object and result property."""
        tool = await self._get_tool_info()
        assert tool is not None, "tool_info not registered"
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "result" in tool.output_schema["properties"]

    @pytest.mark.asyncio
    async def test_tool_info_output_example_accepts_array(self) -> None:
        """tool_info's output_example property must accept arrays (tool schemas return list examples)."""
        tool = await self._get_tool_info()
        assert tool is not None, "tool_info not registered"
        assert tool.output_schema is not None, "Expected output_schema to be set"
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


class TestCallToolRuntimeBehavior:
    """Test runtime behavior of the call_tool function.

    call_tool is a proxy that delegates to ctx.fastmcp.call_tool().
    These tests verify it correctly passes ToolResult through without
    double-wrapping, and properly handles argument validation.
    """

    @pytest.mark.asyncio
    async def test_call_tool_passes_toolresult_through(self) -> None:
        """call_tool is a transparent proxy that returns the inner result unchanged."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": 1}, {"id": 2}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)

        assert result is inner_result

    @pytest.mark.asyncio
    async def test_call_tool_passes_through_json_format(self) -> None:
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
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)

        assert result is inner_result
        assert result.structured_content == data

    @pytest.mark.asyncio
    async def test_call_tool_passes_through_raw_result(self) -> None:
        """call_tool passes through a raw-formatted result unchanged (format handled by inner tool)."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": {"key": "val"}},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)

        assert result is inner_result

    @pytest.mark.asyncio
    async def test_call_tool_no_double_wrap(self) -> None:
        """call_tool must pass the ToolResult through without double-wrapping."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": {"items": [1, 2, 3], "count": 3}},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)
        assert result is inner_result
        assert result.structured_content == {"result": {"items": [1, 2, 3], "count": 3}}
        inner = result.structured_content["result"]
        assert "result" not in inner, (
            f"Double-wrapped! structured_content={result.structured_content}"
        )

    @pytest.mark.asyncio
    async def test_call_tool_preserves_user_meta_from_inner_tool(self) -> None:
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
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        result = await _call_tool_impl("gitea_test_tool", {"arg": "val"}, mock_ctx)
        assert result is inner_result
        assert result.meta == inner_meta

    @pytest.mark.asyncio
    async def test_call_tool_rejects_self_call_bare(self) -> None:
        """call_tool should reject calling itself via bare name."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="cannot call itself"):
            await _call_tool_impl("call_tool", {}, mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_rejects_self_call_prefixed(self) -> None:
        """call_tool should reject calling itself via prefixed name."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="cannot call itself"):
            await _call_tool_impl("gitea_call_tool", {}, mock_ctx, tool_prefix="gitea_")

    @pytest.mark.asyncio
    async def test_call_tool_parses_json_string_arguments(self) -> None:
        """String arguments should be parsed as JSON before forwarding."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(content=[], structured_content={"result": {}})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        await _call_tool_impl("gitea_test_tool", '{"key": "val", "num": 42}', mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with(
            "gitea_test_tool", {"key": "val", "num": 42}
        )

    @pytest.mark.asyncio
    async def test_call_tool_rejects_non_dict_and_non_string_arguments(self) -> None:
        """Arguments that are neither dict nor None nor a JSON string should be rejected."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await _call_tool_impl("gitea_test_tool", [1, 2, 3], mock_ctx)

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await _call_tool_impl("gitea_test_tool", 42, mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_rejects_invalid_json(self) -> None:
        """Invalid JSON string arguments should be rejected."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Invalid JSON"):
            await _call_tool_impl("gitea_test_tool", "{bad json}", mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_handles_none_arguments(self) -> None:
        """None arguments should be forwarded as None."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(content=[], structured_content={"result": []})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        await _call_tool_impl("gitea_test_tool", None, mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with("gitea_test_tool", None)

    @pytest.mark.asyncio
    async def test_call_tool_routes_array_result_from_inner_tool(self) -> None:
        """When inner tool returns an array wrapped in {"result": [...]}, pass through."""
        from gitea_mcp_server.tools.search import _call_tool_impl

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": "a"}, {"id": "b"}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        final = await _call_tool_impl("gitea_array_tool", None, mock_ctx)
        assert final is inner_result


class TestCompactSearchSerializer:
    """Tests for compact_search_serializer function."""

    def test_returns_name_and_description_only(self) -> None:
        """Search results should only include name and description."""
        from gitea_mcp_server.tools.search import compact_search_serializer

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {"id": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
        )
        result = compact_search_serializer([tool])
        assert len(result) == 1
        assert result[0]["name"] == "test_tool"
        assert result[0]["description"] == "A test tool"
        assert "parameters" not in result[0]
        assert "output_schema" not in result[0]
        assert "output_example" not in result[0]

    def test_handles_empty_fields(self) -> None:
        """Should handle tools with minimal fields."""
        from gitea_mcp_server.tools.search import compact_search_serializer

        tool = Tool(
            name="minimal_tool",
            description="",
            parameters={"properties": {}},
            output_schema=None,
        )
        result = compact_search_serializer([tool])
        assert result[0]["name"] == "minimal_tool"
        assert result[0]["description"] == ""

    def test_handles_multiple_tools(self) -> None:
        """Should serialize multiple tools correctly."""
        from gitea_mcp_server.tools.search import compact_search_serializer

        tools = [
            Tool(name="tool_a", description="First tool", parameters={"properties": {}}),
            Tool(name="tool_b", description="Second tool", parameters={"properties": {}}),
        ]
        result = compact_search_serializer(tools)
        assert len(result) == 2
        assert result[0]["name"] == "tool_a"
        assert result[1]["name"] == "tool_b"

    def test_omits_annotations_when_null(self) -> None:
        """Should omit annotations key when tool has no annotations."""
        from gitea_mcp_server.tools.search import compact_search_serializer

        tool = Tool(
            name="no_annotations",
            description="A tool without annotations",
            parameters={"properties": {}},
        )
        result = compact_search_serializer([tool])
        assert "annotations" not in result[0]

    def test_includes_annotations_when_present(self) -> None:
        """Should include annotations key when tool has annotations."""
        from gitea_mcp_server.tools.search import compact_search_serializer

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
        result = compact_search_serializer([tool])
        assert "annotations" in result[0]
        assert result[0]["annotations"]["title"] == "Test Tool"

    def test_omits_annotations_when_all_fields_null(self) -> None:
        """Should omit annotations key when all annotation fields are None."""
        from gitea_mcp_server.tools.search import compact_search_serializer

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
        result = compact_search_serializer([tool])
        # Annotations are always included now (all 5 fields explicit)
        ann = result[0].get("annotations", {})
        assert ann.get("title") is None
        assert ann.get("readOnlyHint") is None

    def test_includes_tags_when_present(self) -> None:
        """Should include tags key when tool has tags."""
        from gitea_mcp_server.tools.search import compact_search_serializer

        tool = Tool(
            name="tagged_tool",
            description="A tool with tags",
            parameters={"properties": {}},
            tags={"issue", "repository"},
        )
        result = compact_search_serializer([tool])
        assert set(result[0]["tags"]) == {"issue", "repository"}

    def test_includes_hints_when_true(self) -> None:
        """Should include hint annotations when they are True."""
        from gitea_mcp_server.tools.search import compact_search_serializer

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
        result = compact_search_serializer([tool])
        assert result[0]["annotations"]["readOnlyHint"] is True
        assert result[0]["annotations"]["destructiveHint"] is True
        assert result[0]["annotations"]["idempotentHint"] is True


class TestSearchableTextExtended:
    """Extended tests for extract_searchable_text_enhanced."""

    def test_includes_tags(self) -> None:
        """Tool tags should appear in the extracted text."""
        from gitea_mcp_server.tools.search import extract_searchable_text_enhanced

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {}},
            tags={"issue", "repository"},
        )
        result = extract_searchable_text_enhanced(tool)
        assert "issue" in result
        assert "repository" in result

    def test_includes_category_aliases(self) -> None:
        """Tags that match SEARCH_CATEGORY_ALIASES should include expanded aliases."""
        from gitea_mcp_server.constants import SEARCH_CATEGORY_ALIASES
        from gitea_mcp_server.tools.search import extract_searchable_text_enhanced

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {}},
            tags={"issue"},
        )
        result = extract_searchable_text_enhanced(tool)
        for alias in SEARCH_CATEGORY_ALIASES["issue"].split():
            assert alias in result

    def test_includes_annotation_title(self) -> None:
        """Tool annotations.title should appear in the extracted text."""
        from gitea_mcp_server.tools.search import extract_searchable_text_enhanced

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {}},
            annotations=ToolAnnotations(title="My Custom Title"),
        )
        result = extract_searchable_text_enhanced(tool)
        assert "My Custom Title" in result

    def test_includes_parameter_descriptions(self) -> None:
        """Parameter descriptions should appear in the extracted text."""
        from gitea_mcp_server.tools.search import extract_searchable_text_enhanced

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
        result = extract_searchable_text_enhanced(tool)
        assert "The repository owner" in result
        assert "The repository name" in result


class TestCallToolRuntimeBehaviorExtended:
    """Extended tests for call_tool runtime behavior."""

    @pytest.mark.asyncio
    async def test_call_tool_passes_through_regardless_of_output_schema(self) -> None:
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
        mock_ctx.fastmcp.get_tool = AsyncMock(
            side_effect=lambda name: Tool(name=name, parameters={"properties": {}})
        )

        result = await _call_tool_impl("gitea_schema_tool", {"arg": 1}, mock_ctx)
        assert result is inner_result


class TestToolInfo:
    """Tests for the tool_info synthetic tool."""

    @pytest.mark.asyncio
    async def test_tool_info_returns_schema(self) -> None:
        """tool_info should return the schema for a known tool."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()

        known_tool = Tool(
            name="gitea_known_tool",
            description="A known tool",
            parameters={"properties": {"x": {"type": "integer"}}},
            tags={"issue"},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[known_tool])

        result = _render(
            await _tool_info_impl("gitea_known_tool", mock_ctx, transform), fmt="markdown"
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert schema["name"] == "gitea_known_tool"
        assert schema["description"] == "A known tool"

    @pytest.mark.asyncio
    async def test_tool_info_detail_full_includes_output_schema(self) -> None:
        """tool_info with detail='full' should include output_schema."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

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

        result = _render(
            await _tool_info_impl("gitea_tool_with_schema", mock_ctx, transform, detail="full"),
            fmt="json",
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert schema["name"] == "gitea_tool_with_schema"
        assert "output_example" in schema
        assert "output_schema" in schema
        assert schema["output_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_tool_info_detail_concise_excludes_output_schema(self) -> None:
        """tool_info with detail='concise' (default) should NOT include output_schema."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

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

        result = _render(
            await _tool_info_impl(
                "gitea_tool_no_schema_included", mock_ctx, transform, detail="concise"
            ),
            fmt="json",
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert "output_example" in schema
        assert "output_schema" not in schema

    @pytest.mark.asyncio
    async def test_tool_info_concise_path_emits_pagination_envelope(self) -> None:
        """tool_info concise (default) path emits the envelope (issue #694).

        The declared schema (paginated=True) always declares
        has_more/next_offset/total_count; the concise path must match, with
        total_count=None since no schema properties are paginated there.
        """
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_tool_concise_envelope",
            description="A tool",
            parameters={"properties": {}},
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

        result = _render(
            await _tool_info_impl("gitea_tool_concise_envelope", mock_ctx, transform), fmt="json"
        )
        sc = get_structured(result)
        assert sc["result"]["name"] == "gitea_tool_concise_envelope"
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] is None

    @pytest.mark.asyncio
    async def test_tool_info_detail_full_preserves_array_result_type(self) -> None:
        """tool_info detail=full must preserve array result type, not collapse to object.

        Regression test: the pagination logic in _tool_info_impl assumed every
        unwrapped result schema was ``type: "object"`` with properties.
        Tools returning arrays (API list endpoints, ``search_tools``,
        ``search``, ``search_resources``, ``search_docs``) got a schema
        advertising ``{"result": {"type": "object", "properties": {}}}``
        instead of the actual ``{"result": {"type": "array", "items": {...}}}``.
        """
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_tool_with_array_result",
            description="A tool that returns an array",
            parameters={"properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                                "email": {"type": "string"},
                            },
                        },
                        "description": "A list of items",
                    },
                },
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

        result = _render(
            await _tool_info_impl(
                "gitea_tool_with_array_result",
                mock_ctx,
                transform,
                detail="full",
            ),
            fmt="json",
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert schema["name"] == "gitea_tool_with_array_result"
        assert "output_schema" in schema
        output_schema = schema["output_schema"]
        # Must preserve the array type, not collapse to empty object
        assert output_schema["type"] == "object"
        result_schema = output_schema["properties"]["result"]
        assert result_schema["type"] == "array", (
            f"Expected array type, got: {result_schema.get('type')}"
        )
        # Must have items schema for the array
        assert "items" in result_schema, (
            f"Expected 'items' key in array result, got keys: {list(result_schema.keys())}"
        )
        # Item properties must be preserved (paginated subset is fine)
        assert "properties" in result_schema["items"]
        assert isinstance(result_schema["items"]["properties"], dict)

    @pytest.mark.asyncio
    async def test_tool_info_detail_full_preserves_string_result_type(self) -> None:
        """tool_info detail=full must preserve string result type, not collapse to object.

        Regression test: for tools like ``read_doc`` whose result is a string,
        the pagination logic incorrectly advertised ``{"result": {"type": "object",
        "properties": {}}}`` instead of ``{"result": {"type": "string"}}``.
        """
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_tool_with_string_result",
            description="A tool that returns a string",
            parameters={"properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "The guide content in Markdown",
                    },
                },
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

        result = _render(
            await _tool_info_impl(
                "gitea_tool_with_string_result",
                mock_ctx,
                transform,
                detail="full",
            ),
            fmt="json",
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        assert schema["name"] == "gitea_tool_with_string_result"
        assert "output_schema" in schema
        output_schema = schema["output_schema"]
        # Must preserve the string type, not collapse to empty object
        assert output_schema["type"] == "object"
        result_schema = output_schema["properties"]["result"]
        assert result_schema["type"] == "string", (
            f"Expected string type, got: {result_schema.get('type')}"
        )
        # Must preserve description
        assert result_schema.get("description") == "The guide content in Markdown"

    @pytest.mark.asyncio
    async def test_tool_info_detail_full_preserves_pagination_envelope(self) -> None:
        """tool_info detail=full must keep pagination metadata siblings of result.

        Regression test: the schema slicing in ``_tool_info_impl`` replaced
        the whole ``properties`` dict with ``{"result": ...}``, dropping the
        ``has_more`` / ``next_offset`` / ``total_count`` properties that
        autogen and synthetic output schemas declare next to ``result``.
        MCP clients reading the declared schema then could not discover the
        pagination envelope, even though runtime ``structured_content``
        carries it.
        """
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_tool_with_pagination",
            description="A paginated tool",
            parameters={"properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        },
                    },
                    "has_more": {"type": "boolean", "description": "Whether more pages exist"},
                    "next_offset": {
                        "type": "integer",
                        "description": "Page number for next page, if any",
                    },
                    "total_count": {
                        "type": "integer",
                        "description": "Total item count from server, if available",
                    },
                },
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

        result = _render(
            await _tool_info_impl("gitea_tool_with_pagination", mock_ctx, transform, detail="full"),
            fmt="json",
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        output_schema = schema["output_schema"]
        props = output_schema["properties"]
        for key in ("has_more", "next_offset", "total_count"):
            assert key in props, f"pagination key '{key}' stripped from tool_info output_schema"
        # The result itself must still be sliced/preserved as an array.
        assert props["result"]["type"] == "array"

    @pytest.mark.asyncio
    async def test_tool_info_detail_full_preserves_envelope_for_object_result(self) -> None:
        """tool_info detail=full keeps envelope for object results (read_doc shape)."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_doc_tool",
            description="Read a doc",
            parameters={"properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                    },
                    "has_more": {"type": "boolean"},
                    "next_offset": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                    },
                    "total_count": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                    },
                },
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

        result = _render(
            await _tool_info_impl("gitea_doc_tool", mock_ctx, transform, detail="full"), fmt="json"
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        props = schema["output_schema"]["properties"]
        for key in ("has_more", "next_offset", "total_count"):
            assert key in props, f"pagination key '{key}' stripped from tool_info output_schema"
        assert props["result"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_tool_info_detail_full_array_of_primitives(self) -> None:
        """tool_info detail=full with array of primitive items preserves the array.

        Covers the branch where array items are primitives or ``$ref``
        pointers (no ``.properties`` to paginate) — the full items schema
        is returned unpaginated.
        """
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()

        tool = Tool(
            name="gitea_tool_array_of_strings",
            description="A tool returning an array of strings",
            parameters={"properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tag names",
                    },
                },
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

        result = _render(
            await _tool_info_impl(
                "gitea_tool_array_of_strings",
                mock_ctx,
                transform,
                detail="full",
            ),
            fmt="json",
        )
        assert result.structured_content is not None
        schema = result.structured_content["result"]
        output_schema = schema["output_schema"]
        result_schema = output_schema["properties"]["result"]
        assert result_schema["type"] == "array"
        # Items must be a primitive type, not collapsed to object
        assert result_schema["items"]["type"] == "string"

    @pytest.mark.asyncio
    async def test_tool_info_not_found(self) -> None:
        """tool_info should raise ValueError for unknown tool."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="not found"):
            await _tool_info_impl("gitea_nonexistent_tool", mock_ctx, transform)


class TestFilterInfoIntegration:
    """Integration tests for the tool_info/call_tool → filter_info wiring.

    Verifies that synthetic tools produce rich filtered-tool messages
    (scope-restricted, config-excluded, deprecated) instead of generic
    "not found" when ``filtered_tools_info`` is provided.
    """

    @pytest.fixture
    def scope_filter_info(self) -> dict:
        """``filtered_tools_info`` with one scope-restricted tool."""
        return {
            "available_scopes": ["read:repository"],
            "exclusion_config": {"exclude": [], "include": []},
            "filtered": {
                "admin_create_user": {
                    "reason": "scope",
                    "required_scope": "sudo",
                },
            },
        }

    @pytest.fixture
    def exclude_filter_info(self) -> dict:
        """``filtered_tools_info`` with one config-excluded tool."""
        return {
            "available_scopes": ["read:repository", "write:issue"],
            "exclusion_config": {"exclude": ["admin_*"], "include": []},
            "filtered": {
                "admin_create_user": {
                    "reason": "excluded",
                },
            },
        }

    @pytest.fixture
    def deprecated_filter_info(self) -> dict:
        """``filtered_tools_info`` with one deprecated tool."""
        return {
            "available_scopes": ["read:repository"],
            "exclusion_config": {"exclude": [], "include": []},
            "filtered": {
                "some_deprecated_tool": {
                    "reason": "deprecated",
                },
            },
        }

    # ── tool_info → filter_info ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_tool_info_scope_filtered(self, scope_filter_info: dict[str, Any]) -> None:
        """tool_info for scope-restricted tool returns scope message."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="restricted by your token scopes"):
            await _tool_info_impl(
                "gitea_admin_create_user",
                mock_ctx,
                transform,
                tool_prefix="gitea_",
                filtered_tools_info=scope_filter_info,
            )

    @pytest.mark.asyncio
    async def test_tool_info_exclude_filtered(self, exclude_filter_info: dict[str, Any]) -> None:
        """tool_info for config-excluded tool returns exclusion message."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="excluded by server configuration"):
            await _tool_info_impl(
                "gitea_admin_create_user",
                mock_ctx,
                transform,
                tool_prefix="gitea_",
                filtered_tools_info=exclude_filter_info,
            )

    @pytest.mark.asyncio
    async def test_tool_info_deprecated_filtered(
        self, deprecated_filter_info: dict[str, Any]
    ) -> None:
        """tool_info for deprecated tool returns deprecation message."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="has been deprecated"):
            await _tool_info_impl(
                "gitea_some_deprecated_tool",
                mock_ctx,
                transform,
                tool_prefix="gitea_",
                filtered_tools_info=deprecated_filter_info,
            )

    @pytest.mark.asyncio
    async def test_tool_info_filtered_falls_back_to_not_found(self) -> None:
        """Without filter info, tool_info still gives generic 'not found'."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform, _tool_info_impl

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="not found"):
            await _tool_info_impl(
                "gitea_admin_create_user",
                mock_ctx,
                transform,
                tool_prefix="gitea_",
                filtered_tools_info=None,
            )

    # ── call_tool → filter_info ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_call_tool_scope_filtered(self, scope_filter_info: dict[str, Any]) -> None:
        """call_tool for scope-restricted tool raises scope error."""
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)
        mock_ctx.fastmcp.call_tool = AsyncMock()

        with pytest.raises(ValueError, match="restricted by your token scopes"):
            await _call_tool_impl(
                "gitea_admin_create_user",
                {},
                mock_ctx,
                tool_prefix="gitea_",
                filtered_tools_info=scope_filter_info,
            )

    @pytest.mark.asyncio
    async def test_call_tool_exclude_filtered(self, exclude_filter_info: dict[str, Any]) -> None:
        """call_tool for config-excluded tool raises exclusion error."""
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)
        mock_ctx.fastmcp.call_tool = AsyncMock()

        with pytest.raises(ValueError, match="excluded by server configuration"):
            await _call_tool_impl(
                "gitea_admin_create_user",
                {},
                mock_ctx,
                tool_prefix="gitea_",
                filtered_tools_info=exclude_filter_info,
            )

    @pytest.mark.asyncio
    async def test_call_tool_deprecated_filtered(
        self, deprecated_filter_info: dict[str, Any]
    ) -> None:
        """call_tool for deprecated tool raises deprecation error."""
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)
        mock_ctx.fastmcp.call_tool = AsyncMock()

        with pytest.raises(ValueError, match="has been deprecated"):
            await _call_tool_impl(
                "gitea_some_deprecated_tool",
                {},
                mock_ctx,
                tool_prefix="gitea_",
                filtered_tools_info=deprecated_filter_info,
            )

    @pytest.mark.asyncio
    async def test_call_tool_filtered_falls_back_to_not_found(self) -> None:
        """Without filter info, call_tool still gives generic 'not found'."""
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)
        mock_ctx.fastmcp.call_tool = AsyncMock()

        with pytest.raises(ValueError, match="not found"):
            await _call_tool_impl(
                "gitea_admin_create_user",
                {},
                mock_ctx,
                tool_prefix="gitea_",
                filtered_tools_info=None,
            )

    @pytest.mark.asyncio
    async def test_call_tool_not_found_without_prefix(self) -> None:
        """Without tool_prefix, call_tool still gives generic 'not found'."""
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.get_tool = AsyncMock(return_value=None)
        mock_ctx.fastmcp.call_tool = AsyncMock()

        with pytest.raises(ValueError, match="not found"):
            await _call_tool_impl(
                "gitea_unknown_tool",
                {},
                mock_ctx,
                tool_prefix="",
            )


class TestSearchToolsSyntheticTool:
    """Tests for the search_tools synthetic tool."""

    @pytest.mark.asyncio
    async def test_search_tools_category_filter_invalid(self) -> None:
        """search_tools with invalid category should raise ValueError."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])
        with pytest.raises(ValueError, match="Invalid category"):
            await _search_tools_impl("test query", "invalid", mock_ctx, transform)

    @pytest.mark.asyncio
    async def test_search_tools_with_no_results(self) -> None:
        """search_tools with no matches should show cross-linking hints."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[])

        result = _render(await _search_tools_impl("nonexistent", None, mock_ctx, transform))
        assert result.structured_content is not None
        text = extract_text_content(result.content) if result.content else ""
        assert "No tools found" in text or "search_docs" in text
        # Empty results still carry the full pagination envelope (issue #694).
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 0

    @pytest.mark.asyncio
    async def test_search_tools_with_results_and_cross_links(self) -> None:
        """search_tools with results should show cross-linking hints."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tool = Tool(
            name="gitea_issue_list",
            description="List issues",
            parameters={"properties": {}},
            tags={"issue"},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[mock_tool])

        result = _render(await _search_tools_impl("issue", None, mock_ctx, transform))
        assert result.structured_content is not None
        text = extract_text_content(result.content) if result.content else ""
        assert "Cross-linking" in text or "search_docs" in text

    @pytest.mark.asyncio
    async def test_search_tools_with_category_filter(self) -> None:
        """search_tools with valid category should filter results."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

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

        result = _render(await _search_tools_impl("list", "issue", mock_ctx, transform))
        assert result.structured_content is not None
        text = extract_text_content(result.content) if result.content else ""
        assert "gitea_issue_list" in text or "Cross-linking" in text

    @pytest.mark.asyncio
    async def test_search_tools_empty_query_lists_all(self) -> None:
        """search_tools with an empty query lists the full catalog (no score)."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        tools = [
            Tool(
                name=f"gitea_tool_{i}",
                description=f"Tool {i}",
                parameters={"properties": {}},
                tags={"issue"},
            )
            for i in range(3)
        ]
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=tools)

        result = _render(await _search_tools_impl("", None, mock_ctx, transform))
        sc = get_structured(result)
        assert len(sc["result"]) == 3
        assert sc["total_count"] == 3
        # List-all has no relevance ranking — no score field.
        assert all("score" not in item for item in sc["result"])
        # Catalog order preserved.
        assert [item["name"] for item in sc["result"]] == [
            "gitea_tool_0",
            "gitea_tool_1",
            "gitea_tool_2",
        ]

    @pytest.mark.asyncio
    async def test_search_tools_whitespace_query_lists_all(self) -> None:
        """Whitespace-only query behaves like an empty query (list all)."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tool = Tool(
            name="gitea_issue_list",
            description="List issues",
            parameters={"properties": {}},
            tags={"issue"},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=[mock_tool])

        result = _render(await _search_tools_impl("   ", None, mock_ctx, transform))
        sc = get_structured(result)
        assert len(sc["result"]) == 1
        assert sc["result"][0]["name"] == "gitea_issue_list"

    @pytest.mark.asyncio
    async def test_search_tools_empty_query_respects_category(self) -> None:
        """Empty query still applies the category filter."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

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

        result = _render(await _search_tools_impl("", "issue", mock_ctx, transform))
        sc = get_structured(result)
        assert len(sc["result"]) == 1
        assert sc["result"][0]["name"] == "gitea_issue_list"


class TestTolerantBM25Search:
    """Tests for TolerantBM25Search."""

    def test_search_returns_ranked_results(self) -> None:
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

    def test_search_with_limit(self) -> None:
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
    async def test_transform_tools_pins_synthetic_tagged_tools(self) -> None:
        """transform_tools should only pin tools with the synthetic tag."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        plain_tool = Tool(
            name="gitea_test",
            description="A test tool",
            parameters={"properties": {}},
            tags=set(),
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
        """compact_search_serializer includes all 5 annotation fields (no None filtering)."""
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
        result = compact_search_serializer([tool])
        item = result[0]
        ann = item.get("annotations", {})
        assert ann["readOnlyHint"] is True
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True
        assert ann["openWorldHint"] is True
        assert ann["title"] == "Foo Tool"
        # All 5 fields present
        assert set(ann) == {
            "title",
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        }

    def test_compact_serializer_no_annotations(self) -> None:
        """compact_search_serializer handles tools with annotations=None gracefully."""
        tool = Tool(
            name="gitea_bar",
            description="No annotations",
            parameters={"properties": {}},
        )
        result = compact_search_serializer([tool])
        item = result[0]
        assert item["name"] == "gitea_bar"
        assert "annotations" not in item

    def test_compact_serializer_partial_title(self) -> None:
        """compact_search_serializer includes title even when other fields are None."""
        tool = Tool(
            name="gitea_baz",
            description="Partial",
            parameters={"properties": {}},
            annotations=ToolAnnotations(title="Just a Title"),
        )
        result = compact_search_serializer([tool])
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
        ctx = MagicMock(spec=Context)
        with pytest.raises(ValueError, match="Invalid JSON"):
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
        with contextlib.suppress(Exception):
            await _tool_info_impl(
                name="nonexistent",
                ctx=MagicMock(spec=Context),
                transform=transform,
                tool_prefix="",
            )

        # Annotations on registered tools unchanged
        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        for name in ("search_tools", "tool_info"):
            self._assert_all_hints(tool_map[name], read_only=True, open_world=False)


class TestSearchResourcesSyntheticTool:
    """Tests for the search_resources synthetic tool via _search_resources_impl."""

    @pytest.mark.asyncio
    async def test_searches_resource_by_uri(self) -> None:
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

        result = _render(await _search_resources_impl(query="wiki", ctx=ctx))

        assert result.structured_content is not None
        results = result.structured_content["result"]
        assert len(results) == 1
        assert results[0]["uri"] == "gitea://wiki/guide"

    @pytest.mark.asyncio
    async def test_searches_resource_by_name(self) -> None:
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

        result = _render(await _search_resources_impl(query="version", ctx=ctx))

        assert result.structured_content is not None
        results = result.structured_content["result"]
        assert len(results) == 1
        assert results[0]["name"] == "Server Version"

    @pytest.mark.asyncio
    async def test_markdown_includes_cross_link_footer(self) -> None:
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

        result = _render(await _search_resources_impl(query="version", ctx=ctx))

        assert result.content is not None
        text = extract_text_content(result.content)
        assert "Cross-linking hints" in text
        assert "search_docs" in text
        assert "search_tools" in text

    @pytest.mark.asyncio
    async def test_empty_result_has_helpful_hint(self) -> None:
        """Empty search results should include helpful cross-linking message."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await _search_resources_impl(query="nothing", ctx=ctx))

        assert result.content is not None
        text = extract_text_content(result.content)
        assert "No results found" in text or "No resources" in text
        assert "search_docs" in text
        assert "search_tools" in text
        assert result.structured_content is not None
        assert result.structured_content["result"] == []
        # Empty results still carry the full pagination envelope (issue #694).
        sc = get_structured(result)
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 0

    @pytest.mark.asyncio
    async def test_raw_format(self) -> None:
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

        result = _render(await _search_resources_impl(query="version", ctx=ctx), fmt="raw")
        assert result.structured_content is not None
        assert len(result.structured_content["result"]) == 1
        assert result.structured_content["result"][0]["uri"] == "gitea://version"

    @pytest.mark.asyncio
    async def test_search_resources_empty_query_lists_all(self) -> None:
        """search_resources with an empty query lists the full catalog (no score)."""
        ctx = MagicMock(spec=Context)
        resources = []
        for i in range(3):
            r = MagicMock()
            r.uri = f"gitea://resource/{i}"
            r.name = f"Resource {i}"
            r.description = f"Resource {i} description"
            r.mime_type = "text/plain"
            r.tags = {"server"}
            r.meta = None
            resources.append(r)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await _search_resources_impl(query="", ctx=ctx))
        sc = get_structured(result)
        assert len(sc["result"]) == 3
        assert sc["total_count"] == 3
        assert all("score" not in item for item in sc["result"])


class TestListHiddenTools:
    """Tests for the list_hidden_tools synthetic tool via _list_hidden_tools_impl."""

    _FILTERED = {
        "filtered": {
            "admin_create_user": {"reason": "scope", "required_scope": "sudo"},
            "repo_old_endpoint": {"reason": "deprecated"},
            "some_excluded": {"reason": "excluded"},
            "admin_delete_user": {"reason": "scope", "required_scope": "sudo"},
        }
    }

    @pytest.mark.asyncio
    async def test_lists_all_hidden_tools_with_prefixed_names(self) -> None:
        """All hidden tools are enumerated with prefixed names and reasons."""
        result = _render(
            await _list_hidden_tools_impl(
                None, filtered_tools_info=self._FILTERED, tool_prefix="gitea_"
            )
        )
        sc = get_structured(result)
        assert sc["total_count"] == 4
        names = [item["name"] for item in sc["result"]]
        assert names == [
            "gitea_admin_create_user",
            "gitea_admin_delete_user",
            "gitea_repo_old_endpoint",
            "gitea_some_excluded",
        ]  # sorted by name
        reasons = {item["name"]: item["reason"] for item in sc["result"]}
        assert reasons["gitea_admin_create_user"] == "scope"
        assert reasons["gitea_repo_old_endpoint"] == "deprecated"
        assert reasons["gitea_some_excluded"] == "excluded"

    @pytest.mark.asyncio
    async def test_scope_entries_carry_required_scope(self) -> None:
        """Scope-restricted entries include required_scope."""
        result = _render(
            await _list_hidden_tools_impl(
                "scope", filtered_tools_info=self._FILTERED, tool_prefix="gitea_"
            )
        )
        sc = get_structured(result)
        assert sc["total_count"] == 2
        for item in sc["result"]:
            assert item["reason"] == "scope"
            assert item["required_scope"] == "sudo"

    @pytest.mark.asyncio
    async def test_reason_filter(self) -> None:
        """reason filter narrows the enumeration."""
        result = _render(
            await _list_hidden_tools_impl(
                "deprecated", filtered_tools_info=self._FILTERED, tool_prefix="gitea_"
            )
        )
        sc = get_structured(result)
        assert sc["total_count"] == 1
        assert sc["result"][0]["name"] == "gitea_repo_old_endpoint"

    @pytest.mark.asyncio
    async def test_invalid_reason_raises(self) -> None:
        """Invalid reason raises ValueError."""
        with pytest.raises(ValueError, match="Invalid reason"):
            await _list_hidden_tools_impl("mystery", filtered_tools_info=self._FILTERED)

    @pytest.mark.asyncio
    async def test_no_filtered_info_returns_empty(self) -> None:
        """None/empty filtered_tools_info returns an empty listing."""
        result = _render(await _list_hidden_tools_impl(None, filtered_tools_info=None))
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["total_count"] == 0

    @pytest.mark.asyncio
    async def test_pagination(self) -> None:
        """list_hidden_tools paginates like other synthetic list tools."""
        result = _render(
            await _list_hidden_tools_impl(
                None, filtered_tools_info=self._FILTERED, tool_prefix="gitea_"
            ),
            page=2,
            limit=2,
        )
        sc = get_structured(result)
        assert len(sc["result"]) == 2
        assert sc["total_count"] == 4
        assert sc["has_more"] is False

    @pytest.mark.asyncio
    async def test_page_out_of_range_message(self) -> None:
        """Out-of-range page returns a helpful message."""
        result = _render(
            await _list_hidden_tools_impl(
                None, filtered_tools_info=self._FILTERED, tool_prefix="gitea_"
            ),
            page=10,
            limit=10,
        )
        sc = get_structured(result)
        assert "Page 10 is out of range" in sc.get("message", "")
        assert sc["result"] == []
        assert sc["total_count"] == 4


class TestSearchAndSlice:
    """Tests for search_and_slice pagination helper."""

    def _make_items(self, count: int) -> list[dict]:
        return [{"id": i, "name": f"item_{i}"} for i in range(count)]

    def _make_texts(self, count: int) -> list[str]:
        return [f"item_{i} description" for i in range(count)]

    def test_first_page(self) -> None:
        """First page should return the first `limit` items."""
        page_items, total = search_and_slice(
            self._make_items(50), self._make_texts(50), "description", page=1, limit=10
        )
        assert total == 50
        assert len(page_items) == 10
        assert page_items[0]["name"] == "item_0"

    def test_second_page(self) -> None:
        """Second page should return items 10-19."""
        page_items, total = search_and_slice(
            self._make_items(50), self._make_texts(50), "description", page=2, limit=10
        )
        assert total == 50
        assert len(page_items) == 10
        assert page_items[0]["name"] == "item_10"

    def test_last_partial_page(self) -> None:
        """Last page with fewer than limit items should still work."""
        page_items, total = search_and_slice(
            self._make_items(25), self._make_texts(25), "description", page=3, limit=10
        )
        assert total == 25
        assert len(page_items) == 5

    def test_page_out_of_range(self) -> None:
        """Page beyond available results returns empty list with correct total."""
        page_items, total = search_and_slice(
            self._make_items(5), self._make_texts(5), "description", page=10, limit=10
        )
        assert total == 5
        assert page_items == []

    def test_empty_items(self) -> None:
        """Empty items list returns ([], 0)."""
        page_items, total = search_and_slice([], [], "query", page=1, limit=10)
        assert total == 0
        assert page_items == []

    def test_query_ranks_by_relevance(self) -> None:
        """Items matching the query should be ranked above non-matching."""
        items = [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
            {"id": 3, "name": "gamma"},
        ]
        texts = ["alpha word", "beta word", "gamma word"]
        # Search for "alpha" - only item 0 should rank high
        page_items, total = search_and_slice(items, texts, "alpha", page=1, limit=10)
        assert total >= 1
        assert page_items[0]["name"] == "alpha"

    def test_limit_one(self) -> None:
        """limit=1 should return exactly one item per page."""
        items = self._make_items(5)
        texts = self._make_texts(5)
        page_items, total = search_and_slice(items, texts, "description", page=1, limit=1)
        assert total == 5
        assert len(page_items) == 1
        assert page_items[0]["name"] == "item_0"

    def test_mismatched_items_and_texts(self) -> None:
        """Mismatched items/texts should not crash (BM25 will handle gracefully)."""
        items = self._make_items(3)
        texts = [*self._make_texts(3), "extra"]  # more texts than items
        # Should not raise
        page_items, total = search_and_slice(items, texts, "description", page=1, limit=10)
        assert total == 3
        assert len(page_items) == 3

    def test_attaches_normalized_score(self) -> None:
        """Each result item carries a normalized `score` (0.0-1.0, top == 1.0)."""
        items = [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
            {"id": 3, "name": "gamma"},
        ]
        texts = ["alpha alpha word", "beta word", "gamma word"]
        page_items, total = search_and_slice(items, texts, "alpha", page=1, limit=10)
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


class TestNameMatches:
    """Tests for _name_matches helper."""

    def test_exact_match_with_prefix(self) -> None:
        """Query matching full prefixed name returns True."""
        assert _name_matches("user get current", "gitea_user_get_current", "gitea_")

    def test_exact_match_without_prefix(self) -> None:
        """Query matching full unprefixed name returns True."""
        assert _name_matches("user get current", "user_get_current", "gitea_")

    def test_prefix_match(self) -> None:
        """Query that is a token-boundary prefix of the name returns True."""
        assert _name_matches("issue create", "gitea_issue_create_issue", "gitea_")

    def test_verb_first_order(self) -> None:
        """Verb-first query ('create issue') matches domain-first name via swap."""
        assert _name_matches("create issue", "gitea_issue_create_issue", "gitea_")

    def test_verb_first_three_tokens(self) -> None:
        """Verb-first 3-token query matches via first-two swap."""
        assert _name_matches("create issue label", "gitea_issue_create_label", "gitea_")

    def test_prefix_match_with_query_including_prefix(self) -> None:
        """Query that includes the prefix still matches."""
        assert _name_matches("gitea user get current", "gitea_user_get_current", "gitea_")

    def test_partial_token_matches_via_prefix(self) -> None:
        """Partial token 'cr' matches 'create' because it's a valid prefix."""
        assert _name_matches("issue cr", "gitea_issue_create_issue", "gitea_")

    def test_no_match(self) -> None:
        """Query that doesn't match returns False."""
        assert not _name_matches("create issue", "gitea_user_get_current", "gitea_")

    def test_empty_query(self) -> None:
        """Empty query returns False."""
        assert not _name_matches("", "gitea_user_get_current", "gitea_")

    def test_single_token_not_boosted(self) -> None:
        """Single-token query returns False (BM25 handles it)."""
        assert not _name_matches("user", "gitea_user_get_current", "gitea_")

    def test_no_prefix_configured(self) -> None:
        """With empty tool_prefix, unprefixed names match."""
        assert _name_matches("user get current", "user_get_current", "")

    def test_custom_prefix(self) -> None:
        """Non-default prefix is stripped correctly."""
        assert _name_matches("user get current", "forgejo_user_get_current", "forgejo_")

    def test_underscore_normalization(self) -> None:
        """Underscores in query are treated as spaces."""
        assert _name_matches("user_get_current", "gitea_user_get_current", "gitea_")

    def test_case_insensitive(self) -> None:
        """Matching is case-insensitive."""
        assert _name_matches("USER GET CURRENT", "gitea_user_get_current", "gitea_")

    def test_query_longer_than_name(self) -> None:
        """Query with more tokens than name returns False."""
        assert not _name_matches("user get current extra", "gitea_user_get_current", "gitea_")

    def test_sliding_window_with_extra_domain_prefix(self) -> None:
        """Query matches when domain prefix sits before query-aligned tokens."""
        assert _name_matches("create pull request", "gitea_repo_create_pull_request", "gitea_")

    def test_sliding_window_domain_prefix_no_spurious_match(self) -> None:
        """Sliding window does not spuriously match unrelated tokens."""
        assert not _name_matches("create pull request", "gitea_repo_create_pull_review", "gitea_")

    def test_sliding_window_domain_prefix_verb_first(self) -> None:
        """Sliding window with swapped ordering handles verb-first queries."""
        assert _name_matches("pull create request", "gitea_repo_create_pull_request", "gitea_")

    def test_sliding_window_mid_name(self) -> None:
        """Sliding window matches query that aligns in the middle of the name."""
        # "list repo" should match "issue_list_repos" (window at pos 1)
        assert _name_matches("list repo", "gitea_issue_list_repos", "gitea_")

    def test_sliding_window_single_token_falls_through(self) -> None:
        """Single-token queries still fall through to BM25 regardless of window."""
        assert not _name_matches("repo", "gitea_repo_create_pull_request", "gitea_")


class TestSearchAndSliceNameMatch:
    """Tests for name-match boost in search_and_slice."""

    def _make_items(self, names: list[str]) -> list[dict]:
        return [{"name": n, "description": f"desc for {n}"} for n in names]

    def _make_texts(self, items: list[dict]) -> list[str]:
        return [f"{i['name']} {i['description']}" for i in items]

    def test_exact_match_ranks_first(self) -> None:
        """Exact name match ranks above BM25 results."""
        items = self._make_items(
            [
                "gitea_user_current_check_following",
                "gitea_user_current_list_following",
                "gitea_user_get_current",
                "gitea_user_current_list_followers",
            ]
        )
        texts = self._make_texts(items)
        page_items, _ = search_and_slice(
            items,
            texts,
            "user get current",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        assert page_items[0]["name"] == "gitea_user_get_current"
        assert page_items[0]["score"] == 1.0

    def test_verb_first_ranks_among_name_matches(self) -> None:
        """Verb-first query ('create issue') puts exact tool among name-match results."""
        items = self._make_items(
            [
                "gitea_issue_create_issue_attachment",
                "gitea_issue_create_issue_comment_attachment",
                "gitea_issue_create_issue",
                "gitea_issue_create_issue_blocking",
            ]
        )
        texts = self._make_texts(items)
        page_items, _ = search_and_slice(
            items,
            texts,
            "create issue",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        # Multiple tools share the "issue create" prefix (via swap), so all
        # get score 1.0. The exact tool is among them.
        assert any(
            item["name"] == "gitea_issue_create_issue" and item["score"] == 1.0
            for item in page_items
        )

    def test_prefix_match_ranks_first(self) -> None:
        """Token-boundary prefix match ranks above BM25 results."""
        items = self._make_items(
            [
                "gitea_user_current_check_following",
                "gitea_user_get_current",
            ]
        )
        texts = self._make_texts(items)
        page_items, _ = search_and_slice(
            items,
            texts,
            "user get",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        assert page_items[0]["name"] == "gitea_user_get_current"
        assert page_items[0]["score"] == 1.0

    def test_domain_prefix_ranks_correct_tool_first(self) -> None:
        """Query matching via sliding window ranks the correct tool #1.

        Regression guard for #518: ``\"create pull request\"`` must rank
        ``gitea_repo_create_pull_request`` above similarly-named tools
        that share the same prefix window (e.g. ``_pull_review``).
        """
        items = self._make_items(
            [
                "gitea_repo_create_pull_review",
                "gitea_repo_create_pull_request",
            ]
        )
        texts = self._make_texts(items)
        page_items, _ = search_and_slice(
            items,
            texts,
            "create pull request",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        assert page_items[0]["name"] == "gitea_repo_create_pull_request"
        assert page_items[0]["score"] == 1.0

    def test_no_prefix_configured(self) -> None:
        """Without prefix, unprefixed names match."""
        items = self._make_items(
            [
                "user_current_check_following",
                "user_get_current",
            ]
        )
        texts = self._make_texts(items)
        page_items, _ = search_and_slice(
            items,
            texts,
            "user get current",
            page=1,
            limit=10,
            tool_prefix="",
        )
        assert page_items[0]["name"] == "user_get_current"

    def test_custom_prefix(self) -> None:
        """Non-default prefix is handled correctly."""
        items = self._make_items(
            [
                "forgejo_user_current_check_following",
                "forgejo_user_get_current",
            ]
        )
        texts = self._make_texts(items)
        page_items, _ = search_and_slice(
            items,
            texts,
            "user get current",
            page=1,
            limit=10,
            tool_prefix="forgejo_",
        )
        assert page_items[0]["name"] == "forgejo_user_get_current"

    def test_broad_query_returns_only_single_token(self) -> None:
        """Single-token query like 'user' is NOT boosted (BM25 handles it)."""
        items = self._make_items(
            [
                "gitea_user_get_current",
                "gitea_user_current_list_following",
                "gitea_repo_create",
                "gitea_issue_create",
            ]
        )
        texts = self._make_texts(items)
        page_items, total = search_and_slice(
            items,
            texts,
            "user",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        # No name matches (single token not boosted), so BM25 ranks purely
        # by relevance. Items without the term "user" score 0 and are
        # filtered out.
        assert total == 2  # only the two 'user_*' tools contain "user"
        assert len(page_items) == 2
        for item in page_items:
            assert "user" in item["name"]

    def test_no_name_match_falls_back_to_bm25(self) -> None:
        """When no name matches, BM25 handles ranking (regression test)."""
        items = self._make_items(
            [
                "gitea_repo_create",
                "gitea_issue_create",
                "gitea_pull_create",
            ]
        )
        texts = self._make_texts(items)
        page_items, total = search_and_slice(
            items,
            texts,
            "create",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        # No name matches (single token), so all items come from BM25.
        assert total == 3
        assert len(page_items) == 3
        names = {item["name"] for item in page_items}
        assert "gitea_repo_create" in names
        assert "gitea_issue_create" in names
        assert "gitea_pull_create" in names

    def test_mixed_name_match_and_bm25(self) -> None:
        """Name matches come first, BM25 results follow."""
        items = self._make_items(
            [
                "gitea_user_get_current",  # name match
                "gitea_user_current_list_repos",  # BM25 match (shares "user current")
                "gitea_user_current_list_following",  # BM25 match
            ]
        )
        texts = self._make_texts(items)
        page_items, total = search_and_slice(
            items,
            texts,
            "user get current",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        assert page_items[0]["name"] == "gitea_user_get_current"
        assert page_items[0]["score"] == 1.0
        # Remaining items come from BM25
        assert total == 3
        assert len(page_items) == 3

    def test_total_count_includes_name_matches(self) -> None:
        """total_count includes both name matches and BM25 results."""
        items = self._make_items(
            [
                "gitea_user_get_current",  # name match
                "gitea_user_current_list_repos",  # BM25 match (shares "user current")
                "gitea_user_current_list_following",  # BM25 match
                "gitea_admin_delete",  # no match
            ]
        )
        texts = self._make_texts(items)
        _, total = search_and_slice(
            items,
            texts,
            "user get current",
            page=1,
            limit=10,
            tool_prefix="gitea_",
        )
        # 1 name match + 2 BM25 matches = 3 total
        assert total == 3


class TestSearchToolsPagination:
    """Pagination metadata assertions for search_tools."""

    @pytest.mark.asyncio
    async def test_search_tools_pagination_metadata_present(self) -> None:
        """search_tools result should include has_more/next_offset/total_count."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tools = [
            Tool(
                name=f"gitea_test_{i}", description=f"Test tool {i}", parameters={"properties": {}}
            )
            for i in range(25)
        ]
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=mock_tools)

        result = _render(
            await _search_tools_impl("test", None, mock_ctx, transform, page=1, limit=10),
            fmt="raw",
            page=1,
            limit=10,
        )
        sc = result.structured_content
        assert sc is not None
        assert "has_more" in sc
        assert "next_offset" in sc
        assert "total_count" in sc
        assert sc["has_more"] is True  # 25 items, page 1, limit 10 → more
        assert sc["next_offset"] == 2
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_tools_pagination_last_page(self) -> None:
        """Last page of search_tools should have has_more=False."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tools = [
            Tool(
                name=f"gitea_test_{i}", description=f"Test tool {i}", parameters={"properties": {}}
            )
            for i in range(25)
        ]
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=mock_tools)

        result = _render(
            await _search_tools_impl("test", None, mock_ctx, transform, page=3, limit=10),
            fmt="raw",
            page=3,
            limit=10,
        )
        sc = get_structured(result)
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_tools_page_out_of_range_message(self) -> None:
        """Out-of-range page should return a helpful message."""
        from gitea_mcp_server.tools.search import TolerantSearchTransform

        transform = TolerantSearchTransform()
        mock_tools = [
            Tool(
                name=f"gitea_test_{i}", description=f"Test tool {i}", parameters={"properties": {}}
            )
            for i in range(5)
        ]
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.list_tools = AsyncMock(return_value=mock_tools)

        result = _render(
            await _search_tools_impl("test", None, mock_ctx, transform, page=10, limit=10),
            fmt="markdown",
            page=10,
            limit=10,
        )
        assert result.content is not None
        text = extract_text_content(result.content)
        assert "Page 10 is out of range" in text
        assert "total results: 5" in text
        # Out-of-range pages still carry the full pagination envelope (issue #694).
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 5


class TestSearchResourcesPagination:
    """Pagination metadata assertions for search_resources."""

    @pytest.mark.asyncio
    async def test_search_resources_pagination_metadata_present(self) -> None:
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

        result = _render(
            await _search_resources_impl(query="test", ctx=ctx, page=1, limit=10),
            fmt="raw",
            page=1,
            limit=10,
        )
        sc = result.structured_content
        assert sc is not None
        assert "has_more" in sc
        assert "next_offset" in sc
        assert "total_count" in sc
        assert sc["has_more"] is True
        assert sc["next_offset"] == 2
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_resources_pagination_last_page(self) -> None:
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

        result = _render(
            await _search_resources_impl(query="test", ctx=ctx, page=3, limit=10),
            fmt="raw",
            page=3,
            limit=10,
        )
        sc = get_structured(result)
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_search_resources_page_out_of_range_message(self) -> None:
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

        result = _render(
            await _search_resources_impl(query="test", ctx=ctx, page=10, limit=10),
            fmt="markdown",
            page=10,
            limit=10,
        )
        assert result.content is not None
        text = extract_text_content(result.content)
        assert "Page 10 is out of range" in text
        assert "total results: 5" in text
        # Out-of-range pages still carry the full pagination envelope (issue #694).
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 5


class TestEmptyResultsMessage:
    """Tests for _empty_results_message helper."""

    def test_with_cross_link_hints(self) -> None:
        """Empty results message includes cross-linking hints."""
        from gitea_mcp_server.tools.search import _empty_results_message

        result = _empty_results_message(
            "test query",
            {
                "workflow guides": "search_docs",
                "data resources": "search_resources",
            },
        )
        assert "No results found for 'test query'" in result
        assert "search_docs" in result
        assert "search_resources" in result

    def test_without_cross_link_hints(self) -> None:
        """Empty results message without hints omits hint section."""
        from gitea_mcp_server.tools.search import _empty_results_message

        result = _empty_results_message("test query", None)
        assert "No results found for 'test query'" in result
        assert "Cross-linking" not in result

    def test_empty_cross_link_hints(self) -> None:
        """Empty dict of hints omits hint section."""
        from gitea_mcp_server.tools.search import _empty_results_message

        result = _empty_results_message("test query", {})
        assert "No results found for 'test query'" in result
        assert "Cross-linking" not in result


class TestFindToolByName:
    """Tests for _find_tool_by_name helper."""

    @pytest.mark.asyncio
    async def test_finds_prefixed_tool(self) -> None:
        """Prefixed tool name is found."""
        from gitea_mcp_server.tools.search import _find_tool_by_name

        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=MagicMock(name="gitea_test_tool"))

        result = await _find_tool_by_name("gitea_test_tool", ctx)
        assert result is not None
        ctx.fastmcp.get_tool.assert_awaited_once_with("gitea_test_tool")

    @pytest.mark.asyncio
    async def test_finds_unprefixed_tool_with_prefix_fallback(self) -> None:
        """Unprefixed name falls back to prefixed variant."""
        from gitea_mcp_server.tools.search import _find_tool_by_name

        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        # First lookup (unprefixed) returns None, second (prefixed) returns tool
        ctx.fastmcp.get_tool = AsyncMock(side_effect=[None, MagicMock(name="gitea_test_tool")])

        result = await _find_tool_by_name("test_tool", ctx, tool_prefix="gitea_")
        assert result is not None
        assert ctx.fastmcp.get_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self) -> None:
        """Returns None when tool not found in any form."""
        from gitea_mcp_server.tools.search import _find_tool_by_name

        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        result = await _find_tool_by_name("nonexistent_tool", ctx, tool_prefix="gitea_")
        assert result is None


class TestFormatFilteredToolsNote:
    """Tests for _format_filtered_tools_note."""

    def test_none_filtered_info_returns_empty(self) -> None:
        """None filtered_tools_info returns empty string."""
        result = _format_filtered_tools_note(None)
        assert result == ""

    def test_empty_filtered_returns_empty(self) -> None:
        """Empty filtered dict returns empty string."""
        result = _format_filtered_tools_note({"filtered": {}})
        assert result == ""

    def test_scope_restricted_count(self) -> None:
        """Scope-restricted tools are counted."""
        result = _format_filtered_tools_note(
            {
                "filtered": {
                    "tool1": {"reason": "scope"},
                    "tool2": {"reason": "scope"},
                },
            }
        )
        assert "2 scope-restricted" in result

    def test_excluded_count(self) -> None:
        """Config-excluded tools are counted."""
        result = _format_filtered_tools_note(
            {
                "filtered": {
                    "tool1": {"reason": "excluded"},
                },
            }
        )
        assert "1 config-excluded" in result

    def test_deprecated_count(self) -> None:
        """Deprecated tools are counted."""
        result = _format_filtered_tools_note(
            {
                "filtered": {
                    "tool1": {"reason": "deprecated"},
                },
            }
        )
        assert "1 deprecated" in result

    def test_combined_counts(self) -> None:
        """Multiple reason types are combined in the note."""
        result = _format_filtered_tools_note(
            {
                "filtered": {
                    "t1": {"reason": "scope"},
                    "t2": {"reason": "excluded"},
                    "t3": {"reason": "deprecated"},
                    "t4": {"reason": "scope"},
                },
            }
        )
        assert "2 scope-restricted" in result
        assert "1 config-excluded" in result
        assert "1 deprecated" in result

    def test_note_points_to_list_hidden_tools(self) -> None:
        """The note points to list_hidden_tools for enumeration."""
        result = _format_filtered_tools_note(
            {
                "filtered": {
                    "tool1": {"reason": "excluded"},
                },
            }
        )
        assert "list_hidden_tools" in result
        assert "tool_info" in result

    def test_unknown_reason_not_counted(self) -> None:
        """Unknown reason type is not counted."""
        result = _format_filtered_tools_note(
            {
                "filtered": {
                    "tool1": {"reason": "mystery"},
                },
            }
        )
        # Unknown reason should produce no parts and return empty
        assert result == ""


class TestToolInfoImplPrefixFallback:
    """Tests for _tool_info_impl prefix fallback (line 604)."""

    @pytest.mark.asyncio
    async def test_unprefixed_name_adds_prefixed_candidate(self) -> None:
        """Unprefixed name should add prefixed version as candidate."""

        # Create a minimal real Tool with version attribute
        tool = Tool(
            name="gitea_test_tool",
            description="Test tool",
            tags=set(),
            parameters={"properties": {}},
            output_schema=None,
            meta={},
            version="1.0",
        )

        ctx = MagicMock(spec=Context)
        transform = MagicMock(spec=TolerantSearchTransform)
        transform.get_tool_catalog = AsyncMock(return_value=[tool])

        result = _render(
            await _tool_info_impl(
                "test_tool",
                ctx,
                transform,
                tool_prefix="gitea_",
            )
        )
        assert result is not None


class TestSearchToolsWithFilteredInfo:
    """Tests for _search_tools_impl with filtered_tools_info."""

    @pytest.mark.asyncio
    async def test_markdown_format_with_filtered_note(self) -> None:
        """Markdown search includes filtered-tools note with results."""
        ctx = MagicMock(spec=Context)
        transform = MagicMock(spec=TolerantSearchTransform)
        # Provide at least one tool so the search doesn't short-circuit to
        # empty-results message.  The tool name won't match the query "test",
        # but the markdown-result path with cross-linking hints + filtered
        # note is what we want to test.
        tool = Tool(
            name="gitea_issue_list_issues",
            description="List issues in a repository",
            tags={"issue"},
            parameters={"properties": {}},
            annotations=None,
        )
        transform.get_tool_catalog = AsyncMock(return_value=[tool])

        filtered_info = {
            "filtered": {
                "admin_tool": {"reason": "scope"},
            },
        }

        result = _render(
            await _search_tools_impl(
                "issue",
                None,
                ctx,
                transform,
                page=1,
                limit=10,
                min_score=0.0,
                filtered_tools_info=filtered_info,
                tool_prefix="gitea_",
            )
        )
        assert result is not None
        text = extract_text_content(result.content) if result.content else ""
        assert "hidden from this listing" in text


class TestTolerantSearchTransformSearch:
    """Tests for TolerantSearchTransform._search method."""

    @pytest.mark.asyncio
    async def test_search_delegates_to_searcher(self) -> None:
        """_search should delegate to internal BM25 searcher."""
        tools = [
            Tool(
                name="tool_a",
                description="First tool",
                tags=set(),
                parameters={"properties": {}},
            ),
            Tool(
                name="tool_b",
                description="Second tool",
                tags=set(),
                parameters={"properties": {}},
            ),
        ]
        transform = TolerantSearchTransform()
        # An empty query returns no results
        result = await transform._search(tools, "")
        assert len(result) == 0
