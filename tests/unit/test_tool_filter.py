"""Unit tests for spec-level tool filtering.

Covers:
- ``fetch_token_scopes`` (token matching).
- ``has_sufficient_scope`` — scope sufficiency rules.
- ``_compute_excluded_routes`` — the spec-prep step that decides which
  ``(path, METHOD)`` pairs are dropped via ``route_map_fn``.
- ``create_openapi_provider`` — filtered operations never become tools.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.providers.openapi import MCPType

from gitea_mcp_server.scope import has_sufficient_scope
from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.spec_loader import (
    _compute_excluded_routes,
    _match_active_token,
    fetch_token_scopes,
)
from gitea_mcp_server.tools.filter_info import compute_filtered_tools_info


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

    def test_skips_non_dict_token_entries(self):
        token_val = "mix-token"
        last_eight = token_val[-8:]
        tokens = [
            "not-a-dict",
            None,
            {"id": 1, "name": "active", "token_last_eight": last_eight, "scopes": ["read:repo"]},
        ]
        result = _match_active_token(tokens, token_val)
        assert result == {"read:repo"}

    def test_scopes_not_a_list_returns_none(self):
        token_val = "str-scopes"
        last_eight = token_val[-8:]
        tokens = [
            {"id": 1, "name": "t", "token_last_eight": last_eight, "scopes": "all"},
        ]
        result = _match_active_token(tokens, token_val)
        assert result is None

    def test_empty_scopes_list_returns_none(self):
        token_val = "empty-sco"
        last_eight = token_val[-8:]
        tokens = [
            {"id": 1, "name": "t", "token_last_eight": last_eight, "scopes": []},
        ]
        result = _match_active_token(tokens, token_val)
        assert result is None

    def test_matches_all_scope(self):
        token_val = "all-token-"
        last_eight = token_val[-8:]
        tokens = [
            {"id": 1, "name": "t", "token_last_eight": last_eight, "scopes": ["all"]},
        ]
        result = _match_active_token(tokens, token_val)
        assert result == {"all"}


# ═══════════════════════════════════════════════════════════════════════
# has_sufficient_scope
# ═══════════════════════════════════════════════════════════════════════


class TestHasSufficientScope:
    """Tests for the has_sufficient_scope helper function."""

    def test_sudo_grants_any_scope(self):
        assert has_sufficient_scope("read:repository", {"sudo"}) is True
        assert has_sufficient_scope("write:issue", {"sudo"}) is True
        assert has_sufficient_scope("sudo", {"sudo"}) is True

    def test_all_grants_any_scope(self):
        assert has_sufficient_scope("read:repository", {"all"}) is True
        assert has_sufficient_scope("write:issue", {"all"}) is True
        assert has_sufficient_scope("sudo", {"all"}) is True
        assert has_sufficient_scope(None, {"all"}) is True

    def test_exact_read_scope_match(self):
        assert has_sufficient_scope("read:repository", {"read:repository"}) is True

    def test_exact_write_scope_match(self):
        assert has_sufficient_scope("write:issue", {"write:issue"}) is True

    def test_write_scope_grants_read(self):
        assert has_sufficient_scope("read:repository", {"write:repository"}) is True

    def test_read_scope_does_not_grant_write(self):
        assert has_sufficient_scope("write:repository", {"read:repository"}) is False

    def test_unrelated_scope_does_not_suffice(self):
        assert has_sufficient_scope("write:issue", {"read:repository"}) is False

    def test_none_required_always_sufficient(self):
        assert has_sufficient_scope(None, set()) is True
        assert has_sufficient_scope(None, {"read:repository"}) is True

    def test_empty_available_is_insufficient(self):
        assert has_sufficient_scope("read:repository", set()) is False


# ═══════════════════════════════════════════════════════════════════════
# fetch_token_scopes
# ═══════════════════════════════════════════════════════════════════════


class TestFetchTokenScopes:
    """Tests for fetch_token_scopes (now in spec_loader)."""

    @pytest.mark.asyncio
    async def test_user_fetch_exception_returns_none(self):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=Exception("API error"))
        result = await fetch_token_scopes(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_tokens_not_a_list_returns_none(self):
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
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=["not a dict"])
        result = await fetch_token_scopes(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_scopes(self):
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

    @pytest.mark.asyncio
    async def test_successful_fetch_all_scope(self):
        token_val = "all-scope-token"
        last_eight = token_val[-8:]
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                [{"id": 1, "name": "t1", "token_last_eight": last_eight, "scopes": ["all"]}],
            ]
        )
        result = await fetch_token_scopes(mock_client, token_val)
        assert result == {"all"}

    @pytest.mark.asyncio
    async def test_tokens_fetch_exception_returns_none(self):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"login": "testuser"},
                Exception("tokens API error"),
            ]
        )
        result = await fetch_token_scopes(mock_client, "test-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_user_missing_login_uses_unknown(self):
        token_val = "no-match-token"
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                {"id": 1},
                [{"id": 1, "name": "t1", "token_last_eight": "aaaaaaaa", "scopes": ["sudo"]}],
            ]
        )
        result = await fetch_token_scopes(mock_client, token_val)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# _compute_excluded_routes (spec-prep filtering)
# ═══════════════════════════════════════════════════════════════════════


class TestComputeExcludedRoutes:
    """Tests for _compute_excluded_routes — the spec-level filter decision."""

    def _spec(self) -> dict:
        return {
            "openapi": "3.1.1",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {
                        "operationId": "repo_get",
                        "tags": ["repository"],
                    },
                },
                "/admin/users": {
                    "get": {
                        "operationId": "admin_list_users",
                        "tags": ["admin"],
                    },
                },
                "/old/endpoint": {
                    "post": {
                        "operationId": "oldEndpoint",
                        "deprecated": True,
                    },
                },
            },
            "components": {"schemas": {}},
        }

    def test_empty_filtered_info_returns_empty(self):
        excluded = _compute_excluded_routes(self._spec(), {})
        assert excluded == set()

    def test_excludes_scope_filtered_operation(self):
        filtered = compute_filtered_tools_info(
            self._spec(),
            available_scopes={"read:repository"},  # admin_list_users needs read:admin
            exclusion_config={"exclude": [], "include": []},
            tool_prefix="",
        )
        excluded = _compute_excluded_routes(self._spec(), filtered)
        assert ("/admin/users", "GET") in excluded
        assert ("/repos/{owner}/{repo}", "GET") not in excluded

    def test_excludes_deprecated_operation(self):
        filtered = compute_filtered_tools_info(
            self._spec(),
            available_scopes={"sudo"},  # sees everything
            exclusion_config={"exclude": [], "include": []},
            tool_prefix="",
        )
        excluded = _compute_excluded_routes(self._spec(), filtered)
        assert ("/old/endpoint", "POST") in excluded

    def test_excludes_config_excluded_operation(self):
        filtered = compute_filtered_tools_info(
            self._spec(),
            available_scopes={"sudo"},
            exclusion_config={"exclude": ["repo_get"], "include": []},
            tool_prefix="",
        )
        excluded = _compute_excluded_routes(self._spec(), filtered)
        assert ("/repos/{owner}/{repo}", "GET") in excluded

    def test_include_overrides_exclude(self):
        filtered = compute_filtered_tools_info(
            self._spec(),
            available_scopes={"sudo"},
            exclusion_config={"exclude": ["repo_get"], "include": ["repo_get"]},
            tool_prefix="",
        )
        excluded = _compute_excluded_routes(self._spec(), filtered)
        assert ("/repos/{owner}/{repo}", "GET") not in excluded

    def test_matches_prefixed_operation_id(self):
        filtered = compute_filtered_tools_info(
            self._spec(),
            available_scopes={"sudo"},
            exclusion_config={"exclude": ["gitea_repo_get"], "include": []},
            tool_prefix="gitea_",
        )
        excluded = _compute_excluded_routes(self._spec(), filtered, tool_prefix="gitea_")
        assert ("/repos/{owner}/{repo}", "GET") in excluded


# ═══════════════════════════════════════════════════════════════════════
# create_openapi_provider — route_map_fn drops filtered operations
# ═══════════════════════════════════════════════════════════════════════


class TestProviderRouteMapFiltering:
    """Integration-level unit tests: filtered routes never become tools."""

    def _make_provider(self, excluded_routes, response_format="markdown") -> MagicMock:
        from gitea_mcp_server.label_service import LabelService

        spec = {
            "openapi": "3.1.1",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {"operationId": "repo_get", "tags": ["repository"]},
                },
                "/admin/users": {
                    "get": {"operationId": "admin_list_users", "tags": ["admin"]},
                },
            },
            "components": {"schemas": {}},
        }
        mock_gitea_client = MagicMock()
        mock_gitea_client.client = MagicMock()
        return create_openapi_provider(
            openapi_spec=spec,
            gitea_client=mock_gitea_client,
            label_service=LabelService(),
            excluded_routes=excluded_routes,
            response_format=response_format,
        )

    def test_no_exclusions_keeps_all(self, caplog):
        import logging

        caplog.set_level(logging.DEBUG)
        provider = self._make_provider(set())
        assert provider is not None
        assert "Excluding filtered endpoint" not in caplog.text

    def test_excluded_route_is_dropped(self, caplog):
        import logging

        caplog.set_level(logging.DEBUG)
        provider = self._make_provider({("/admin/users", "GET")})
        assert provider is not None
        assert "Excluding filtered endpoint" in caplog.text

    @pytest.mark.asyncio
    async def test_filtered_route_not_in_tool_list(self):
        provider = self._make_provider({("/admin/users", "GET")})
        tools = await provider.list_tools()
        names = {t.name for t in tools}
        assert "admin_list_users" not in names
        assert "repo_get" in names
