"""Tests for MCP resource tools."""

import json as json_module
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.context import Context
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.mcp_tools import (
    _mcp_list_resources_impl,
    _mcp_read_resource_impl,
    _format_resource_content,
    register_mcp_resource_tools,
)


class TestMcpListResourcesImpl:
    """Tests for _mcp_list_resources_impl function."""

    @pytest.mark.asyncio
    async def test_returns_resources_and_count(self):
        """Should return dict with resources list and count from resource manager."""
        # Create mock Context with fastmcp.list_resources() and list_resource_templates()
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "Test Resource"
        resource_mock.description = "Test description"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()

        # Mock list_resources to return an async list with one resource
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _mcp_list_resources_impl(ctx)

        assert "resources" in result
        assert "count" in result
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_includes_resource_metadata(self):
        """Should include URI, name, description, mimeType."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://repo"
        resource_mock.name = "Repo Info"
        resource_mock.description = "Repository information"
        resource_mock.mime_type = "text/markdown"
        resource_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["uri"] == "gitea://repo"
        assert resource["name"] == "Repo Info"
        assert resource["description"] == "Repository information"
        assert resource["mimeType"] == "text/markdown"

    @pytest.mark.asyncio
    async def test_includes_templates(self):
        """Should include resource templates (parameterized URIs)."""
        ctx = MagicMock(spec=Context)
        template_mock = MagicMock()
        template_mock.uri_template = "gitea://repos/{owner}/{repo}"
        template_mock.name = "Repository"
        template_mock.description = "Repository metadata"
        template_mock.mime_type = "text/markdown"
        template_mock.tags = set()
        # fn not needed since name is provided

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[template_mock])

        result = await _mcp_list_resources_impl(ctx)

        assert result["count"] == 1
        resource = result["resources"][0]
        assert resource["uri"] == "gitea://repos/{owner}/{repo}"
        assert resource["name"] == "Repository"
        assert resource["type"] == "template"

    @pytest.mark.asyncio
    async def test_includes_both_resources_and_templates(self):
        """Should include both concrete resources and templates."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://static"
        resource_mock.name = "Static Resource"
        resource_mock.description = "A concrete resource"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()

        template_mock = MagicMock()
        template_mock.uri_template = "gitea://dynamic/{id}"
        template_mock.name = "Dynamic Template"
        template_mock.description = "A parameterized template"
        template_mock.mime_type = "text/markdown"
        template_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[template_mock])

        result = await _mcp_list_resources_impl(ctx)

        assert result["count"] == 2
        uris = [r["uri"] for r in result["resources"]]
        assert "gitea://static" in uris
        assert "gitea://dynamic/{id}" in uris
        # Check types
        types = {r["type"] for r in result["resources"]}
        assert "resource" in types
        assert "template" in types

    @pytest.mark.asyncio
    async def test_handles_empty_list(self):
        """Should handle empty resource list."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _mcp_list_resources_impl(ctx)

        assert result["resources"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_handles_missing_description(self):
        """Should handle resources with None description."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "Test"
        resource_mock.description = None
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["description"] == ""

    @pytest.mark.asyncio
    async def test_includes_required_scope_from_template_meta(self):
        """Should include required_scope from template meta."""
        ctx = MagicMock(spec=Context)
        template_mock = MagicMock()
        template_mock.uri_template = "gitea://repos/{owner}/{repo}"
        template_mock.name = "Repository"
        template_mock.description = "Repository metadata"
        template_mock.mime_type = "text/markdown"
        template_mock.tags = set()
        template_mock.meta = {"required_scope": "read:repository"}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[template_mock])

        result = await _mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["required_scope"] == "read:repository"

    @pytest.mark.asyncio
    async def test_includes_required_scope_from_resource_meta(self):
        """Should include required_scope from concrete resource meta."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://version"
        resource_mock.name = "Version"
        resource_mock.description = "Server version"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()
        resource_mock.meta = {"required_scope": None}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["required_scope"] is None

    @pytest.mark.asyncio
    async def test_required_scope_is_none_when_no_meta(self):
        """Should return None for required_scope when meta is absent."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "Test"
        resource_mock.description = "Test"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()
        resource_mock.meta = None

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["required_scope"] is None

    @pytest.mark.asyncio
    async def test_handles_missing_name_and_mime_type(self):
        """Should fall back to function name and default mime type."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "my_resource_func"
        resource_mock.description = "Test resource"
        resource_mock.mime_type = None
        resource_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await _mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["name"] == "my_resource_func"
        assert resource["mimeType"] == "text/plain"


class TestMcpReadResourceImpl:
    """Tests for _mcp_read_resource_impl function."""

    @pytest.mark.asyncio
    async def test_reads_resource_success(self):
        """Should read resource content via ctx.read_resource."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        # ctx.read_resource returns a ResourceResult (FastMCP 3.x)
        content_part = ResourceContent("Hello World")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        result = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert result == ("Hello World", None, None, None)
        ctx.read_resource.assert_awaited_once_with("gitea://test")

    @pytest.mark.asyncio
    async def test_extracts_meta_from_content(self):
        """Should extract schema, format_hint, and extra from content meta."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent(
            '{"key": "val"}',
            meta={
                "response_schema": {"type": "object"},
                "format_hint": "repository",
                "custom_key": "custom_val",
            },
        )
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert raw == '{"key": "val"}'
        assert schema == {"type": "object"}
        assert format_hint == "repository"
        assert extra == {"custom_key": "custom_val"}

    @pytest.mark.asyncio
    async def test_handles_missing_meta_gracefully(self):
        """Should return None for all meta fields when no meta is present."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent("plain text")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert raw == "plain text"
        assert schema is None
        assert format_hint is None
        assert extra is None

    @pytest.mark.asyncio
    async def test_raises_for_missing_resource(self):
        """Should raise ValueError for non-existent resource."""
        from fastmcp.resources import ResourceResult

        ctx = MagicMock(spec=Context)
        empty_result = ResourceResult(contents=[])
        ctx.read_resource = AsyncMock(return_value=empty_result)

        with pytest.raises(ValueError, match="returned no content"):
            await _mcp_read_resource_impl(ctx, "gitea://nonexistent")

    @pytest.mark.asyncio
    async def test_raises_on_exception(self):
        """Should wrap any exception in ValueError."""
        ctx = MagicMock(spec=Context)
        ctx.read_resource = AsyncMock(side_effect=RuntimeError("Connection failed"))

        with pytest.raises(ValueError, match="Error reading resource"):
            await _mcp_read_resource_impl(ctx, "gitea://test")

    @pytest.mark.asyncio
    async def test_handles_bytes_content(self):
        """Should decode bytes content to string."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent(b"Hello Bytes")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        result = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert result == ("Hello Bytes", None, None, None)
        ctx.read_resource.assert_awaited_once_with("gitea://test")


class TestRegisterMcpResourceTools:
    """Tests for register_mcp_resource_tools function."""

    def test_registers_two_tools(self):
        """Should register exactly two tools (list_resources, read_resource)."""
        mcp = MagicMock()
        mcp.tool = MagicMock()

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 2

    def test_tool_decorators_applied(self):
        """Should apply @mcp.tool() decorator to all functions."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 2

    def test_list_resources_has_openworld_false(self):
        """list_resources should have openWorldHint=False."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        call_kwargs = mcp.tool.call_args_list[0][1]
        assert call_kwargs.get("name") == "list_resources"
        annotations = call_kwargs.get("annotations")
        assert annotations is not None
        assert annotations.openWorldHint is False

    def test_read_resource_has_openworld_true(self):
        """read_resource should have openWorldHint=True (fetches from Gitea API)."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        call_kwargs = mcp.tool.call_args_list[1][1]
        assert call_kwargs.get("name") == "read_resource"
        annotations = call_kwargs.get("annotations")
        assert annotations is not None
        assert annotations.openWorldHint is True


class TestMcpReadResourceTool:
    """Tests for read_resource tool function.

    The tool always returns both content (TextContent with raw text) and
    structured_content (for schema compliance). The key property is that
    content[0].text delivers the resource content as-is without JSON escaping.
    structured_content wraps it in {"result": ...} for tool validation.
    """

    def _capture_read_resource(self):
        """Register resource tools and return the read_resource function."""
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn
            return deco
        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        fn = captured["read_resource"]
        assert fn is not None
        return fn

    @pytest.mark.asyncio
    async def test_non_json_has_raw_text_in_content(self):
        """Non-JSON (markdown/text) content text should be raw in content, not escaped."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent("# Hello\n\nThis is **markdown**")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        tool_result = await fn(uri="gitea://test", format="markdown", ctx=ctx)

        assert isinstance(tool_result, ToolResult)
        assert len(tool_result.content) == 1
        assert tool_result.content[0].text == "# Hello\n\nThis is **markdown**"
        # structured_content is present for schema compliance
        assert tool_result.structured_content is not None
        assert tool_result.structured_content["result"] == "# Hello\n\nThis is **markdown**"

    @pytest.mark.asyncio
    async def test_json_returns_structured_content(self):
        """JSON content should return structured_content with formatted result."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('{"key": "val", "num": 42}')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        tool_result = await fn(uri="gitea://test", format="markdown", ctx=ctx)

        assert isinstance(tool_result, ToolResult)
        assert tool_result.structured_content is not None
        assert "result" in tool_result.structured_content
        result_text = tool_result.structured_content["result"]
        assert "|" in result_text
        assert "Key" in result_text
        assert "val" in result_text
        assert "Num" in result_text
        assert "42" in result_text
        # content is present with text for display
        assert len(tool_result.content) == 1
        assert "|" in tool_result.content[0].text

    @pytest.mark.asyncio
    async def test_raw_format_has_raw_text(self):
        """format=raw should return raw text in content, not processed."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent("raw markdown")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        tool_result = await fn(uri="gitea://test", format="raw", ctx=ctx)

        assert isinstance(tool_result, ToolResult)
        assert len(tool_result.content) == 1
        assert tool_result.content[0].text == "raw markdown"
        assert tool_result.structured_content is not None
        assert tool_result.structured_content["result"] == "raw markdown"

    @pytest.mark.asyncio
    async def test_raw_format_with_json(self):
        """format=raw with JSON content should return raw JSON in content."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('{"key": "val"}')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        tool_result = await fn(uri="gitea://test", format="raw", ctx=ctx)

        assert isinstance(tool_result, ToolResult)
        assert len(tool_result.content) == 1
        assert tool_result.content[0].text == '{"key": "val"}'
        assert tool_result.structured_content is not None
        assert tool_result.structured_content["result"] == '{"key": "val"}'

    @pytest.mark.asyncio
    async def test_non_json_with_json_format(self):
        """Non-JSON content with format=json should wrap in {\"result\": ...}."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent("plain text")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        tool_result = await fn(uri="gitea://test", format="json", ctx=ctx)

        assert isinstance(tool_result, ToolResult)
        assert len(tool_result.content) == 1
        parsed = json_module.loads(tool_result.content[0].text)
        assert parsed == {"result": "plain text"}
        assert tool_result.structured_content is not None
        parsed_result = json_module.loads(tool_result.structured_content["result"])
        assert parsed_result == {"result": "plain text"}


class TestFormatResourceContent:
    """Tests for _format_resource_content helper.

    This is used by read_resource to reformat resource content
    (JSON strings) into markdown, json, or raw output.
    """

    def test_raw_passthrough(self):
        """format=raw should return the string unchanged."""
        assert _format_resource_content("hello world", "raw") == "hello world"

    def test_raw_with_json_input(self):
        """format=raw with JSON input should return the JSON string unchanged."""
        raw = '{"key": "val"}'
        assert _format_resource_content(raw, "raw") is raw

    def test_json_reformats_json_dict(self):
        """format=json with JSON dict input should pretty-print."""
        result = _format_resource_content('{"key": "val", "num": 42}', "json")
        parsed = json_module.loads(result)
        assert parsed == {"key": "val", "num": 42}
        assert '"key": "val"' in result

    def test_json_reformats_json_array(self):
        """format=json with JSON array input should pretty-print."""
        result = _format_resource_content('[{"id": 1}, {"id": 2}]', "json")
        parsed = json_module.loads(result)
        assert parsed == [{"id": 1}, {"id": 2}]

    def test_markdown_reformats_json_dict(self):
        """format=markdown with JSON dict input should produce markdown."""
        result = _format_resource_content('{"name": "test", "count": 3}', "markdown")
        assert "|" in result
        assert "Name" in result or "name" in result

    def test_markdown_reformats_json_array(self):
        """format=markdown with JSON array input should produce markdown table."""
        result = _format_resource_content('[{"id": 1, "label": "a"}]', "markdown")
        assert "| Property | Value |" in result
        assert "| Id | 1 |" in result
        assert "| Label | a |" in result

    def test_non_json_wrapped_in_result_for_json_format(self):
        """format=json with non-JSON content should wrap in {\"result\": ...}."""
        result = _format_resource_content("plain text", "json")
        parsed = json_module.loads(result)
        assert parsed == {"result": "plain text"}

    def test_non_json_passthrough_for_markdown_format(self):
        """format=markdown with non-JSON content should return unchanged."""
        assert _format_resource_content("plain text", "markdown") == "plain text"

    def test_non_json_passthrough_for_raw_format(self):
        """format=raw with non-JSON content should return unchanged."""
        assert _format_resource_content("plain text", "raw") == "plain text"

    def test_unknown_format_with_json_returns_raw(self):
        """Unknown format with valid JSON returns raw string unchanged."""
        assert _format_resource_content('{"key": "val"}', "xml") == '{"key": "val"}'

    # ── detail=concise with schema tests ────────────────────────────────────

    def test_concise_json_collapses_nested_dict(self):
        """detail=concise with schema should collapse $ref objects."""
        raw = '{"name": "test", "owner": {"id": 1, "login": "alice"}}'
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "owner": {"$ref": "#/components/schemas/User"},
            },
        }
        result = _format_resource_content(raw, "json", detail="concise", schema=schema)
        parsed = json_module.loads(result)
        assert parsed["name"] == "test"
        assert parsed["owner"] == "$ref:User"

    def test_concise_json_collapses_nested_list(self):
        """detail=concise with schema should collapse $ref list items."""
        raw = '{"items": [{"id": 1}, {"id": 2}], "name": "test"}'
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Label"},
                },
            },
        }
        result = _format_resource_content(raw, "json", detail="concise", schema=schema)
        parsed = json_module.loads(result)
        assert parsed["name"] == "test"
        assert parsed["items"] == "$ref:Label[2]"

    def test_concise_full_detail_without_schema(self):
        """Without schema, concise should return data unchanged (no collapse)."""
        raw = '{"name": "test", "nested": {"a": 1}}'
        result = _format_resource_content(raw, "json", detail="concise", schema=None)
        parsed = json_module.loads(result)
        assert parsed == {"name": "test", "nested": {"a": 1}}

    def test_concise_markdown_with_schema(self):
        """detail=concise with schema should collapse in markdown output too."""
        raw = '{"name": "test", "owner": {"id": 1, "login": "alice"}}'
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "owner": {"$ref": "#/components/schemas/User"},
            },
        }
        result = _format_resource_content(raw, "markdown", detail="concise", schema=schema)
        # The collapsed $ref:User should appear in the markdown
        assert "$ref:User" in result


class TestMcpListResourcesFormat:
    """Tests that list_resources respects the format parameter.

    Uses a mock FastMCP to capture the tool function, then calls it
    directly with each format to verify structured_content and content.
    """

    @pytest.fixture
    def _mock_resource(self) -> MagicMock:
        """Create a clean mock resource that won't produce MagicMock objects in the output."""
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://version"
        resource_mock.name = "Version"
        resource_mock.description = "Server version"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()
        resource_mock.meta = None  # prevent MagicMock leakage into required_scope
        return resource_mock

    def _capture_tool(self, name: str):
        """Register resource tools and return the named function."""
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn
            return deco
        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        fn = captured[name]
        assert fn is not None
        return fn

    @pytest.mark.asyncio
    async def test_raw_format(self, _mock_resource):
        """format=raw should return ToolResult with structured_content and no content."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[_mock_resource])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await fn(ctx=ctx, format="raw")

        assert isinstance(result, ToolResult)
        assert result.structured_content["result"]["count"] == 1
        assert result.structured_content["result"]["resources"][0]["uri"] == "gitea://version"

    @pytest.mark.asyncio
    async def test_json_format(self, _mock_resource):
        """format=json should produce pretty-printed JSON in content."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[_mock_resource])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await fn(ctx=ctx, format="json")

        assert isinstance(result, ToolResult)
        assert result.structured_content["result"]["count"] == 1
        assert len(result.content) == 1
        parsed = json_module.loads(result.content[0].text)
        assert parsed["count"] == 1
        assert parsed["resources"][0]["uri"] == "gitea://version"

    @pytest.mark.asyncio
    async def test_markdown_format(self, _mock_resource):
        """format=markdown should produce markdown text in content."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[_mock_resource])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await fn(ctx=ctx, format="markdown")

        assert isinstance(result, ToolResult)
        assert result.structured_content["result"]["count"] == 1
        assert len(result.content) == 1
        assert "|" in result.content[0].text
        assert "version" in result.content[0].text.lower()


class TestMcpListResourcesTagTypeFilter:
    """Tests for tag and type filtering in list_resources tool."""

    def _capture_tool(self, name: str):
        """Register resource tools and return the named function."""
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn
            return deco
        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        fn = captured[name]
        assert fn is not None
        return fn

    @pytest.mark.asyncio
    async def test_tag_filter(self):
        """list_resources with tag filter returns only matching resources."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()

        r1 = MagicMock()
        r1.uri = "gitea://repos/owner/repo"
        r1.name = "Repo"
        r1.description = "Repository"
        r1.mime_type = "text/markdown"
        r1.tags = {"wrapper", "repository"}
        r1.meta = None

        r2 = MagicMock()
        r2.uri = "gitea://users/user"
        r2.name = "User"
        r2.description = "User"
        r2.mime_type = "text/markdown"
        r2.tags = {"wrapper", "user"}
        r2.meta = None

        ctx.fastmcp.list_resources = AsyncMock(return_value=[r1, r2])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await fn(ctx=ctx, tag="user")

        assert result.structured_content is not None
        assert result.structured_content["result"]["count"] == 1
        assert result.structured_content["result"]["resources"][0]["uri"] == "gitea://users/user"

    @pytest.mark.asyncio
    async def test_type_filter(self):
        """list_resources with type filter returns only matching type."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()

        tpl = MagicMock()
        tpl.uri_template = "gitea://repos/{owner}/{repo}"
        tpl.name = "Repo"
        tpl.description = "Repo template"
        tpl.mime_type = "text/markdown"
        tpl.tags = {"wrapper"}
        tpl.meta = None

        res = MagicMock()
        res.uri = "gitea://version"
        res.name = "Version"
        res.description = "Version"
        res.mime_type = "text/plain"
        res.tags = {"server"}
        res.meta = None

        ctx.fastmcp.list_resources = AsyncMock(return_value=[res])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[tpl])

        result = await fn(ctx=ctx, type="template")

        assert result.structured_content["result"]["count"] == 1
        assert result.structured_content["result"]["resources"][0]["type"] == "template"

    @pytest.mark.asyncio
    async def test_type_and_tag_filter_combined(self):
        """list_resources with both tag and type filter."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()

        r = MagicMock()
        r.uri = "gitea://version"
        r.name = "Version"
        r.description = "Version"
        r.mime_type = "text/plain"
        r.tags = {"wrapper", "server"}
        r.meta = None

        tpl = MagicMock()
        tpl.uri_template = "gitea://repos/{owner}/{repo}"
        tpl.name = "Repo"
        tpl.description = "Repo"
        tpl.mime_type = "text/markdown"
        tpl.tags = {"wrapper", "repository"}
        tpl.meta = None

        ctx.fastmcp.list_resources = AsyncMock(return_value=[r])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[tpl])

        result = await fn(ctx=ctx, tag="wrapper", type="resource")

        assert result.structured_content["result"]["count"] == 1
        assert result.structured_content["result"]["resources"][0]["uri"] == "gitea://version"


class TestExtractResourceContent:
    """Tests for _extract_resource_content helper."""

    def test_non_bytes_non_str_content(self):
        """Non-bytes, non-string content is converted via str()."""
        from gitea_mcp_server.mcp_tools import _extract_resource_content

        class CustomContent:
            def __str__(self):
                return "custom content"

        result = _extract_resource_content([type("Obj", (), {"content": CustomContent()})()], "gitea://test")
        assert result == "custom content"

    @pytest.mark.asyncio
    async def test_list_resources_impl_exception_handled(self):
        """Exception in _mcp_list_resources_impl returns empty result."""
        from gitea_mcp_server.mcp_tools import _mcp_list_resources_impl

        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(side_effect=AttributeError("no attribute"))
        ctx.fastmcp.list_resource_templates = AsyncMock(side_effect=AttributeError("no attribute"))

        result = await _mcp_list_resources_impl(ctx)
        assert result == {"resources": [], "count": 0}


class TestToolSchemaResource:
    """Tests for _tool_schema_resource."""

    def _capture_resource_fn(self):
        mcp = MagicMock()
        captured: dict[str, object] = {}
        resource_registry: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def resource_decorator(**kwargs):
            def deco(fn):
                resource_registry[fn.__name__] = fn
                return fn
            return deco

        mcp.tool = tool_decorator
        mcp.resource = resource_decorator
        register_mcp_resource_tools(mcp)
        assert "_tool_schema_resource" in resource_registry
        return resource_registry["_tool_schema_resource"]

    @pytest.mark.asyncio
    async def test_returns_full_tool_schema(self):
        """tool/{name}/schema returns full tool schema with params, output, tags."""
        fn = self._capture_resource_fn()
        ctx = MagicMock(spec=Context)
        tool = MagicMock()
        tool.name = "gitea_issue_list"
        tool.description = "List issues"
        tool.parameters = {"properties": {"owner": {"type": "string"}}, "required": ["owner"]}
        tool.output_schema = {
            "type": "object",
            "properties": {
                "result": {"type": "array", "items": {"type": "object"}},
            },
        }
        tool.tags = {"issue"}
        tool.version = "1.0"
        tool.annotations = None
        tool.meta = {}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=tool)

        import json
        result = await fn(name="gitea_issue_list", ctx=ctx)
        data = json.loads(result)
        assert data["name"] == "gitea_issue_list"
        assert data["description"] == "List issues"
        assert "parameters" in data
        assert "output_example" in data
        assert "tags" in data
        assert data["tags"] == ["issue"]
        assert data["version"] == "1.0"

    @pytest.mark.asyncio
    async def test_raises_for_missing_tool(self):
        """tool/{name}/schema raises ValueError for unknown tool."""
        fn = self._capture_resource_fn()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Tool 'unknown_tool' not found"):
            await fn(name="unknown_tool", ctx=ctx)

    @pytest.mark.asyncio
    async def test_handles_missing_output_schema(self):
        """tool/{name}/schema handles None output_schema gracefully."""
        fn = self._capture_resource_fn()
        ctx = MagicMock(spec=Context)
        tool = MagicMock()
        tool.name = "text_tool"
        tool.description = "Text tool"
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.tags = None
        tool.version = None
        tool.annotations = None
        tool.meta = {}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=tool)

        import json
        result = await fn(name="text_tool", ctx=ctx)
        data = json.loads(result)
        assert data["name"] == "text_tool"
        assert "output_example" not in data
        assert "tags" not in data
        assert "version" not in data


class TestMcpListResourcesRawFormat:
    """Tests for list_resources raw format output."""

    def _capture_tool(self):
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn
            return deco
        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        return captured["list_resources"]

    @pytest.mark.asyncio
    async def test_raw_format_has_structured_content(self):
        """format=raw should return ToolResult with structured_content only."""
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        r = MagicMock()
        r.uri = "gitea://version"
        r.name = "Version"
        r.description = "Server version"
        r.mime_type = "text/plain"
        r.tags = set()
        r.meta = None
        ctx.fastmcp.list_resources = AsyncMock(return_value=[r])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await fn(ctx=ctx, format="raw")
        assert result.structured_content is not None
        assert result.structured_content["result"]["count"] == 1
        assert result.structured_content["result"]["resources"][0]["uri"] == "gitea://version"


class TestMcpListResourcesFetchAll:
    """Regression tests for fetch_all parameter in list_resources."""

    def _capture_tool(self):
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn
            return deco
        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        return captured["list_resources"]

    def _make_resource(self, idx: int):
        r = MagicMock()
        r.uri = f"gitea://resource/{idx}"
        r.name = f"Resource {idx}"
        r.description = f"Resource number {idx}"
        r.mime_type = "text/plain"
        r.tags = set()
        r.meta = None
        return r

    @pytest.mark.asyncio
    async def test_fetch_all_with_non_default_page(self):
        """When fetch_all=True, has_more must be False regardless of page arg.

        Regression: if page/limit are not normalized when fetch_all=True,
        add_pagination_metadata computes has_more = page * limit < total_count,
        which is wrong — all items are already in the result.
        """
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        resources = [self._make_resource(i) for i in range(25)]
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        # fetch_all=True with page=3 — a stale page value that would produce
        # incorrect has_more=True if page/limit weren't normalized.
        result = await fn(ctx=ctx, format="raw", page=3, limit=5, fetch_all=True)
        sc = result.structured_content
        assert sc is not None
        assert len(sc["result"]["resources"]) == 25
        assert sc["has_more"] is False, (
            "has_more should be False when fetch_all=True — all items already returned"
        )
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_fetch_all_with_page_1(self):
        """fetch_all + page=1 returns all items with correct metadata."""
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        resources = [self._make_resource(i) for i in range(7)]
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await fn(ctx=ctx, format="raw", page=1, limit=3, fetch_all=True)
        sc = result.structured_content
        assert len(sc["result"]["resources"]) == 7
        assert sc["has_more"] is False
        assert sc["total_count"] == 7
