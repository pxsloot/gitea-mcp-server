"""Unit tests for tool permission filtering.

Tests for ``PermissionFilterTransform``, ``fetch_token_scopes``, and their
supporting helpers (``_get_required_scope``, ``_has_sufficient_scope``, etc.).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.tool_filter import (
    PermissionFilterTransform,
    _get_required_scope,
    _has_sufficient_scope,
    _match_active_token,
    fetch_token_scopes,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_tool(name: str, required_scope: str | None = None) -> MagicMock:
    """Create a mock Tool object with optional scope metadata."""
    tool = MagicMock()
    tool.name = name
    tool.key = name
    tool.tags = set()
    tool.meta = {}
    if required_scope is not None:
        tool.meta["required_scope"] = required_scope
    return tool


def _make_resource(
    name: str, uri: str = "", required_scope: str | None = None
) -> MagicMock:
    """Create a mock Resource object with optional scope metadata."""
    resource = MagicMock()
    resource.name = name
    resource.uri = uri or f"gitea://{name}"
    resource.meta = {}
    if required_scope is not None:
        resource.meta["required_scope"] = required_scope
    return resource


def _make_template(
    name: str, uri_template: str = "", required_scope: str | None = None
) -> MagicMock:
    """Create a mock ResourceTemplate object with optional scope metadata."""
    template = MagicMock()
    template.name = name
    template.uri_template = uri_template or f"gitea://{name}/{{param}}"
    template.meta = {}
    if required_scope is not None:
        template.meta["required_scope"] = required_scope
    return template


# ═══════════════════════════════════════════════════════════════════════
# _match_active_token
# ═══════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════
# _get_required_scope
# ═══════════════════════════════════════════════════════════════════════

class TestGetRequiredScope:
    """Tests for the _get_required_scope helper function."""

    def test_returns_scope_from_meta(self):
        tool = _make_tool("test_tool", required_scope="read:repository")
        assert _get_required_scope(tool) == "read:repository"

    def test_returns_sudo_from_meta(self):
        tool = _make_tool("test_tool", required_scope="sudo")
        assert _get_required_scope(tool) == "sudo"

    def test_returns_none_when_no_meta(self):
        tool = MagicMock()
        tool.meta = {}
        assert _get_required_scope(tool) is None

    def test_returns_none_when_meta_is_none(self):
        tool = MagicMock()
        tool.meta = None
        assert _get_required_scope(tool) is None

    def test_returns_none_when_scope_key_absent(self):
        tool = MagicMock()
        tool.meta = {"other": {}}
        assert _get_required_scope(tool) is None


# ═══════════════════════════════════════════════════════════════════════
# _has_sufficient_scope
# ═══════════════════════════════════════════════════════════════════════

class TestHasSufficientScope:
    """Tests for the _has_sufficient_scope helper function."""

    def test_sudo_grants_any_scope(self):
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


# ═══════════════════════════════════════════════════════════════════════
# _validate_user_data
# ═══════════════════════════════════════════════════════════════════════

class TestValidateUserData:
    """Tests for _validate_user_data edge cases."""

    def test_non_dict_raises_type_error(self):
        from gitea_mcp_server.tool_filter import _validate_user_data

        with pytest.raises(TypeError, match="Unexpected user data type"):
            _validate_user_data("not a dict")


# ═══════════════════════════════════════════════════════════════════════
# fetch_token_scopes
# ═══════════════════════════════════════════════════════════════════════

class TestFetchTokenScopes:
    """Tests for fetch_token_scopes."""

    @pytest.mark.asyncio
    async def test_user_fetch_exception_returns_none(self):
        """Exception fetching user returns None."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=Exception("API error"))

        result = await fetch_token_scopes(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_tokens_not_a_list_returns_none(self):
        """Tokens response that is not a list returns None."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                "not_a_list",
            ]
        )

        result = await fetch_token_scopes(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_token_match_none_returns_none(self):
        """No matching token returns None."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": "aaaaaaaa", "scopes": ["sudo"]}],
            ]
        )

        result = await fetch_token_scopes(mock_client, "no-match-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_dict_user_data_returns_none(self):
        """Non-dict user data is handled gracefully (returns None)."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                "not a dict",
            ]
        )

        result = await fetch_token_scopes(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_scopes(self):
        """Successful token fetch returns scopes."""
        token_val = "test-t-token----"
        last_eight = token_val[-8:]
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": last_eight, "scopes": ["read:repo", "write:issue"]}],
            ]
        )

        result = await fetch_token_scopes(mock_client, token_val)
        assert result == {"read:repo", "write:issue"}


# ═══════════════════════════════════════════════════════════════════════
# PermissionFilterTransform — tools
# ═══════════════════════════════════════════════════════════════════════

class TestPermissionFilterTransformTools:
    """Tests for PermissionFilterTransform tool-related methods."""

    def _transform(self, available_scopes: set[str] | None = None) -> PermissionFilterTransform:
        return PermissionFilterTransform(
            available_scopes if available_scopes is not None else {"read:repository"},
        )

    # ── list_tools ─────────────────────────────────────────────────

    async def test_list_tools_filters_by_scope(self):
        transform = self._transform({"read:repository"})
        tools = [
            _make_tool("repo_list", required_scope="read:repository"),
            _make_tool("issue_list", required_scope="read:issue"),
            _make_tool("version"),
        ]
        result = await transform.list_tools(tools)
        assert len(result) == 2
        assert result[0].name == "repo_list"
        assert result[1].name == "version"

    async def test_list_tools_sudo_sees_all(self):
        transform = self._transform({"sudo"})
        tools = [
            _make_tool("admin_op", required_scope="sudo"),
            _make_tool("repo_op", required_scope="read:repository"),
            _make_tool("version"),
        ]
        result = await transform.list_tools(tools)
        assert len(result) == 3

    async def test_list_tools_write_covers_read(self):
        transform = self._transform({"write:repository"})
        tools = [
            _make_tool("repo_list", required_scope="read:repository"),
            _make_tool("repo_create", required_scope="write:repository"),
        ]
        result = await transform.list_tools(tools)
        assert len(result) == 2

    async def test_list_tools_all_filtered_when_no_scopes(self):
        transform = self._transform(set())
        tools = [
            _make_tool("repo_list", required_scope="read:repository"),
        ]
        result = await transform.list_tools(tools)
        assert len(result) == 0

    async def test_list_tools_empty_input(self):
        transform = self._transform({"read:repository"})
        result = await transform.list_tools([])
        assert result == []

    # ── get_tool ───────────────────────────────────────────────────

    async def test_get_tool_returns_allowed_tool(self):
        transform = self._transform({"read:repository"})
        repo_tool = _make_tool("repo_list", required_scope="read:repository")

        async def call_next(name, *, version=None):
            return repo_tool

        result = await transform.get_tool("repo_list", call_next)
        assert result is repo_tool

    async def test_get_tool_returns_none_for_denied_tool(self):
        transform = self._transform({"read:repository"})
        issue_tool = _make_tool("issue_list", required_scope="read:issue")

        async def call_next(name, *, version=None):
            return issue_tool

        result = await transform.get_tool("issue_list", call_next)
        assert result is None

    async def test_get_tool_returns_none_when_next_returns_none(self):
        transform = self._transform({"read:repository"})

        async def call_next(name, *, version=None):
            return None

        result = await transform.get_tool("nonexistent", call_next)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# PermissionFilterTransform — resources
# ═══════════════════════════════════════════════════════════════════════

class TestPermissionFilterTransformResources:
    """Tests for PermissionFilterTransform resource-related methods."""

    def _transform(self, available_scopes: set[str] | None = None) -> PermissionFilterTransform:
        return PermissionFilterTransform(
            available_scopes if available_scopes is not None else {"read:repository"},
        )

    # ── list_resources ─────────────────────────────────────────────

    async def test_list_resources_filters_by_scope(self):
        transform = self._transform({"read:repository"})
        resources = [
            _make_resource("repo", required_scope="read:repository"),
            _make_resource("issue", required_scope="read:issue"),
            _make_resource("version"),
        ]
        result = await transform.list_resources(resources)
        assert len(result) == 2
        assert result[0].name == "repo"
        assert result[1].name == "version"

    async def test_list_resources_sudo_sees_all(self):
        transform = self._transform({"sudo"})
        resources = [
            _make_resource("admin", required_scope="sudo"),
            _make_resource("repo", required_scope="read:repository"),
        ]
        result = await transform.list_resources(resources)
        assert len(result) == 2

    async def test_list_resources_empty_input(self):
        transform = self._transform({"read:repository"})
        result = await transform.list_resources([])
        assert result == []

    # ── list_resource_templates ────────────────────────────────────

    async def test_list_resource_templates_filters_by_scope(self):
        transform = self._transform({"read:repository"})
        templates = [
            _make_template("repo_tpl", required_scope="read:repository"),
            _make_template("org_tpl", required_scope="read:organization"),
        ]
        result = await transform.list_resource_templates(templates)
        assert len(result) == 1
        assert result[0].name == "repo_tpl"

    async def test_list_resource_templates_empty_input(self):
        transform = self._transform({"read:repository"})
        result = await transform.list_resource_templates([])
        assert result == []

    # ── get_resource ───────────────────────────────────────────────

    async def test_get_resource_returns_allowed(self):
        transform = self._transform({"read:repository"})
        resource = _make_resource("repo", required_scope="read:repository")

        async def call_next(uri, *, version=None):
            return resource

        result = await transform.get_resource("gitea://repo", call_next)
        assert result is resource

    async def test_get_resource_returns_none_for_denied(self):
        transform = self._transform({"read:repository"})
        resource = _make_resource("issue", required_scope="read:issue")

        async def call_next(uri, *, version=None):
            return resource

        result = await transform.get_resource("gitea://issue", call_next)
        assert result is None

    async def test_get_resource_none_when_next_returns_none(self):
        transform = self._transform({"read:repository"})

        async def call_next(uri, *, version=None):
            return None

        result = await transform.get_resource("gitea://nonexistent", call_next)
        assert result is None

    # ── get_resource_template ──────────────────────────────────────

    async def test_get_resource_template_returns_allowed(self):
        transform = self._transform({"read:repository"})
        template = _make_template("repo_tpl", required_scope="read:repository")

        async def call_next(uri, *, version=None):
            return template

        result = await transform.get_resource_template("gitea://repo", call_next)
        assert result is template

    async def test_get_resource_template_returns_none_for_denied(self):
        transform = self._transform({"read:repository"})
        template = _make_template("org_tpl", required_scope="read:organization")

        async def call_next(uri, *, version=None):
            return template

        result = await transform.get_resource_template("gitea://org", call_next)
        assert result is None

    async def test_get_resource_template_none_when_next_returns_none(self):
        transform = self._transform({"read:repository"})

        async def call_next(uri, *, version=None):
            return None

        result = await transform.get_resource_template("gitea://nonexistent", call_next)
        assert result is None
