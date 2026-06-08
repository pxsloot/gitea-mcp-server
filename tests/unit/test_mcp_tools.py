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
        template_mock.meta = {"fastmcp": {"_internal": {"required_scope": "read:repository"}}}

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
        resource_mock.meta = {"fastmcp": {"_internal": {"required_scope": None}}}

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

        result_str = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert result_str == "Hello World"
        ctx.read_resource.assert_awaited_once_with("gitea://test")

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

        result_str = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert result_str == "Hello Bytes"
        ctx.read_resource.assert_awaited_once_with("gitea://test")


class TestRegisterMcpResourceTools:
    """Tests for register_mcp_resource_tools function."""

    def test_registers_three_tools(self):
        """Should register exactly three tools."""
        mcp = MagicMock()
        mcp.tool = MagicMock()

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 3

    def test_tool_decorators_applied(self):
        """Should apply @mcp.tool() decorator to all functions."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 3

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

    def test_search_resources_has_openworld_false(self):
        """search_resources should have openWorldHint=False."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        call_kwargs = mcp.tool.call_args_list[2][1]
        assert call_kwargs.get("name") == "search_resources"
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
        assert "key" in result_text
        assert "val" in result_text
        assert "num" in result_text
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
        assert "| id | 1 |" in result
        assert "| label | a |" in result

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


class TestMcpSearchResources:
    """Tests for search_resources tool, including URI-in-search-text."""

    def _capture_search_resources(self):
        """Register resource tools and return the search_resources function."""
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
        fn = captured["search_resources"]
        assert fn is not None
        return fn

    @pytest.mark.asyncio
    async def test_searches_resource_by_uri(self):
        """Resource URI should be searchable via search_resources."""
        fn = self._capture_search_resources()
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

        result = await fn(query="wiki", ctx=ctx)

        assert result.structured_content is not None
        results = result.structured_content["result"]
        assert len(results) == 1
        assert results[0]["uri"] == "gitea://wiki/guide"

    @pytest.mark.asyncio
    async def test_searches_resource_by_name(self):
        """Resource name should still be searchable (baseline check)."""
        fn = self._capture_search_resources()
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

        result = await fn(query="version", ctx=ctx)

        assert result.structured_content is not None
        results = result.structured_content["result"]
        assert len(results) == 1
        assert results[0]["name"] == "Server Version"

    @pytest.mark.asyncio
    async def test_markdown_includes_cross_link_footer(self):
        """Markdown output should include cross-linking hints footer."""
        fn = self._capture_search_resources()
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

        result = await fn(query="version", ctx=ctx)

        assert result.content is not None
        text = result.content[0].text
        assert "Cross-linking hints" in text
        assert "search_docs" in text
        assert "search_tools" in text

    @pytest.mark.asyncio
    async def test_empty_result_has_helpful_hint(self):
        """Empty search results should include helpful cross-linking message."""
        fn = self._capture_search_resources()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await fn(query="nothing", ctx=ctx)

        assert result.content is not None
        text = result.content[0].text
        assert "No resources found" in text
        assert "search_docs" in text
        assert "search_tools" in text
        assert result.structured_content is not None
        assert result.structured_content["result"] == []
