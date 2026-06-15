"""Unit tests for tool permission filtering."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitea_mcp_server.tool_filter import (
    _fetch_user_and_tokens,
    _get_required_scope,
    _has_sufficient_scope,
    _make_tool_key,
    _match_active_token,
    filter_resources_by_permissions,
    filter_tools_by_permissions,
)


class TestMakeToolKey:
    """Tests for the _make_tool_key helper function."""

    def test_without_prefix(self):
        assert _make_tool_key("issue_list") == "tool:issue_list@"

    def test_with_prefix(self):
        assert _make_tool_key("issue_list", "gitea_") == "tool:gitea_issue_list@"

    def test_empty_name(self):
        assert _make_tool_key("") == "tool:@"

    def test_empty_prefix(self):
        assert _make_tool_key("get_version", "") == "tool:get_version@"


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
    """Tests for the filter_tools_by_permissions function.

    Verifies that mcp.disable() is called with the correct tool keys
    for tools whose required scope exceeds the active token's scopes.
    """

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
        """Sudo scope should keep all tools visible (no disable call)."""
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

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, "test-token", prefix="")

        mock_mcp.disable.assert_not_called()

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

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        # issue_list has read:issue (which is available) -> not disabled
        # repo_create has write:repository (not in limited token) -> disabled
        mock_mcp.disable.assert_called_once_with(keys={"tool:repo_create@"})

    async def test_disables_tools_without_required_scope(self, mock_mcp, mock_gitea_client):
        """Tools needing scopes outside the token should be disabled."""
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

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        mock_mcp.disable.assert_called_once_with(keys={"tool:issue_list@"})

    async def test_disables_with_prefix(self, mock_mcp, mock_gitea_client):
        """When a prefix is provided, tool keys should include it."""
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
            ]
        )

        issue_tool = self.create_tool("issue_list", required_scope="read:issue")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[issue_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="gitea_")

        mock_mcp.disable.assert_called_once_with(keys={"tool:gitea_issue_list@"})

    async def test_write_scope_covers_read_needs(self, mock_mcp, mock_gitea_client):
        """Write scope should satisfy read requirements."""
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["write:repository"], "test-token")],
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        mock_mcp.disable.assert_not_called()

    async def test_tools_without_scope_requirement_always_visible(
        self, mock_mcp, mock_gitea_client
    ):
        """Tools with no scope requirement should never be disabled."""
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", [], "test-token")],
            ]
        )

        misc_tool = self.create_tool("get_version")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[misc_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        mock_mcp.disable.assert_not_called()

    async def test_token_fetch_failure_keeps_all_tools(self, mock_mcp, mock_gitea_client):
        """API failure fetching tokens should keep all tools visible."""
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                Exception("Token API error"),
            ]
        )

        repo_tool = self.create_tool("repo_list", required_scope="read:repository")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[repo_tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, "test-token", prefix="")

        mock_mcp.disable.assert_not_called()

    async def test_user_fetch_failure_keeps_all_tools(self, mock_mcp, mock_gitea_client):
        """API failure fetching user should keep all tools visible."""
        mock_gitea_client.request = AsyncMock(side_effect=Exception("API error"))
        tool = self.create_tool("repo_list", required_scope="read:repository")
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[tool])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        mock_mcp.disable.assert_not_called()

    async def test_empty_provider_tools(self, mock_mcp, mock_gitea_client):
        """Empty tool list from providers should not call mcp.disable."""
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"admin": False, "login": "dev2"},
                [self._make_token("test", ["read:repository"], "test-token")],
            ]
        )
        mock_mcp.providers[0].list_tools = AsyncMock(return_value=[])

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        mock_mcp.disable.assert_not_called()

    async def test_multiple_providers(self, mock_mcp, mock_gitea_client):
        """Tools from multiple providers should all be considered."""
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

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        mock_mcp.disable.assert_called_once_with(keys={"tool:issue_list@"})

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

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        mock_mcp.disable.assert_not_called()


class TestFilterResourcesByPermissions:
    """Tests for the filter_resources_by_permissions function.

    Verifies that mcp.disable() is called with the correct resource/template
    keys for components whose required scope exceeds the active token's scopes.
    """

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
        # Real Resource.key would be resource:{uri}@{version}, match that pattern
        resource.key = f"resource:{uri}@"
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
        # Real ResourceTemplate.key would be template:{uri_template}@{version}
        template.key = f"template:{uri_template}@"
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
        """Sudo scope should keep all resources visible."""
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

        mock_mcp.disable.assert_not_called()

    async def test_disables_resources_without_required_scope(self, mock_mcp, mock_gitea_client):
        """Resources needing scopes outside the token should be disabled."""
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

        mock_mcp.disable.assert_called_once_with(keys={"resource:gitea://issue@"})

    async def test_disables_templates_without_required_scope(self, mock_mcp, mock_gitea_client):
        """Resource templates needing scopes outside the token should be disabled."""
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

        mock_mcp.disable.assert_called_once_with(keys={"template:gitea://orgs/{org}@"})

    async def test_write_scope_covers_read_needs(self, mock_mcp, mock_gitea_client):
        """Write scope should satisfy read requirements for resources."""
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

        mock_mcp.disable.assert_not_called()

    async def test_resources_without_scope_requirement_always_visible(
        self, mock_mcp, mock_gitea_client
    ):
        """Resources with no scope requirement should never be disabled."""
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

        mock_mcp.disable.assert_not_called()

    async def test_token_fetch_failure_keeps_all_resources(self, mock_mcp, mock_gitea_client):
        """API failure fetching tokens should keep all resources visible."""
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

        mock_mcp.disable.assert_not_called()

    async def test_user_fetch_failure_keeps_all_resources(self, mock_mcp, mock_gitea_client):
        """API failure fetching user should keep all resources visible."""
        mock_gitea_client.request = AsyncMock(side_effect=Exception("API error"))
        repo_res = self.create_resource("repo", "gitea://repo", required_scope="read:repository")
        mock_mcp.providers[0].list_resources = AsyncMock(return_value=[repo_res])
        mock_mcp.providers[0].list_resource_templates = AsyncMock(return_value=[])

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        mock_mcp.disable.assert_not_called()

    async def test_empty_provider_resources(self, mock_mcp, mock_gitea_client):
        """Empty resource list from providers should not call mcp.disable."""
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

        mock_mcp.disable.assert_not_called()

    async def test_both_resources_and_templates_filtered(self, mock_mcp, mock_gitea_client):
        """Both resources and templates are checked for scope sufficiency."""
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

        mock_mcp.disable.assert_called_once_with(keys={"template:gitea://orgs/{org}@"})

    async def test_no_token_match_keeps_all_resources(self, mock_mcp, mock_gitea_client):
        """When no token matches, keep all resources visible."""
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

        mock_mcp.disable.assert_not_called()


class TestValidateUserData:
    """Tests for _validate_user_data edge cases."""

    def test_non_dict_raises_type_error(self):
        """Non-dict user data raises TypeError."""
        from gitea_mcp_server.tool_filter import _validate_user_data

        with pytest.raises(TypeError, match="Unexpected user data type"):
            _validate_user_data("not a dict")


class TestCollectProviderToolsEdgeCases:
    """Tests for _collect_provider_tools and _collect_provider_resources edge cases."""

    @pytest.mark.asyncio
    async def test_provider_list_tools_exception_skipped(self):
        """Exception in provider.list_tools() is handled gracefully."""
        from unittest.mock import MagicMock, AsyncMock

        from gitea_mcp_server.tool_filter import _collect_provider_tools

        mcp = MagicMock()
        provider = AsyncMock()
        provider.list_tools = AsyncMock(side_effect=AttributeError("missing method"))
        mcp.providers = [provider]

        result = await _collect_provider_tools(mcp)
        assert result == []

    @pytest.mark.asyncio
    async def test_provider_list_resources_exception_skipped(self):
        """Exception in provider.list_resources() is handled gracefully."""
        from unittest.mock import MagicMock, AsyncMock

        from gitea_mcp_server.tool_filter import _collect_provider_resources

        mcp = MagicMock()
        provider = AsyncMock()
        provider.list_resources = AsyncMock(side_effect=AttributeError("missing method"))
        provider.list_resource_templates = AsyncMock(return_value=[])
        mcp.providers = [provider]

        result = await _collect_provider_resources(mcp)
        assert result == []

    @pytest.mark.asyncio
    async def test_provider_list_templates_exception_skipped(self):
        """Exception in provider.list_resource_templates() is handled gracefully."""
        from unittest.mock import MagicMock, AsyncMock

        from gitea_mcp_server.tool_filter import _collect_provider_resources

        mcp = MagicMock()
        provider = AsyncMock()
        provider.list_resources = AsyncMock(return_value=[])
        provider.list_resource_templates = AsyncMock(side_effect=AttributeError("missing method"))
        mcp.providers = [provider]

        result = await _collect_provider_resources(mcp)
        assert result == []


class TestFetchUserAndTokensEdgeCases:
    """Tests for _fetch_user_and_tokens edge cases."""

    @pytest.mark.asyncio
    async def test_user_fetch_exception_returns_none(self):
        """Exception fetching user returns None."""
        from gitea_mcp_server.tool_filter import _fetch_user_and_tokens

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=Exception("API error"))

        result = await _fetch_user_and_tokens(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_tokens_not_a_list_returns_none(self):
        """Tokens response that is not a list returns None."""
        from gitea_mcp_server.tool_filter import _fetch_user_and_tokens

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                "not_a_list",
            ]
        )

        result = await _fetch_user_and_tokens(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_token_match_none_returns_none(self):
        """No matching token returns None."""
        from gitea_mcp_server.tool_filter import _fetch_user_and_tokens

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": "aaaaaaaa", "scopes": ["sudo"]}],
            ]
        )

        result = await _fetch_user_and_tokens(mock_client, "no-match-token")
        assert result is None


class TestFilterToolsByPermissionsEdgeCases:
    """Tests for edge cases in filter_tools_by_permissions."""

    @pytest.mark.asyncio
    async def test_non_dict_user_data_logged(self):
        """Non-dict user data is handled gracefully."""
        mock_mcp = MagicMock()
        mock_mcp.providers = []
        mock_gitea_client = MagicMock()
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(return_value="not a dict")

        from gitea_mcp_server.tool_filter import filter_tools_by_permissions

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

    @pytest.mark.asyncio
    async def test_provider_list_tools_exception_logged(self):
        """Exception from provider.list_tools is caught by filter_tools_by_permissions."""
        mock_gitea_client = MagicMock()
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": "test-token", "scopes": ["read:repo"]}],
            ]
        )

        mock_mcp = MagicMock()
        bad_provider = AsyncMock()
        bad_provider.list_tools = AsyncMock(side_effect=TypeError("unexpected"))
        mock_mcp.providers = [bad_provider]

        from gitea_mcp_server.tool_filter import filter_tools_by_permissions

        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

    @pytest.mark.asyncio
    async def test_provider_list_resources_exception_logged(self):
        """Exception from provider.list_resources is caught."""
        mock_gitea_client = MagicMock()
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": "test-token", "scopes": ["read:repo"]}],
            ]
        )

        mock_mcp = MagicMock()
        bad_provider = AsyncMock()
        bad_provider.list_resources = AsyncMock(side_effect=TypeError("unexpected"))
        bad_provider.list_resource_templates = AsyncMock(return_value=[])
        mock_mcp.providers = [bad_provider]

        from gitea_mcp_server.tool_filter import filter_resources_by_permissions

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

    @pytest.mark.asyncio
    async def test_provider_list_templates_exception_logged(self):
        """Exception from provider.list_resource_templates is caught."""
        mock_gitea_client = MagicMock()
        mock_gitea_client.config.token = "test-token"
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": "test-token", "scopes": ["read:repo"]}],
            ]
        )

        mock_mcp = MagicMock()
        bad_provider = AsyncMock()
        bad_provider.list_resources = AsyncMock(return_value=[])
        bad_provider.list_resource_templates = AsyncMock(side_effect=TypeError("unexpected"))
        mock_mcp.providers = [bad_provider]

        from gitea_mcp_server.tool_filter import filter_resources_by_permissions

        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

    @pytest.mark.asyncio
    async def test_mcp_disable_exception_in_filter_tools(self):
        """Exception in mcp.disable() during filter_tools_by_permissions is caught."""
        raw_token = "admin-token-last8ok"
        last_eight = raw_token[-8:]
        mock_gitea_client = MagicMock()
        mock_gitea_client.config.token = raw_token
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": last_eight, "scopes": ["read:admin"]}],
            ]
        )

        mock_mcp = MagicMock()
        tool_with_scope = MagicMock()
        tool_with_scope.name = "admin_tool"
        tool_with_scope.key = "admin_tool"
        tool_with_scope.meta = {"fastmcp": {"_internal": {"required_scope": "sudo"}}}
        provider = AsyncMock()
        provider.list_tools = AsyncMock(return_value=[tool_with_scope])
        mock_mcp.providers = [provider]

        # Make mcp.disable raise an exception
        mock_mcp.disable.side_effect = TypeError("visibility transform error")

        from gitea_mcp_server.tool_filter import filter_tools_by_permissions
        await filter_tools_by_permissions(mock_mcp, mock_gitea_client, prefix="")

        # Should not propagate — exception is caught and logged
        mock_mcp.disable.assert_called_once()

    @pytest.mark.asyncio
    async def test_mcp_disable_exception_in_filter_resources(self):
        """Exception in mcp.disable() during filter_resources_by_permissions is caught."""
        raw_token = "admin-token-last8ok"
        last_eight = raw_token[-8:]
        mock_gitea_client = MagicMock()
        mock_gitea_client.config.token = raw_token
        mock_gitea_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": last_eight, "scopes": ["read:admin"]}],
            ]
        )

        mock_mcp = MagicMock()
        resource_component = MagicMock()
        resource_component.name = "admin_resource"
        resource_component.key = "resource:admin://data@"
        resource_component.meta = {"fastmcp": {"_internal": {"required_scope": "sudo"}}}
        provider = AsyncMock()
        provider.list_resources = AsyncMock(return_value=[resource_component])
        provider.list_resource_templates = AsyncMock(return_value=[])
        mock_mcp.providers = [provider]

        # Make mcp.disable raise an exception
        mock_mcp.disable.side_effect = TypeError("visibility transform error")

        from gitea_mcp_server.tool_filter import filter_resources_by_permissions
        await filter_resources_by_permissions(mock_mcp, mock_gitea_client)

        # Should not propagate — exception is caught and logged
        mock_mcp.disable.assert_called_once()


class TestCollectProviderToolsIntegration:
    """Integration tests for provider tool collection."""

    @pytest.mark.asyncio
    async def test_multiple_providers_some_fail(self):
        """Some providers failing doesn't prevent others from being collected."""
        from unittest.mock import MagicMock, AsyncMock

        from gitea_mcp_server.tool_filter import _collect_provider_tools

        mcp = MagicMock()

        provider1 = AsyncMock()
        provider1.list_tools = AsyncMock(return_value=["tool1"])

        provider2 = AsyncMock()
        provider2.list_tools = AsyncMock(side_effect=TypeError("unexpected error"))

        provider3 = AsyncMock()
        provider3.list_tools = AsyncMock(return_value=["tool2", "tool3"])

        mcp.providers = [provider1, provider2, provider3]

        result = await _collect_provider_tools(mcp)
        assert result == ["tool1", "tool2", "tool3"]
