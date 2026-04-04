"""Unit tests for tool permission filtering."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.tool_filter import _is_admin_tool, filter_tools_by_permissions


class TestIsAdminTool:
    """Tests for the _is_admin_tool helper function."""

    def test_admin_tool_with_admin_tag(self):
        """Tool with 'admin' in tags should be identified as admin."""
        tool = MagicMock()
        tool.tags = {"admin", "user"}
        assert _is_admin_tool(tool) is True

    def test_non_admin_tool_without_admin_tag(self):
        """Tool without 'admin' tag should not be identified as admin."""
        tool = MagicMock()
        tool.tags = {"repository", "issue"}
        assert _is_admin_tool(tool) is False

    def test_tool_without_tags_attribute(self):
        """Tool without tags attribute should not be identified as admin."""
        tool = MagicMock(spec=[])  # No tags attribute
        assert _is_admin_tool(tool) is False

    def test_tool_with_none_tags(self):
        """Tool with tags=None should not be identified as admin."""
        tool = MagicMock()
        tool.tags = None
        assert _is_admin_tool(tool) is False

    def test_tool_with_empty_set(self):
        """Tool with empty set for tags should not be identified as admin."""
        tool = MagicMock()
        tool.tags = set()
        assert _is_admin_tool(tool) is False

    def test_admin_tag_in_different_category(self):
        """Tool with 'admin' as part of another tag (exact match required)."""
        tool = MagicMock()
        tool.tags = {"administration"}  # Not exact 'admin'
        assert _is_admin_tool(tool) is False


class TestFilterToolsByPermissions:
    """Tests for the filter_tools_by_permissions function."""

    @pytest.fixture
    def mock_mcp(self):
        """Create a mock FastMCP server with providers."""
        mcp = MagicMock()
        provider = AsyncMock()
        mcp.providers = [provider]
        return mcp

    @pytest.fixture
    def mock_gitea_client(self):
        """Create a mock GiteaClient."""
        client = MagicMock()
        return client

    def create_tool(self, name: str, tags: set = None):
        """Helper to create a mock tool."""
        tool = MagicMock()
        tool.name = name
        tool.key = name
        tool.tags = tags or set()
        tool.meta = {}
        return tool

    async def test_filters_admin_tools_for_non_admin_user(self, mock_mcp, mock_gitea_client):
        """Non-admin user should have admin tools filtered out."""
        # Mock user fetch - non-admin
        mock_gitea_client.request = AsyncMock(return_value={"admin": False, "login": "user"})

        # Mock provider tools: mixed admin and non-admin
        admin_tool1 = self.create_tool("admin_users_list", tags={"admin"})
        admin_tool2 = self.create_tool("admin_orgs_create", tags={"admin"})
        repo_tool = self.create_tool("repo_list", tags={"repository"})
        user_tool = self.create_tool("user_get_current", tags={"user"})

        mock_mcp.providers[0].list_tools = AsyncMock(
            return_value=[admin_tool1, admin_tool2, repo_tool, user_tool]
        )

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        # Check that admin tools are disabled
        assert admin_tool1.meta["fastmcp"]["_internal"]["visibility"] is False
        assert admin_tool2.meta["fastmcp"]["_internal"]["visibility"] is False
        # Non-admin tools remain visible (no visibility set or True)
        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})
        assert "visibility" not in user_tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_no_filtering_for_admin_user(self, mock_mcp, mock_gitea_client):
        """Admin user should have all tools visible."""
        mock_gitea_client.request = AsyncMock(return_value={"admin": True, "login": "admin"})

        tools = [
            self.create_tool("admin_users_list", tags={"admin"}),
            self.create_tool("repo_list", tags={"repository"}),
        ]
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=tools)

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        # No tool should be disabled
        for tool in tools:
            assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_tools_without_tags_remain_visible(self, mock_mcp, mock_gitea_client):
        """Tools without tags should not be filtered (even if name suggests admin)."""
        mock_gitea_client.request = AsyncMock(return_value={"admin": False, "login": "user"})

        # Create tool that has "admin" in name but no tag (unlikely but possible)
        tool = self.create_tool("admin_legacy_tool")
        tool.tags = None  # No tags

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        # Should not be disabled because no admin tag
        assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_user_fetch_failure_keeps_all_tools(self, mock_mcp, mock_gitea_client):
        """On user fetch error, all tools should remain visible."""
        mock_gitea_client.request = AsyncMock(side_effect=Exception("API error"))

        tool = self.create_tool("repo_list", tags={"repository"})
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        # Tool should not be modified
        assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_empty_provider_tools(self, mock_mcp, mock_gitea_client):
        """Empty provider tools list should be handled gracefully."""
        mock_gitea_client.request = AsyncMock(return_value={"admin": False, "login": "user"})
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[])

        # Should not raise
        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

    async def test_multiple_providers(self, mock_mcp, mock_gitea_client):
        """Filtering should aggregate tools from all providers."""
        mock_gitea_client.request = AsyncMock(return_value={"admin": False, "login": "user"})

        admin_tool = self.create_tool("admin_test", tags={"admin"})
        repo_tool = self.create_tool("repo_test", tags={"repository"})

        # Create proper provider mocks with list_tools method
        provider1 = AsyncMock()
        provider1.list_tools = AsyncMock(return_value=[admin_tool])
        provider2 = AsyncMock()
        provider2.list_tools = AsyncMock(return_value=[repo_tool])
        mock_mcp.providers = [provider1, provider2]

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert admin_tool.meta["fastmcp"]["_internal"]["visibility"] is False
        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})
