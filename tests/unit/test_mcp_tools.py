"""Tests for MCP resource tools."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.context import Context

from gitea_mcp_server.mcp_tools import (
    _mcp_list_resources_impl,
    _mcp_read_resource_impl,
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

    def test_registers_two_tools(self):
        """Should register exactly two tools."""
        mcp = MagicMock()
        mcp.tool = MagicMock()

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 2

    def test_tool_decorators_applied(self):
        """Should apply @mcp.tool() decorator to both functions."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 2
