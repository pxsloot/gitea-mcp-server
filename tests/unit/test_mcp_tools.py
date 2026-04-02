"""Tests for MCP resource tools."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.mcp_tools import (
    _mcp_list_resources_impl,
    _mcp_read_resource_impl,
    register_mcp_resource_tools,
)


class TestMcpListResourcesImpl:
    """Tests for _mcp_list_resources_impl function."""

    def test_returns_resources_and_count(self):
        """Should return dict with resources list and count."""
        mcp = MagicMock()
        resource_mock = MagicMock()
        resource_mock.name = "Test Resource"
        resource_mock.description = "Test description"
        resource_mock.mime_type = "text/plain"
        mcp._resources = {"gitea://test": resource_mock}
        mcp._resource_templates = {}

        import asyncio

        result = asyncio.run(_mcp_list_resources_impl(mcp))

        assert "resources" in result
        assert "count" in result
        assert result["count"] == 1

    def test_includes_resource_metadata(self):
        """Should include URI, name, description, mimeType."""
        mcp = MagicMock()
        resource_mock = MagicMock()
        resource_mock.name = "Repo Info"
        resource_mock.description = "Repository information"
        resource_mock.mime_type = "text/markdown"
        mcp._resources = {"gitea://repo": resource_mock}
        mcp._resource_templates = {}

        import asyncio

        result = asyncio.run(_mcp_list_resources_impl(mcp))

        resource = result["resources"][0]
        assert resource["uri"] == "gitea://repo"
        assert resource["name"] == "Repo Info"
        assert resource["description"] == "Repository information"
        assert resource["mimeType"] == "text/markdown"

    def test_includes_templates_with_isTemplate_flag(self):
        """Should include resource templates with isTemplate=True."""
        mcp = MagicMock()
        template_mock = MagicMock()
        template_mock.name = "Template Resource"
        template_mock.description = "Template description"
        template_mock.mime_type = "application/json"
        mcp._resources = {}
        mcp._resource_templates = {"gitea://template/{id}": template_mock}

        import asyncio

        result = asyncio.run(_mcp_list_resources_impl(mcp))

        resource = result["resources"][0]
        assert resource["uri"] == "gitea://template/{id}"
        assert resource["isTemplate"] is True

    def test_handles_empty_registries(self):
        """Should handle empty resource registries."""
        mcp = MagicMock()
        mcp._resources = {}
        mcp._resource_templates = {}

        import asyncio

        result = asyncio.run(_mcp_list_resources_impl(mcp))

        assert result["resources"] == []
        assert result["count"] == 0

    def test_handles_missing_attributes(self):
        """Should handle resources with missing optional attributes."""
        mcp = MagicMock()
        # Create mock with no explicit attributes - getattr will return MagicMock
        # So we need to configure it to raise AttributeError or return None
        resource_mock = MagicMock(spec=[])  # Empty spec means no attributes
        mcp._resources = {"gitea://minimal": resource_mock}
        mcp._resource_templates = {}

        import asyncio

        result = asyncio.run(_mcp_list_resources_impl(mcp))

        resource = result["resources"][0]
        assert resource["uri"] == "gitea://minimal"
        # With spec=[], getattr will still return MagicMock for non-existent attrs
        # To simulate missing, we need to use hasattr check or configure __getattr__
        # Let's explicitly delete the attributes to ensure they don't exist
        del resource_mock.name
        del resource_mock.description
        del resource_mock.mime_type

        # Re-run after deletion
        result = asyncio.run(_mcp_list_resources_impl(mcp))
        resource = result["resources"][0]
        assert resource["name"] == "gitea://minimal"  # Falls back to URI
        assert resource["description"] == ""
        assert resource["mimeType"] is None


class TestMcpReadResourceImpl:
    """Tests for _mcp_read_resource_impl function."""

    @pytest.mark.asyncio
    async def test_reads_resource_with_mcp_read_resource(self):
        """Should use mcp.read_resource if available."""
        mcp = MagicMock()
        mcp.read_resource = AsyncMock(return_value=("Hello World", "text/plain"))

        result = await _mcp_read_resource_impl(mcp, "gitea://test")

        assert result == "Hello World"
        mcp.read_resource.assert_awaited_once_with("gitea://test")

    @pytest.mark.asyncio
    async def test_fallback_to_direct_resource_lookup(self):
        """Should fall back to direct lookup if read_resource not available."""
        # Create a mock that raises AttributeError for hasattr check
        mcp = MagicMock(spec=[])  # Empty spec means no attributes by default
        resource_func = AsyncMock(return_value="Direct content")
        mcp._resources = {"gitea://test": resource_func}
        mcp._resource_templates = {}

        result = await _mcp_read_resource_impl(mcp, "gitea://test")

        assert result == "Direct content"

    @pytest.mark.asyncio
    async def test_raises_for_missing_resource(self):
        """Should raise ValueError for non-existent resource."""
        mcp = MagicMock(spec=[])
        mcp._resources = {}
        mcp._resource_templates = {}

        with pytest.raises(ValueError, match="Resource not found"):
            await _mcp_read_resource_impl(mcp, "gitea://nonexistent")

    @pytest.mark.asyncio
    async def test_raises_for_template_uri_in_fallback(self):
        """Should raise ValueError for template URIs in fallback mode (no parameter parsing)."""
        mcp = MagicMock(spec=[])
        mcp._resources = {}
        mcp._resource_templates = {"gitea://template/{id}": MagicMock()}

        # In fallback mode, template URIs aren't matched (requires read_resource)
        with pytest.raises(ValueError, match="Resource not found"):
            await _mcp_read_resource_impl(mcp, "gitea://template/something")

    @pytest.mark.asyncio
    async def test_raises_on_exception(self):
        """Should wrap any exception in ValueError."""
        mcp = MagicMock()
        mcp.read_resource = AsyncMock(side_effect=RuntimeError("Connection failed"))

        with pytest.raises(ValueError, match="Error reading resource"):
            await _mcp_read_resource_impl(mcp, "gitea://test")


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
        mcp.tool = MagicMock(return_value=lambda f: f)  # Decorator returns function unchanged

        register_mcp_resource_tools(mcp)

        # The decorator should be called twice
        assert mcp.tool.call_count == 2
