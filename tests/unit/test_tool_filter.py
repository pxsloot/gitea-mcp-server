"""Unit tests for tool permission filtering."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.tool_filter import (
    _get_required_scope,
    _has_sufficient_scope,
    filter_tools_by_permissions,
)


class TestGetRequiredScope:
    """Tests for the _get_required_scope helper function."""

    def _make_tool_with_scope(self, required_scope: str | None):
        """Helper to create a mock tool with required_scope in meta."""
        tool = MagicMock()
        tool.name = "test_tool"
        tool.key = "test_tool"
        tool.meta = {}
        if required_scope is not None:
            tool.meta.setdefault("fastmcp", {}).setdefault("_internal", {})[
                "required_scope"
            ] = required_scope
        return tool

    def test_returns_scope_from_meta(self):
        tool = self._make_tool_with_scope("read:repository")
        assert _get_required_scope(tool) == "read:repository"

    def test_returns_sudo_from_meta(self):
        tool = self._make_tool_with_scope("sudo")
        assert _get_required_scope(tool) == "sudo"

    def test_returns_none_when_no_meta(self):
        tool = MagicMock()
        tool.meta = {}
        assert _get_required_scope(tool) is None

    def test_returns_none_when_meta_is_none(self):
        tool = MagicMock()
        tool.meta = None
        assert _get_required_scope(tool) is None

    def test_returns_none_when_missing_internal(self):
        tool = MagicMock()
        tool.meta = {"fastmcp": {}}
        assert _get_required_scope(tool) is None

    def test_returns_none_when_missing_fastmcp(self):
        tool = MagicMock()
        tool.meta = {}
        assert _get_required_scope(tool) is None


class TestHasSufficientScope:
    """Tests for the _has_sufficient_scope helper function."""

    def test_sudo_in_available_grants_any_scope(self):
        assert _has_sufficient_scope("read:repository", {"sudo"}) is True
        assert _has_sufficient_scope("write:issue", {"sudo"}) is True
        assert _has_sufficient_scope("sudo", {"sudo"}) is True

    def test_exact_read_scope_match(self):
        assert _has_sufficient_scope("read:repository", {"read:repository"}) is True

    def test_exact_write_scope_match(self):
        assert _has_sufficient_scope("write:issue", {"write:issue"}) is True

    def test_write_scope_grants_read(self):
        """Having write:repository implies read:repository access."""
        assert _has_sufficient_scope("read:repository", {"write:repository"}) is True

    def test_read_scope_does_not_grant_write(self):
        assert _has_sufficient_scope("write:repository", {"read:repository"}) is False

    def test_unrelated_scope_does_not_suffice(self):
        assert _has_sufficient_scope("write:issue", {"read:repository"}) is False

    def test_none_required_always_sufficient(self):
        assert _has_sufficient_scope(None, set()) is True
        assert _has_sufficient_scope(None, {"read:repository"}) is True

    def test_empty_available_is_insufficient(self):
        assert _has_sufficient_scope("read:repository", set()) is False


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
        return MagicMock()

    def create_tool(self, name: str, tags: set | None = None, required_scope: str | None = None):
        """Helper to create a mock tool."""
        tool = MagicMock()
        tool.name = name
        tool.key = name
        tool.tags = tags or set()
        tool.meta = {}
        if required_scope is not None:
            tool.meta.setdefault("fastmcp", {}).setdefault("_internal", {})[
                "required_scope"
            ] = required_scope
        return tool

    def _make_tokens_response(self, scopes: list[str]) -> list[dict]:
        """Helper to create a mock tokens API response."""
        return [{"id": 1, "name": "test", "scopes": scopes}]

    async def test_user_with_sudo_sees_all_tools(self, mock_mcp, mock_gitea_client):
        """User with sudo scope in token should have all tools visible."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},  # GET /user
                self._make_tokens_response(["sudo"]),  # GET /users/dev2/tokens
            ]
        )

        repo_tool = self.create_tool("repo_list", tags={"repository"}, required_scope="read:repository")
        admin_tool = self.create_tool("admin_users", tags={"admin"}, required_scope="sudo")
        user_tool = self.create_tool("user_get", tags={"user"}, required_scope="read:user")

        mock_mcp.providers[0].list_tools = AsyncMock(
            return_value=[repo_tool, admin_tool, user_tool]
        )

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        for tool in [repo_tool, admin_tool, user_tool]:
            assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_non_admin_with_all_scopes_sees_all(self, mock_mcp, mock_gitea_client):
        """Non-admin with all required scopes sees all tools."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                self._make_tokens_response(
                    ["read:repository", "read:issue", "read:user"]
                ),
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        issue_tool = self.create_tool("issue_list", required_scope="read:issue")
        user_tool = self.create_tool("user_get", required_scope="read:user")

        mock_mcp.providers[0].list_tools = AsyncMock(
            return_value=[repo_tool, issue_tool, user_tool]
        )

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        for tool in [repo_tool, issue_tool, user_tool]:
            assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_disables_tools_without_required_scope(self, mock_mcp, mock_gitea_client):
        """Tools requiring scope not in token should be disabled."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                self._make_tokens_response(["read:repository"]),
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        issue_tool = self.create_tool("issue_list", required_scope="read:issue")

        mock_mcp.providers[0].list_tools = AsyncMock(
            return_value=[repo_tool, issue_tool]
        )

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        # repo tool should be visible
        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})
        # issue tool should be disabled
        assert issue_tool.meta["fastmcp"]["_internal"]["visibility"] is False

    async def test_write_scope_covers_read_needs(self, mock_mcp, mock_gitea_client):
        """Having write:xxx should satisfy a read:xxx required scope."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                self._make_tokens_response(["write:repository"]),
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_tools_without_scope_requirement_always_visible(
        self, mock_mcp, mock_gitea_client
    ):
        """Tools with no required_scope (None) should be visible regardless."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                self._make_tokens_response([]),
            ]
        )

        # Tool with no required_scope (e.g., version, markdown)
        misc_tool = self.create_tool("get_version")

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[misc_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in misc_tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_token_fetch_failure_keeps_all_tools(self, mock_mcp, mock_gitea_client):
        """On token fetch error, all tools should remain visible."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                Exception("Token API error"),
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_user_fetch_failure_keeps_all_tools(self, mock_mcp, mock_gitea_client):
        """On user fetch error (existing behavior), all tools remain visible."""
        mock_gitea_client.request = AsyncMock(side_effect=Exception("API error"))

        tool = self.create_tool("repo_list", required_scope="read:repository")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_empty_provider_tools(self, mock_mcp, mock_gitea_client):
        """Empty provider tools list should be handled gracefully."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                self._make_tokens_response(["read:repository"]),
            ]
        )
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

    async def test_multiple_providers(self, mock_mcp, mock_gitea_client):
        """Filtering should aggregate tools from all providers."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                self._make_tokens_response(["read:repository"]),
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        issue_tool = self.create_tool("issue_list", required_scope="read:issue")

        provider1 = AsyncMock()
        provider1.list_tools = AsyncMock(return_value=[repo_tool])
        provider2 = AsyncMock()
        provider2.list_tools = AsyncMock(return_value=[issue_tool])
        mock_mcp.providers = [provider1, provider2]

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})
        assert issue_tool.meta["fastmcp"]["_internal"]["visibility"] is False

    async def test_no_tokens_for_user(self, mock_mcp, mock_gitea_client):
        """User with no tokens should see only unscoped tools."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [],  # No tokens
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        misc_tool = self.create_tool("get_version")  # No required scope

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool, misc_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert repo_tool.meta["fastmcp"]["_internal"]["visibility"] is False
        assert "visibility" not in misc_tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_union_of_multiple_token_scopes(self, mock_mcp, mock_gitea_client):
        """Scopes from multiple tokens should be unioned."""
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [
                    {"id": 1, "name": "token1", "scopes": ["read:repository"]},
                    {"id": 2, "name": "token2", "scopes": ["read:issue"]},
                ],
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        issue_tool = self.create_tool("issue_list", required_scope="read:issue")
        user_tool = self.create_tool("user_get", required_scope="read:user")

        mock_mcp.providers[0].list_tools = AsyncMock(
            return_value=[repo_tool, issue_tool, user_tool]
        )

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})
        assert "visibility" not in issue_tool.meta.get("fastmcp", {}).get("_internal", {})
        assert user_tool.meta["fastmcp"]["_internal"]["visibility"] is False
