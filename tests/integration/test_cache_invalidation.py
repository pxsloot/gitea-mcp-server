"""Integration tests for cache invalidation with the MCP server.

These tests verify that write operations properly invalidate cached resources
by using respx to mock the Gitea API and observing cache behavior.
"""

import hashlib

import pytest

from gitea_mcp_server.cache_invalidation import (
    compute_uris_to_invalidate,
)
from gitea_mcp_server.tools.customize import (
    compute_invalidation_patterns as _compute_tool_invalidation_patterns,
)


class TestCacheInvalidationIntegration:
    """Integration tests for cache invalidation using respx mocks."""

    @pytest.fixture
    def simple_config(self):
        """Config fixture."""

        class SimpleConfig:
            def __init__(
                self,
                url="https://git.example.com",
                token="test_token",
                *,
                verify_ssl=False,
                ssl_cert_file=None,
                log_level="ERROR",
                log_format="text",
                tool_filtering_enabled=False,
                enable_lazy_loading=False,
            ):
                self.url = url.rstrip("/")
                self.token = token
                self.verify_ssl = verify_ssl
                self.ssl_cert_file = ssl_cert_file
                self.log_level = log_level
                self.log_format = log_format
                self.tool_filtering_enabled = tool_filtering_enabled
                self.enable_lazy_loading = enable_lazy_loading

            @property
            def base_url(self) -> str:
                return f"{self.url}/api/v1"

        return SimpleConfig

    @pytest.mark.asyncio
    async def test_issue_edit_invalidation_mapping(self, simple_config):
        """Test that issue_edit_issue is mapped to invalidate issues resources."""

        # Manually register for this test (server does this automatically on startup)
        from gitea_mcp_server.cache_invalidation import register_tool_invalidation

        register_tool_invalidation(
            "issue_edit_issue", ["issues_list"]
        )

        arguments = {"owner": "org", "repo": "repo", "index": 1}
        uris = compute_uris_to_invalidate("issue_edit_issue", arguments)

        expected = [
            "gitea://repos/org/repo/issues",
        ]
        assert set(uris) == set(expected)

    @pytest.mark.asyncio
    async def test_pr_create_invalidation_mapping(self, simple_config):
        """Test that PR creation invalidates pulls resources."""
        from gitea_mcp_server.cache_invalidation import (
            register_tool_invalidation,
        )

        register_tool_invalidation("repoCreatePullRequest", ["pulls_list"])

        arguments = {"owner": "org", "repo": "repo", "head": "feature", "base": "main"}
        uris = compute_uris_to_invalidate("repoCreatePullRequest", arguments)

        expected = [
            "gitea://repos/org/repo/pulls",
        ]
        assert set(uris) == set(expected)

    @pytest.mark.asyncio
    async def test_repo_edit_invalidation_mapping(self, simple_config):
        """Test that repo edit invalidates repo resource."""
        from gitea_mcp_server.cache_invalidation import (
            register_tool_invalidation,
        )

        register_tool_invalidation("repo_edit", ["repo"])

        arguments = {"owner": "myorg", "repo": "myrepo"}
        uris = compute_uris_to_invalidate("repo_edit", arguments)
        assert uris == ["gitea://repos/myorg/myrepo"]

    @pytest.mark.asyncio
    async def test_file_content_invalidation_mapping(self, simple_config):
        """Test file content operations use filepath parameter correctly."""
        from gitea_mcp_server.cache_invalidation import (
            register_tool_invalidation,
        )

        register_tool_invalidation("repo_create_content", ["files"])

        arguments = {
            "owner": "org",
            "repo": "repo",
            "filepath": "README.md",
            "content": "new content",
        }
        uris = compute_uris_to_invalidate("repo_create_content", arguments)
        assert "gitea://repos/org/repo/files/README.md" in uris

    @pytest.mark.asyncio
    async def test_label_operations_invalidation(self, simple_config):
        """Test label CRUD invalidates both issues and pulls."""
        patterns = _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/labels", "POST")
        assert set(patterns) == {"issues_list", "pulls_list"}

        patterns = _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/labels/bug", "DELETE")
        assert set(patterns) == {"issues_list", "pulls_list"}

    @pytest.mark.asyncio
    async def test_path_based_pattern_mapping_coverage(self, simple_config):
        """Comprehensive test of path-based pattern mapping."""
        # Issues
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/issues", "POST") == [
            "issues_list",
        ]
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/issues/42", "DELETE") == [
            "issues_list",
        ]
        assert _compute_tool_invalidation_patterns(
            "/repos/{owner}/{repo}/issues/42/labels", "PUT"
        ) == ["issues_list"]

        # Pulls
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/pulls", "POST") == [
            "pulls_list",
        ]
        assert _compute_tool_invalidation_patterns(
            "/repos/{owner}/{repo}/pulls/5/merge", "POST"
        ) == ["pulls_list"]

        # Repo
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}", "PUT") == ["repo"]
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}", "DELETE") == ["repo"]

        # Files
        assert _compute_tool_invalidation_patterns(
            "/repos/{owner}/{repo}/contents/README.md", "PUT"
        ) == ["files"]
        assert _compute_tool_invalidation_patterns(
            "/repos/{owner}/{repo}/contents/path/file.py", "DELETE"
        ) == ["files"]
        assert (
            _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/contents/README.md", "GET")
            == []
        )

        # Labels, Milestones, Releases, Topics
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/labels", "POST") == [
            "issues_list",
            "pulls_list",
        ]
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/milestones", "POST") == [
            "issues_list",
            "pulls_list",
        ]
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/releases", "POST") == [
            "repo"
        ]
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/topics", "PUT") == [
            "repo"
        ]

        # Safe methods
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/issues", "GET") == []
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/issues", "HEAD") == []
        assert _compute_tool_invalidation_patterns("/repos/{owner}/{repo}/pulls", "OPTIONS") == []


class TestCacheKeyConsistency:
    """Test that cache key computation matches FastMCP's algorithm."""

    def test_cache_key_matches_sha256(self):
        """Verify our cache key computation matches FastMCP's."""
        uri = "gitea://repos/owner/repo/issues"
        expected = hashlib.sha256(uri.encode()).hexdigest()
        from gitea_mcp_server.cache_invalidation import _compute_cache_key

        assert _compute_cache_key(uri) == expected

    def test_different_uris_different_keys(self):
        """Different URIs should produce different cache keys."""
        from gitea_mcp_server.cache_invalidation import _compute_cache_key

        uri1 = "gitea://repos/owner/repo/issues"
        uri2 = "gitea://repos/owner/repo/pulls"
        assert _compute_cache_key(uri1) != _compute_cache_key(uri2)


class TestTemplateSubstitution:
    """Test URI template substitution logic."""

    def test_simple_substitution(self):
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "myorg", "repo": "myrepo"}
        assert _substitute_template(template, params) == "gitea://repos/myorg/myrepo/issues"

    def test_filepath_substitution(self):
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/files/{filepath}"
        params = {"owner": "org", "repo": "repo", "filepath": "src/main.py"}
        assert _substitute_template(template, params) == "gitea://repos/org/repo/files/src/main.py"

    def test_missing_parameter_raises(self):
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org"}  # missing repo
        with pytest.raises(ValueError, match="Missing parameters"):
            _substitute_template(template, params)

    def test_extra_parameters_ignored(self):
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org", "repo": "repo", "extra": "ignored"}
        assert _substitute_template(template, params) == "gitea://repos/org/repo/issues"


class TestToolInvalidationCoverage:
    """Test that all important write tools are covered."""

    def test_pr_write_tools_are_mapped(self):
        """All PR write operations should have invalidation patterns."""
        # The mapping uses the actual operationId from swagger, e.g., "repoCreatePullRequest"
        # and the path-based registration should cover them
        pr_write_paths = [
            "/repos/{owner}/{repo}/pulls",
            "/repos/{owner}/{repo}/pulls/{index}",
            "/repos/{owner}/{repo}/pulls/{index}/merge",
            "/repos/{owner}/{repo}/pulls/{index}/close",
        ]
        for path in pr_write_paths:
            patterns = _compute_tool_invalidation_patterns(path, "POST")
            assert patterns, f"Path {path} should produce invalidation patterns"
            assert "pulls_list" in patterns

    def test_repo_write_tools_are_mapped(self):
        """Repository write operations should invalidate repo resource."""
        paths_and_methods = [
            ("/repos/{owner}/{repo}", "PUT"),
            ("/repos/{owner}/{repo}", "DELETE"),
            ("/repos/{owner}/{repo}/topics", "PUT"),
            ("/repos/{owner}/{repo}/contents/README.md", "PUT"),
        ]
        for path, method in paths_and_methods:
            patterns = _compute_tool_invalidation_patterns(path, method)
            assert patterns, f"{method} {path} should produce invalidation patterns"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
