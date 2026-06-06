"""Unit tests for tool permission filtering."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.tool_filter import (
    _fetch_user_and_tokens,
    _get_required_scope,
    _has_sufficient_scope,
    _match_active_token,
    _set_visibility,
    filter_resources_by_permissions,
    filter_tools_by_permissions,
)


class TestMatchActiveToken:
    """Tests for the _match_active_token helper function."""

    def test_matches_by_last_eight(self):
        token_val = "my-secret-token"
        last_eight = token_val[-8:]
        tokens = [
            {"id": 1, "name": "other", "token_last_eight": "00000000", "scopes": ["read:other"]},
            {"id": 2, "name": "active", "token_last_eight": last_eight, "scopes": ["read:repo"]},
        ]
        result = _match_active_token(tokens, token_val)
        assert result == {"read:repo"}

    def test_no_match_returns_none(self):
        tokens = [
            {"id": 1, "name": "t1", "token_last_eight": "aaaaaaaa", "scopes": ["read:a"]},
        ]
        result = _match_active_token(tokens, "no-match-token")
        assert result is None

    def test_empty_tokens_list(self):
        result = _match_active_token([], "some-token")
        assert result is None

    def test_token_without_scopes_field(self):
        token_val = "no-scopes"
        last_eight = token_val[-8:]
        tokens = [
            {"id": 1, "name": "t1", "token_last_eight": last_eight},
        ]
        result = _match_active_token(tokens, token_val)
        assert result is None


class TestGetRequiredScope:
    """Tests for the _get_required_scope helper function."""

    def _make_tool_with_scope(self, required_scope: str | None):
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
        mcp = MagicMock()
        provider = AsyncMock()
        mcp.providers = [provider]
        return mcp

    @pytest.fixture
    def mock_gitea_client(self):
        return MagicMock()

    def create_tool(self, name: str, tags: set | None = None, required_scope: str | None = None):
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

    def _make_token(self, name: str, scopes: list[str], token_val: str | None = None) -> dict:
        """Create a token dict that matches the API format."""
        token = {"id": 1, "name": name, "scopes": scopes}
        if token_val:
            token["token_last_eight"] = token_val[-8:]
        else:
            token["token_last_eight"] = "00000000"
        return token

    async def test_user_with_sudo_sees_all_tools(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("admin-token", ["sudo"], "test-token")],
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        admin_tool = self.create_tool("admin_users", required_scope="sudo")
        user_tool = self.create_tool("user_get", required_scope="read:user")

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool, admin_tool, user_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, "test-token")

        for tool in [repo_tool, admin_tool, user_tool]:
            assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_only_active_token_scopes_used(self, mock_mcp, mock_gitea_client):
        """Only the active token's scopes are used, not union of all."""
        mock_gitea_client.config.token = "active-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [
                    self._make_token("limited", ["read:issue"], "active-token"),
                    self._make_token("powerful", ["write:repository", "read:user"], "other-token"),
                ],
            ]
        )

        issue_tool = self.create_tool("issue_list", required_scope="read:issue")
        repo_tool = self.create_tool("repo_create", required_scope="write:repository")

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[issue_tool, repo_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in issue_tool.meta.get("fastmcp", {}).get("_internal", {})
        assert repo_tool.meta["fastmcp"]["_internal"]["visibility"] is False

    async def test_disables_tools_without_required_scope(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        issue_tool = self.create_tool("issue_list", required_scope="read:issue")

        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool, issue_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})
        assert issue_tool.meta["fastmcp"]["_internal"]["visibility"] is False

    async def test_write_scope_covers_read_needs(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["write:repository"], "test-token")],
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_tools_without_scope_requirement_always_visible(
        self, mock_mcp, mock_gitea_client
    ):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", [], "test-token")],
            ]
        )

        misc_tool = self.create_tool("get_version")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[misc_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in misc_tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_token_fetch_failure_keeps_all_tools(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
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
        mock_gitea_client.request = AsyncMock(side_effect=Exception("API error"))
        tool = self.create_tool("repo_list", required_scope="read:repository")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in tool.meta.get("fastmcp", {}).get("_internal", {})

    async def test_empty_provider_tools(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
            ]
        )
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

    async def test_multiple_providers(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
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

    async def test_no_token_match_keeps_all_tools(self, mock_mcp, mock_gitea_client):
        """When no token matches the active token hash, keep all tools."""
        mock_gitea_client.config.token = "unknown-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("t1", ["read:repo"], "other-token")],
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_tool.meta.get("fastmcp", {}).get("_internal", {})


class TestSetVisibility:
    """Tests for the _set_visibility helper function."""

    def test_sets_visibility_false(self):
        obj = MagicMock()
        obj.meta = {}
        _set_visibility(obj, False)
        assert obj.meta["fastmcp"]["_internal"]["visibility"] is False

    def test_sets_visibility_true(self):
        obj = MagicMock()
        obj.meta = {}
        _set_visibility(obj, True)
        assert obj.meta["fastmcp"]["_internal"]["visibility"] is True

    def test_creates_nested_dicts_when_missing(self):
        obj = MagicMock()
        obj.meta = None
        _set_visibility(obj, False)
        assert obj.meta["fastmcp"]["_internal"]["visibility"] is False

    def test_preserves_existing_meta(self):
        obj = MagicMock()
        obj.meta = {"existing": "value"}
        _set_visibility(obj, False)
        assert obj.meta["existing"] == "value"
        assert obj.meta["fastmcp"]["_internal"]["visibility"] is False


class TestFilterResourcesByPermissions:
    """Tests for the filter_resources_by_permissions function."""

    @pytest.fixture
    def mock_mcp(self):
        mcp = MagicMock()
        provider = AsyncMock()
        mcp.providers = [provider]
        return mcp

    @pytest.fixture
    def mock_gitea_client(self):
        return MagicMock()

    def create_resource(self, name: str, uri: str, required_scope: str | None = None):
        resource = MagicMock()
        resource.name = name
        resource.uri = uri
        resource.meta = {}
        if required_scope is not None:
            resource.meta.setdefault("fastmcp", {}).setdefault("_internal", {})[
                "required_scope"
            ] = required_scope
        return resource

    def create_template(self, name: str, uri_template: str, required_scope: str | None = None):
        template = MagicMock()
        template.name = name
        template.uri_template = uri_template
        template.meta = {}
        if required_scope is not None:
            template.meta.setdefault("fastmcp", {}).setdefault("_internal", {})[
                "required_scope"
            ] = required_scope
        return template

    def _make_token(self, name: str, scopes: list[str], token_val: str | None = None) -> dict:
        token = {"id": 1, "name": name, "scopes": scopes}
        if token_val:
            token["token_last_eight"] = token_val[-8:]
        else:
            token["token_last_eight"] = "00000000"
        return token

    async def test_user_with_sudo_sees_all_resources(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("admin-token", ["sudo"], "test-token")],
            ]
        )

        repo_res = self.create_resource("repo", "gitea://repo", required_scope="read:repository")
        org_res = self.create_resource("org", "gitea://org", required_scope="read:organization")

        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res, org_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        for r in [repo_res, org_res]:
            assert "visibility" not in r.meta.get("fastmcp", {}).get("_internal", {})

    async def test_disables_resources_without_required_scope(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
            ]
        )

        repo_res = self.create_resource("repo", "gitea://repo", required_scope="read:repository")
        issue_res = self.create_resource("issue", "gitea://issue", required_scope="read:issue")

        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res, issue_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_res.meta.get("fastmcp", {}).get("_internal", {})
        assert issue_res.meta["fastmcp"]["_internal"]["visibility"] is False

    async def test_disables_templates_without_required_scope(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
            ]
        )

        tpl = self.create_template("repo_tpl", "gitea://repos/{owner}/{repo}", required_scope="read:repository")
        org_tpl = self.create_template("org_tpl", "gitea://orgs/{org}", required_scope="read:organization")

        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[tpl, org_tpl])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in tpl.meta.get("fastmcp", {}).get("_internal", {})
        assert org_tpl.meta["fastmcp"]["_internal"]["visibility"] is False

    async def test_write_scope_covers_read_needs(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["write:repository"], "test-token")],
            ]
        )

        repo_res = self.create_resource("repo", "gitea://repo", required_scope="read:repository")
        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_res.meta.get("fastmcp", {}).get("_internal", {})

    async def test_resources_without_scope_requirement_always_visible(
        self, mock_mcp, mock_gitea_client
    ):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", [], "test-token")],
            ]
        )

        version_res = self.create_resource("version", "gitea://version")
        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[version_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in version_res.meta.get("fastmcp", {}).get("_internal", {})

    async def test_token_fetch_failure_keeps_all_resources(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                Exception("Token API error"),
            ]
        )

        repo_res = self.create_resource("repo", "gitea://repo", required_scope="read:repository")
        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_res.meta.get("fastmcp", {}).get("_internal", {})

    async def test_user_fetch_failure_keeps_all_resources(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.request = AsyncMock(side_effect=Exception("API error"))
        repo_res = self.create_resource("repo", "gitea://repo", required_scope="read:repository")
        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_res.meta.get("fastmcp", {}).get("_internal", {})

    async def test_empty_provider_resources(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
            ]
        )
        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

    async def test_both_resources_and_templates_filtered(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "mixed-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [
                    self._make_token("mixed", ["read:repository", "read:user"], "mixed-token"),
                ],
            ]
        )

        repo_res = self.create_resource("repo", "gitea://repos/static", required_scope="read:repository")
        org_tpl = self.create_template("org_tpl", "gitea://orgs/{org}", required_scope="read:organization")
        user_res = self.create_resource("user", "gitea://user", required_scope="read:user")

        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res, user_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[org_tpl])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_res.meta.get("fastmcp", {}).get("_internal", {})
        assert "visibility" not in user_res.meta.get("fastmcp", {}).get("_internal", {})
        assert org_tpl.meta["fastmcp"]["_internal"]["visibility"] is False

    async def test_no_token_match_keeps_all_resources(self, mock_mcp, mock_gitea_client):
        mock_gitea_client.config.token = "unknown-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("t1", ["read:repo"], "other-token")],
            ]
        )

        repo_res = self.create_resource("repo", "gitea://repo", required_scope="read:repository")
        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        assert "visibility" not in repo_res.meta.get("fastmcp", {}).get("_internal", {})
