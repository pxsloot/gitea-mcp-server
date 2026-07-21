"""Unit tests for filter_info module — filter-prediction computation and messaging.

Tests ``compute_filtered_tools_info``, ``get_filtered_tool_info``,
``build_filtered_tools_message``, and ``FilteredToolMiddleware`` using
pure dict-in/dict-out patterns with minimal OpenAPI spec fixtures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ToolError

from gitea_mcp_server.tools.filter_info import (
    FilteredToolMiddleware,
    build_filtered_tools_message,
    compute_filtered_tools_info,
    get_filtered_tool_info,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def empty_spec() -> dict:
    """Minimal OpenAPI 3.1 spec with no paths."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1"},
        "paths": {},
    }


@pytest.fixture
def spec_with_one_endpoint() -> dict:
    """Spec with a single GET /repos/{owner}/{repo} endpoint."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1"},
        "paths": {
            "/repos/{owner}/{repo}": {
                "get": {
                    "operationId": "repo_get",
                    "tags": ["repository"],
                    "summary": "Get a repository",
                    "responses": {
                        "200": {"description": "OK"},
                    },
                },
            },
        },
    }


@pytest.fixture
def spec_with_deprecated_endpoint() -> dict:
    """Spec with a deprecated endpoint."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1"},
        "paths": {
            "/old/thing": {
                "get": {
                    "operationId": "old_get_thing",
                    "tags": ["repository"],
                    "deprecated": True,
                    "summary": "Old deprecated endpoint",
                    "responses": {
                        "200": {"description": "OK"},
                    },
                },
            },
        },
    }


@pytest.fixture
def spec_with_admin_endpoint() -> dict:
    """Spec with an admin-only endpoint."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1"},
        "paths": {
            "/admin/users": {
                "get": {
                    "operationId": "admin_list_users",
                    "tags": ["admin"],
                    "summary": "List users (admin)",
                    "responses": {
                        "200": {"description": "OK"},
                    },
                },
            },
        },
    }


@pytest.fixture
def spec_with_mixed_endpoints() -> dict:
    """Spec with several endpoints: one visible, one scope-restricted,
    one deprecated, one config-excluded."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1"},
        "paths": {
            "/repos/{owner}/{repo}": {
                "get": {
                    "operationId": "repo_get",
                    "tags": ["repository"],
                    "summary": "Get a repo",
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/admin/users": {
                "get": {
                    "operationId": "admin_list_users",
                    "tags": ["admin"],
                    "summary": "List users (admin)",
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/old/endpoint": {
                "get": {
                    "operationId": "old_get_endpoint",
                    "tags": ["repository"],
                    "deprecated": True,
                    "summary": "Old endpoint",
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/repos/{owner}/{repo}/hidden": {
                "get": {
                    "operationId": "repo_get_hidden",
                    "tags": ["repository"],
                    "summary": "Hidden repo endpoint",
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# compute_filtered_tools_info
# ═══════════════════════════════════════════════════════════════════════


class TestComputeFilteredToolsInfo:
    """Main computation function that iterates the spec."""

    def test_empty_spec_returns_empty_result(self, empty_spec):
        """No paths → no filtered operations."""
        result = compute_filtered_tools_info(empty_spec)
        assert result["filtered"] == {}
        assert result["available_scopes"] == []

    def test_no_scope_data_no_exclusions_no_filtering(self, spec_with_one_endpoint):
        """available_scopes=None → no scope-based filtering."""
        result = compute_filtered_tools_info(spec_with_one_endpoint, available_scopes=None)
        assert result["filtered"] == {}

    def test_sufficient_scope_no_filtering(self, spec_with_one_endpoint):
        """Token has read:repository → repo_get is visible."""
        result = compute_filtered_tools_info(
            spec_with_one_endpoint,
            available_scopes={"read:repository"},
        )
        assert result["filtered"] == {}

    def test_insufficient_scope_filters_endpoint(self, spec_with_admin_endpoint):
        """Token lacks sudo → admin_list_users is scope-restricted."""
        result = compute_filtered_tools_info(
            spec_with_admin_endpoint,
            available_scopes={"read:repository"},
        )
        filtered = result["filtered"]
        assert "admin_list_users" in filtered
        assert filtered["admin_list_users"]["reason"] == "scope"
        assert filtered["admin_list_users"]["required_scope"] == "sudo"

    def test_deprecated_endpoint_filtered(self, spec_with_deprecated_endpoint):
        """Endpoint with deprecated:true → filtered as deprecated."""
        result = compute_filtered_tools_info(
            spec_with_deprecated_endpoint,
            available_scopes={"read:repository"},
        )
        filtered = result["filtered"]
        assert "old_get_thing" in filtered
        assert filtered["old_get_thing"]["reason"] == "deprecated"

    def test_exclusion_config_excludes_tool(self, spec_with_one_endpoint):
        """Exact name in exclude list → filtered as excluded."""
        result = compute_filtered_tools_info(
            spec_with_one_endpoint,
            exclusion_config={"exclude": ["repo_get"], "include": []},
        )
        filtered = result["filtered"]
        assert "repo_get" in filtered
        assert filtered["repo_get"]["reason"] == "excluded"

    def test_include_overrides_exclude(self, spec_with_one_endpoint):
        """Tool matching both include and exclude → visible (include wins)."""
        result = compute_filtered_tools_info(
            spec_with_one_endpoint,
            exclusion_config={"exclude": ["repo_*"], "include": ["repo_get"]},
        )
        assert result["filtered"] == {}

    def test_mixed_endpoints_multiple_reasons(self, spec_with_mixed_endpoints):
        """Verify that different endpoints are filtered for different reasons."""
        result = compute_filtered_tools_info(
            spec_with_mixed_endpoints,
            available_scopes={"read:repository"},
            exclusion_config={"exclude": ["repo_get_hidden"], "include": []},
        )
        filtered = result["filtered"]

        # Visible — not filtered
        assert "repo_get" not in filtered

        # Scope-restricted
        assert filtered["admin_list_users"]["reason"] == "scope"
        assert filtered["admin_list_users"]["required_scope"] == "sudo"

        # Deprecated
        assert filtered["old_get_endpoint"]["reason"] == "deprecated"

        # Config-excluded
        assert filtered["repo_get_hidden"]["reason"] == "excluded"

        assert len(filtered) == 3

    def test_available_scopes_in_result(self, spec_with_one_endpoint):
        """The available_scopes list should be reflected in the result."""
        result = compute_filtered_tools_info(
            spec_with_one_endpoint,
            available_scopes={"read:repository", "write:issue"},
        )
        assert "read:repository" in result["available_scopes"]
        assert "write:issue" in result["available_scopes"]
        assert len(result["available_scopes"]) == 2

    def test_exclusion_config_in_result(self, spec_with_one_endpoint):
        """The exclusion config patterns should be reflected in the result."""
        result = compute_filtered_tools_info(
            spec_with_one_endpoint,
            exclusion_config={"exclude": ["foo"], "include": ["bar"]},
        )
        assert result["exclusion_config"]["exclude"] == ["foo"]
        assert result["exclusion_config"]["include"] == ["bar"]

    def test_post_method_write_scope(self):
        """POST endpoints require write: scope."""
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1"},
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "issue_create_issue",
                        "tags": ["issue"],
                        "summary": "Create an issue",
                        "responses": {"201": {"description": "Created"}},
                    },
                },
            },
        }
        # Token has read-only → write operation is filtered
        result = compute_filtered_tools_info(
            spec, available_scopes={"read:issue"}
        )
        assert "issue_create_issue" in result["filtered"]
        assert result["filtered"]["issue_create_issue"]["reason"] == "scope"
        assert result["filtered"]["issue_create_issue"]["required_scope"] == "write:issue"

        # Token has write → visible
        result2 = compute_filtered_tools_info(
            spec, available_scopes={"write:issue"}
        )
        assert result2["filtered"] == {}


# ═══════════════════════════════════════════════════════════════════════
# get_filtered_tool_info
# ═══════════════════════════════════════════════════════════════════════


class TestGetFilteredToolInfo:
    """Lookup helper — finds a tool's filter info by name."""

    def test_none_data_returns_none(self):
        """Passing None for filtered_tools_info returns None."""
        assert get_filtered_tool_info("any_tool", None) is None

    def test_empty_filtered_returns_none(self):
        """Empty filtered dict returns None."""
        info = {"filtered": {}}
        assert get_filtered_tool_info("any_tool", info) is None

    def test_tool_not_in_filtered_returns_none(self):
        """Tool not in the filtered set returns None."""
        info = {"filtered": {"other_tool": {"reason": "scope"}}}
        assert get_filtered_tool_info("my_tool", info) is None

    def test_finds_tool_by_bare_name(self):
        """Bare operationId matches directly."""
        info = {"filtered": {"repo_get": {"reason": "deprecated"}}}
        result = get_filtered_tool_info("repo_get", info)
        assert result is not None
        assert result["reason"] == "deprecated"

    def test_strips_prefix_to_find_tool(self):
        """Prefixed name is stripped before lookup."""
        info = {"filtered": {"repo_get": {"reason": "scope", "required_scope": "sudo"}}}
        result = get_filtered_tool_info("gitea_repo_get", info, tool_prefix="gitea_")
        assert result is not None
        assert result["reason"] == "scope"

    def test_prefix_not_stripped_if_no_match(self,):
        """If prefix is not present, look up with the bare name."""
        info = {"filtered": {"repo_get": {"reason": "deprecated"}}}
        result = get_filtered_tool_info("repo_get", info, tool_prefix="gitea_")
        assert result is not None
        assert result["reason"] == "deprecated"


# ═══════════════════════════════════════════════════════════════════════
# build_filtered_tools_message
# ═══════════════════════════════════════════════════════════════════════


class TestBuildFilteredToolsMessage:
    """Agent-facing error message formatting."""

    def test_scope_reason_mentions_required_scope(self):
        """Scope-filtered message includes 'Required scope'."""
        entry = {"reason": "scope", "required_scope": "sudo"}
        msg = build_filtered_tools_message("admin_list_users", entry)
        assert "admin_list_users" in msg
        assert "sudo" in msg
        assert "restricted by your token scopes" in msg

    def test_scope_reason_includes_available_scopes(self):
        """When filtered_tools_info has available_scopes, include them."""
        entry = {"reason": "scope", "required_scope": "sudo"}
        info = {"available_scopes": ["read:repository", "write:issue"]}
        msg = build_filtered_tools_message("admin_list_users", entry, info)
        assert "read:repository" in msg
        assert "write:issue" in msg

    def test_scope_reason_without_available_scopes(self):
        """Scope message works even when filtered_tools_info is None."""
        entry = {"reason": "scope", "required_scope": "sudo"}
        msg = build_filtered_tools_message("admin_list_users", entry)
        assert "sudo" in msg
        assert "search_tools()" in msg

    def test_excluded_reason(self):
        """Excluded message mentions server configuration."""
        entry = {"reason": "excluded"}
        msg = build_filtered_tools_message("repo_get_hidden", entry)
        assert "repo_get_hidden" in msg
        assert "excluded by server configuration" in msg

    def test_deprecated_reason(self):
        """Deprecated message mentions Gitea API deprecation."""
        entry = {"reason": "deprecated"}
        msg = build_filtered_tools_message("old_get_thing", entry)
        assert "old_get_thing" in msg
        assert "deprecated by the Gitea API" in msg

    def test_unknown_reason(self):
        """Unknown reason produces a generic message."""
        entry = {"reason": "some_weird_reason"}
        msg = build_filtered_tools_message("mystery_tool", entry)
        assert "mystery_tool" in msg
        assert "not available" in msg
        assert "some_weird_reason" in msg


# ═══════════════════════════════════════════════════════════════════════
# FilteredToolMiddleware
# ═══════════════════════════════════════════════════════════════════════


class TestFilteredToolMiddleware:
    """Tests for FilteredToolMiddleware — intercepts tool calls to filtered tools."""

    @pytest.fixture
    def scope_filter_info(self) -> dict:
        return {
            "available_scopes": ["read:repository"],
            "exclusion_config": {"exclude": [], "include": []},
            "filtered": {
                "admin_create_user": {
                    "reason": "scope",
                    "required_scope": "sudo",
                },
            },
        }

    @pytest.fixture
    def exclude_filter_info(self) -> dict:
        return {
            "available_scopes": ["read:repository", "write:issue"],
            "exclusion_config": {"exclude": ["admin_*"], "include": []},
            "filtered": {
                "admin_create_user": {
                    "reason": "excluded",
                },
            },
        }

    @pytest.fixture
    def deprecated_filter_info(self) -> dict:
        return {
            "available_scopes": ["read:repository"],
            "exclusion_config": {"exclude": [], "include": []},
            "filtered": {
                "some_deprecated_tool": {
                    "reason": "deprecated",
                },
            },
        }

    def _make_mock_context(self, tool_name: str) -> MagicMock:
        """Create a mock MiddlewareContext with the given tool name."""
        ctx = MagicMock()
        ctx.message.name = tool_name
        ctx.message.arguments = {}
        return ctx

    @pytest.mark.asyncio
    async def test_passes_through_for_visible_tool(self, scope_filter_info):
        """Middleware should pass through for tools not in filter info."""
        middleware = FilteredToolMiddleware(
            filtered_tools_info=scope_filter_info,
            tool_prefix="gitea_",
        )
        ctx = self._make_mock_context("gitea_issue_list_issues")
        call_next = AsyncMock(return_value="result")

        result = await middleware.on_call_tool(ctx, call_next)
        assert result == "result"
        call_next.assert_called_once_with(ctx)

    @pytest.mark.asyncio
    async def test_passes_through_when_no_filter_info(self):
        """Middleware should pass through when filtered_tools_info is None."""
        middleware = FilteredToolMiddleware(filtered_tools_info=None)
        ctx = self._make_mock_context("gitea_admin_create_user")
        call_next = AsyncMock(return_value="result")

        result = await middleware.on_call_tool(ctx, call_next)
        assert result == "result"
        call_next.assert_called_once_with(ctx)

    @pytest.mark.asyncio
    async def test_scope_filtered_raises_tool_error(self, scope_filter_info):
        """Scope-restricted tool should raise ToolError with scope message."""
        middleware = FilteredToolMiddleware(
            filtered_tools_info=scope_filter_info,
            tool_prefix="gitea_",
        )
        ctx = self._make_mock_context("gitea_admin_create_user")
        call_next = AsyncMock()

        with pytest.raises(ToolError, match="restricted by your token scopes"):
            await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_exclude_filtered_raises_tool_error(self, exclude_filter_info):
        """Config-excluded tool should raise ToolError with exclusion message."""
        middleware = FilteredToolMiddleware(
            filtered_tools_info=exclude_filter_info,
            tool_prefix="gitea_",
        )
        ctx = self._make_mock_context("gitea_admin_create_user")
        call_next = AsyncMock()

        with pytest.raises(ToolError, match="excluded by server configuration"):
            await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_deprecated_filtered_raises_tool_error(self, deprecated_filter_info):
        """Deprecated tool should raise ToolError with deprecation message."""
        middleware = FilteredToolMiddleware(
            filtered_tools_info=deprecated_filter_info,
            tool_prefix="gitea_",
        )
        ctx = self._make_mock_context("gitea_some_deprecated_tool")
        call_next = AsyncMock()

        with pytest.raises(ToolError, match="has been deprecated"):
            await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_works_without_prefix(self, scope_filter_info):
        """Middleware works with unprefixed tool names."""
        middleware = FilteredToolMiddleware(
            filtered_tools_info=scope_filter_info,
            tool_prefix="",
        )
        ctx = self._make_mock_context("admin_create_user")
        call_next = AsyncMock()

        with pytest.raises(ToolError, match="restricted by your token scopes"):
            await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_called()
