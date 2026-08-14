"""Unit tests for cache invalidation functionality."""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.middleware.caching import ResponseCachingMiddleware

from gitea_mcp_server.cache_invalidation import (
    TOOL_INVALIDATION_MAP,
    CacheInvalidationMiddleware,
    _compute_cache_key,
    _substitute_template,
    compute_uris_to_invalidate,
    invalidate_cached_resources,
    register_tool_invalidation,
)
from gitea_mcp_server.tools.customize import (
    compute_invalidation_patterns as _compute_tool_invalidation_patterns,
)


@pytest.fixture(autouse=True)
def clear_invalidation_map() -> Generator[None, None, None]:
    """Clear the invalidation map before each test."""
    TOOL_INVALIDATION_MAP.clear()
    yield
    TOOL_INVALIDATION_MAP.clear()


class TestComputeCacheKey:
    """Tests for _compute_cache_key function."""

    def test_consistent_hashing(self) -> None:
        """Same URI produces same hash."""
        uri = "gitea://repos/owner/repo/issues"
        key1 = _compute_cache_key(uri)
        key2 = _compute_cache_key(uri)
        assert key1 == key2
        assert len(key1) == 64  # SHA256 hex digest length

    def test_different_uris_different_keys(self) -> None:
        """Different URIs produce different hashes."""
        uri1 = "gitea://repos/owner/repo/issues"
        uri2 = "gitea://repos/owner/repo/pulls"
        key1 = _compute_cache_key(uri1)
        key2 = _compute_cache_key(uri2)
        assert key1 != key2


class TestSubstituteTemplate:
    """Tests for _substitute_template function."""

    def test_simple_substitution(self) -> None:
        """Basic parameter substitution."""
        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "myorg", "repo": "myrepo"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/myorg/myrepo/issues"

    def test_multiple_parameters(self) -> None:
        """Multiple parameters are all substituted."""
        template = "gitea://repos/{owner}/{repo}/files/{path}"
        params = {"owner": "org", "repo": "repo", "path": "src/main.py"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/org/repo/files/src/main.py"

    def test_missing_parameter_raises(self) -> None:
        """Missing required parameter raises ValueError."""
        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org"}  # missing repo
        with pytest.raises(ValueError, match="Missing parameters"):
            _substitute_template(template, params)

    def test_extra_parameters_ignored(self) -> None:
        """Extra parameters not in template are ignored."""
        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org", "repo": "repo", "extra": "ignored"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/org/repo/issues"

    def test_wildcard_parameter(self) -> None:
        """Wildcard parameters are handled."""
        template = "gitea://repos/{owner}/{repo}/files/{path*}"
        params = {"owner": "org", "repo": "repo", "path": "docs/guide/intro.md"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/org/repo/files/docs/guide/intro.md"


class TestComputeUrisToInvalidate:
    """Tests for compute_uris_to_invalidate function."""

    def test_issue_edit_invalidates_issues(self) -> None:
        """issue_edit_issue invalidates issues list."""
        register_tool_invalidation("issue_edit_issue", ["issues_list"])
        arguments = {"owner": "myorg", "repo": "myrepo", "index": 42}
        uris = compute_uris_to_invalidate("issue_edit_issue", arguments)
        expected = [
            "gitea://repos/myorg/myrepo/issues",
        ]
        assert set(uris) == set(expected)

    def test_issue_create_invalidates_issues(self) -> None:
        """issue_create_repo_issue invalidates issues list."""
        register_tool_invalidation("issue_create_repo_issue", ["issues_list"])
        arguments = {"owner": "org", "repo": "repo", "title": "Bug"}
        uris = compute_uris_to_invalidate("issue_create_repo_issue", arguments)
        expected = [
            "gitea://repos/org/repo/issues",
        ]
        assert set(uris) == set(expected)

    def test_pr_create_invalidates_pulls(self) -> None:
        """pull_request_create invalidates pulls list."""
        register_tool_invalidation("pull_request_create", ["pulls_list"])
        arguments = {"owner": "org", "repo": "repo", "head": "feature", "base": "main"}
        uris = compute_uris_to_invalidate("pull_request_create", arguments)
        expected = [
            "gitea://repos/org/repo/pulls",
        ]
        assert set(uris) == set(expected)

    def test_unknown_tool_returns_empty(self) -> None:
        """Unknown tool returns empty list."""
        arguments = {"owner": "org", "repo": "repo"}
        uris = compute_uris_to_invalidate("unknown_tool", arguments)
        assert uris == []

    def test_repo_edit_invalidates_repo_resource(self) -> None:
        """repo_edit invalidates repository resource."""
        register_tool_invalidation("repo_edit", ["repo"])
        arguments = {"owner": "org", "repo": "repo"}
        uris = compute_uris_to_invalidate("repo_edit", arguments)
        assert uris == ["gitea://repos/org/repo"]

    def test_file_operation_invalidates_file_resource(self) -> None:
        """repo_create_content invalidates file resource with correct path."""
        register_tool_invalidation("repo_create_content", ["files"])
        arguments = {
            "owner": "org",
            "repo": "repo",
            "filepath": "README.md",  # note: filepath matches pattern placeholder
        }
        uris = compute_uris_to_invalidate("repo_create_content", arguments)
        # Should have at least one URI containing the path
        assert any("README.md" in uri for uri in uris)
        assert "gitea://repos/org/repo/files/README.md" in uris

    def test_missing_parameters_skipped(self) -> None:
        """If required parameters are missing, pattern is skipped gracefully."""
        register_tool_invalidation("issue_edit_issue", ["issues_list"])
        # issue_edit_issue needs owner, repo, index
        arguments = {"owner": "org"}  # missing repo and index
        uris = compute_uris_to_invalidate("issue_edit_issue", arguments)
        # Should return empty because patterns can't be substituted
        assert uris == []

    def test_unknown_pattern_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown pattern name logs a warning."""
        import logging

        caplog.set_level(logging.WARNING)

        register_tool_invalidation("some_tool", ["unknown_pattern"])
        arguments = {"owner": "org", "repo": "repo"}
        uris = compute_uris_to_invalidate("some_tool", arguments)
        assert uris == []
        assert "Unknown resource pattern" in caplog.text
        assert "unknown_pattern" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_uris_list_noop(self) -> None:
        """invalidate_cached_resources with empty list returns immediately."""
        from unittest.mock import MagicMock

        mock_caching = MagicMock()
        await invalidate_cached_resources(mock_caching, [], "test_tool")

    @pytest.mark.asyncio
    async def test_cache_delete_key_error_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """KeyError during cache delete is caught and logged."""
        import logging

        caplog.set_level(logging.WARNING)

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()  # exists
        mock_cache.delete.side_effect = KeyError("cache key not found")

        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        register_tool_invalidation("issue_edit_issue", ["issues_list"])
        uris = compute_uris_to_invalidate(
            "issue_edit_issue", {"owner": "org", "repo": "repo", "index": 1}
        )

        await invalidate_cached_resources(mock_caching, uris, "issue_edit_issue")
        assert "Failed to invalidate cache" in caplog.text


class TestCacheInvalidationMiddleware:
    """Tests for CacheInvalidationMiddleware behavior."""

    @pytest.mark.asyncio
    async def test_successful_tool_invalidates_cache(self) -> None:
        """Successful tool call triggers cache invalidation."""
        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        register_tool_invalidation("issue_edit_issue", ["issues_list"])

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        assert mock_cache.delete.called

    @pytest.mark.asyncio
    async def test_error_tool_no_invalidation(self) -> None:
        """Failed tool call does not invalidate cache."""
        mock_cache = AsyncMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=True)

        await middleware.on_call_tool(mock_context, mock_call_next)

        assert not mock_cache.delete.called

    @pytest.mark.asyncio
    async def test_unknown_tool_no_invalidation(self) -> None:
        """Tool not in invalidation map does not trigger invalidation."""
        mock_cache = AsyncMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "some_unknown_tool"
        mock_context.message.arguments = {}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        assert not mock_cache.delete.called

    @pytest.mark.asyncio
    async def test_missing_read_resource_cache_graceful(self) -> None:
        """Graceful degradation when _read_resource_cache attribute is missing."""
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        del mock_caching._read_resource_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        register_tool_invalidation("issue_edit_issue", ["issues_list"])

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

    @pytest.mark.asyncio
    async def test_invalidate_cached_resources_missing_attribute(self) -> None:
        """invalidate_cached_resources handles missing _read_resource_cache gracefully."""
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        del mock_caching._read_resource_cache

        await invalidate_cached_resources(
            mock_caching, ["gitea://repos/org/repo/issues"], "test_tool"
        )


class TestComputeToolInvalidationPatterns:
    """Tests for _compute_tool_invalidation_patterns from server module."""

    def test_issue_paths_invalidate_issues(self) -> None:
        """Paths under /issues trigger invalidations for issues resources."""
        assert self.compute("/repos/{owner}/{repo}/issues", "POST") == [
            "issues_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/issues/42", "DELETE") == [
            "issues_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/issues/42/labels", "PUT") == [
            "issues_list",
        ]

    def test_pull_paths_invalidate_pulls(self) -> None:
        """Paths under /pulls trigger invalidations for pulls resources."""
        assert self.compute("/repos/{owner}/{repo}/pulls", "POST") == [
            "pulls_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/pulls/5", "DELETE") == [
            "pulls_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/pulls/5/merge", "POST") == [
            "pulls_list",
        ]

    def test_repo_path_invalidates_repo(self) -> None:
        """Direct repo modification invalidates repository resource."""
        assert self.compute("/repos/{owner}/{repo}", "PUT") == ["repo"]
        assert self.compute("/repos/{owner}/{repo}", "DELETE") == ["repo"]
        assert self.compute("/repos/{owner}/{repo}", "PATCH") == ["repo"]

    def test_file_contents_invalidate_files(self) -> None:
        """File contents modifications invalidate file resource."""
        assert self.compute("/repos/{owner}/{repo}/contents/README.md", "PUT") == ["files"]
        assert self.compute("/repos/{owner}/{repo}/contents/src/main.py", "DELETE") == ["files"]
        # GET does not invalidate
        assert self.compute("/repos/{owner}/{repo}/contents/README.md", "GET") == []

    def test_label_operations_invalidate_labels_issues_and_pulls(self) -> None:
        """Label CRUD affects labels, issues, and pull requests."""
        assert self.compute("/repos/{owner}/{repo}/labels", "POST") == [
            "labels",
            "issues_list",
            "pulls_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/labels/bug", "DELETE") == [
            "labels",
            "issues_list",
            "pulls_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/labels", "PATCH") == [
            "labels",
            "issues_list",
            "pulls_list",
        ]

    def test_milestone_operations_invalidate_issues_and_pulls(self) -> None:
        """Milestone CRUD affects both issues and pull requests."""
        assert self.compute("/repos/{owner}/{repo}/milestones", "POST") == [
            "issues_list",
            "pulls_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/milestones/1", "PATCH") == [
            "issues_list",
            "pulls_list",
        ]
        assert self.compute("/repos/{owner}/{repo}/milestones/1", "DELETE") == [
            "issues_list",
            "pulls_list",
        ]

    def test_release_operations_invalidate_repo(self) -> None:
        """Release CRUD affects repository resource."""
        assert self.compute("/repos/{owner}/{repo}/releases", "POST") == ["repo"]
        assert self.compute("/repos/{owner}/{repo}/releases/v1.0", "DELETE") == ["repo"]

    def test_topic_operations_invalidate_repo(self) -> None:
        """Topic changes affect repository resource."""
        assert self.compute("/repos/{owner}/{repo}/topics", "PUT") == ["repo"]
        assert self.compute("/repos/{owner}/{repo}/topics", "DELETE") == ["repo"]

    def test_safe_methods_return_empty(self) -> None:
        """Safe methods (GET, HEAD, OPTIONS) do not invalidate."""
        assert self.compute("/repos/{owner}/{repo}/issues", "GET") == []
        assert self.compute("/repos/{owner}/{repo}/issues", "HEAD") == []
        assert self.compute("/repos/{owner}/{repo}/pulls", "OPTIONS") == []

    def compute(self, path: str, method: str) -> list[str]:
        """Helper to call _compute_tool_invalidation_patterns."""
        return _compute_tool_invalidation_patterns(path, method)


class TestIntegration:
    """Integration tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_close_issue_invalidates_resources(self) -> None:
        """Closing an issue via issue_edit_issue invalidates relevant caches."""
        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        register_tool_invalidation("issue_edit_issue", ["issues_list"])

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {
            "owner": "testorg",
            "repo": "testrepo",
            "index": 5,
            "state": "closed",
        }

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        deleted_uris = [call[1]["key"] for call in mock_cache.delete.call_args_list]
        expected_key = _compute_cache_key("gitea://repos/testorg/testrepo/issues")
        assert deleted_uris == [expected_key]


class TestClearLabelServiceCache:
    """Tests for CacheInvalidationMiddleware._clear_label_service_cache."""

    @pytest.mark.asyncio
    async def test_label_uri_clears_label_cache(self) -> None:
        """URI ending with /labels clears LabelService cache for that repo."""
        from unittest.mock import AsyncMock, MagicMock

        from gitea_mcp_server.label_service import LabelService

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        label_service = MagicMock(spec=LabelService)
        middleware = CacheInvalidationMiddleware(mock_caching, label_service=label_service)

        register_tool_invalidation("repo_create_label", ["labels"])

        mock_context = MagicMock()
        mock_context.message.name = "repo_create_label"
        mock_context.message.arguments = {"owner": "myorg", "repo": "myrepo"}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        # LabelService cache should be cleared for myorg/myrepo
        label_service.clear_cache_for.assert_called_once_with("myorg", "myrepo")

    @pytest.mark.asyncio
    async def test_non_label_uri_does_not_clear_label_cache(self) -> None:
        """URI not ending with /labels does not clear LabelService cache."""
        from unittest.mock import AsyncMock, MagicMock

        from gitea_mcp_server.label_service import LabelService

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        label_service = MagicMock(spec=LabelService)
        middleware = CacheInvalidationMiddleware(mock_caching, label_service=label_service)

        register_tool_invalidation("issue_edit_issue", ["issues_list"])

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_label_service_skips_gracefully(self) -> None:
        """When label_service is None, no error is raised."""
        from unittest.mock import AsyncMock, MagicMock

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching, label_service=None)

        register_tool_invalidation("repo_create_label", ["labels"])

        mock_context = MagicMock()
        mock_context.message.name = "repo_create_label"
        mock_context.message.arguments = {"owner": "org", "repo": "repo"}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        # Should not raise even though label_service is None
        await middleware.on_call_tool(mock_context, mock_call_next)

    # ------------------------------------------------------------------
    # URI parsing edge cases — tested through on_call_tool with patched
    # compute_uris_to_invalidate to inject edge-case URIs that cannot
    # arise from the normal invalidation system (all registered patterns
    # produce well-formed gitea://repos/.../labels URIs).
    # ------------------------------------------------------------------

    def _make_middleware(self, label_service: Any = None) -> CacheInvalidationMiddleware:
        """Helper: create CacheInvalidationMiddleware with a mock cache and optional label_service."""
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.middleware.caching import ResponseCachingMiddleware

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache
        return CacheInvalidationMiddleware(mock_caching, label_service=label_service)

    def _make_context_and_call_next(
        self, tool_name: str = "test_label_tool", arguments: dict[str, Any] | None = None
    ) -> tuple[MagicMock, Any]:
        """Helper: create mock context and call_next for on_call_tool tests."""
        from unittest.mock import MagicMock

        mock_context = MagicMock()
        mock_context.message.name = tool_name
        mock_context.message.arguments = arguments or {}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        return mock_context, mock_call_next

    @pytest.mark.asyncio
    async def test_uri_too_few_parts_skips(self) -> None:
        """URI with fewer than _MIN_LABEL_URI_PARTS parts does not call clear_cache_for."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        register_tool_invalidation("test_label_tool", ["labels"])
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_not_gitea_scheme_skips(self) -> None:
        """URI where parts[1] is not empty string skips.

        URIs like ``gitea:repos/owner/repo/labels`` (missing ``//``) produce
        fewer parts or no empty string at ``parts[1]``.
        """
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        register_tool_invalidation("test_label_tool", ["labels"])
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea:repos/owner/repo/labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_not_repos_segment_skips(self) -> None:
        """URI where parts[2] is not 'repos' skips."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        register_tool_invalidation("test_label_tool", ["labels"])
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://user/owner/repo/labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_with_empty_owner_skips(self) -> None:
        """URI with empty owner (parts[3] == '') skips."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        register_tool_invalidation("test_label_tool", ["labels"])
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://repos//repo/labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_with_empty_repo_skips(self) -> None:
        """URI with empty repo (parts[4] == '') skips."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        register_tool_invalidation("test_label_tool", ["labels"])
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://repos/owner//labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    def test_clear_label_service_cache_with_none_label_service(self) -> None:
        """Calling _clear_label_service_cache directly with label_service=None is a no-op."""
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.middleware.caching import ResponseCachingMiddleware

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching, label_service=None)
        # Direct call to the inner guard method — should not raise
        middleware._clear_label_service_cache(
            ["gitea://repos/org/repo/labels"], {"owner": "org", "repo": "repo"}
        )
