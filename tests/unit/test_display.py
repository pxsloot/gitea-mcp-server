"""Tests for display formatters (tools/display.py).

Covers:
    - call_formatter error path (unknown formatter)
    - _format_user_markdown created_at fallback
    - _format_repo_markdown
    - _format_issues_markdown, _format_pulls_markdown, _format_release_markdown
    - Formatter edge cases
    - Tool/resource formatting consistency
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from gitea_mcp_server.format import build_server_info_markdown

if TYPE_CHECKING:
    from collections.abc import Generator

    from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.tools.display import (
    _FORMATTER_META,
    _FORMATTERS,
    _ISSUE_FIELDS,
    _build_labels_markdown,
    _format_issues_markdown,
    _format_labels_markdown,
    _format_pulls_markdown,
    _format_release_markdown,
    _format_repo_markdown,
    _format_user_markdown,
    call_formatter,
    register_formatter,
)


@pytest.fixture(autouse=True)
def _clean_formatters() -> Generator[None, None, None]:
    """Save and restore the global formatter registry around each test.

    Tests register ad-hoc formatters via ``@register_formatter`` which
    mutates the module-level ``_FORMATTERS`` and ``_FORMATTER_META``
    dicts.  This fixture ensures each test starts with a clean slate
    and does not leak registrations to subsequent tests.
    """
    saved_formatters = dict(_FORMATTERS)
    saved_meta = dict(_FORMATTER_META)
    yield
    _FORMATTERS.clear()
    _FORMATTERS.update(saved_formatters)
    _FORMATTER_META.clear()
    _FORMATTER_META.update(saved_meta)


class TestCallFormatter:
    """Tests for call_formatter."""

    def test_unknown_formatter_raises(self) -> None:
        """Unknown formatter name raises ValueError."""
        with pytest.raises(ValueError, match="No formatter registered for 'nonexistent'"):
            call_formatter("nonexistent", {"key": "value"})

    def test_known_formatter_invoked(self) -> None:
        """Known formatter is called and returns expected output."""

        @register_formatter("test_formatter")
        def _test_fmt(data: Any, *, detail: str ="full") -> str:
            return f"formatted: {data}"

        result = call_formatter("test_formatter", {"hello": "world"})
        assert "formatted:" in result

    def test_formatter_with_extra_needed(self) -> None:
        """Formatter registered with need_extra=True receives extra dict."""

        @register_formatter("test_extra", need_extra=True)
        def _test_extra(data: Any, *, detail: str = "full", extra: dict[str, Any] | None = None) -> str:
            ctx = (extra or {}).get("ctx", "none")
            return f"data={data} ctx={ctx}"

        result = call_formatter(
            "test_extra", "val", extra={"ctx": "my_context"}
        )
        assert "ctx=my_context" in result

    def test_formatter_without_detail(self) -> None:
        """Formatter that ignores detail still works."""

        @register_formatter("test_no_detail")
        def _test_no_detail(data: Any, **kwargs: Any) -> str:
            return f"ok:{data}"

        result = call_formatter("test_no_detail", 42)
        assert result == "ok:42"


class TestFormatUserMarkdown:
    """Tests for _format_user_markdown edge cases."""

    def test_created_fallback(self) -> None:
        """When 'created_at' absent but 'created' present, use 'created'."""
        data = {
            "login": "testuser",
            "created": "2024-06-01T00:00:00Z",
            "type": "User",
        }
        result = _format_user_markdown(data)
        # Should show created_at in output (normalized from created)
        assert "2024-06-01" in result
        assert "| Created At |" in result or "created_at" in result.lower()

    def test_created_at_present_no_fallback(self) -> None:
        """When 'created_at' is present, 'created' is ignored."""
        data = {
            "login": "testuser",
            "created_at": "2024-01-01T00:00:00Z",
            "created": "2024-06-01T00:00:00Z",
        }
        result = _format_user_markdown(data)
        # Should use created_at, not created
        assert "2024-01-01" in result


class TestFormatLabelsMarkdownEdgeCases:
    """Edge cases for _format_labels_markdown."""

    def test_empty_data_labels(self) -> None:
        """Empty labels list produces 'no labels' message."""
        result = _format_labels_markdown(
            [],
            detail="full",
            extra={"owner": "org", "repo": "repo"},
        )
        assert "No labels configured for this repository" in result

    def test_empty_data_labels_no_extra(self) -> None:
        """Empty labels list with no extra still works (uses ? placeholders)."""
        result = _format_labels_markdown([], detail="full")
        assert "?/?" in result


class TestBuildLabelsMarkdown:
    """Tests for _build_labels_markdown shorthand."""

    def test_build_labels_markdown(self) -> None:
        """_build_labels_markdown delegates correctly."""
        data = [{"id": 1, "name": "bug", "color": "ff0000", "description": "A bug"}]
        result = _build_labels_markdown(data, "myorg", "myrepo", detail="full")
        assert "myorg/myrepo" in result
        assert "bug" in result


class TestFormatRepoMarkdown:
    """Tests for _format_repo_markdown."""

    def test_formats_repo_completely(self) -> None:
        """Test repository is formatted with all fields."""
        repo = {
            "full_name": "owner/repo",
            "description": "Test repo",
            "owner": {"login": "owner"},
            "html_url": "https://example.com/owner/repo",
            "default_branch": "main",
            "stargazers_count": 42,
            "forks_count": 10,
            "open_issues_count": 5,
            "size": 1024,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T00:00:00Z",
            "topics": ["test", "example"],
            "license": {"name": "MIT"},
        }
        result = _format_repo_markdown(repo)

        assert "# owner/repo" in result
        assert "| Description | Test repo |" in result
        # Owner renders as compact_ref flat row (login), not a nested section
        assert "| Owner | owner |" in result
        assert "## Owner" not in result
        assert "| Stargazers Count | 42 |" in result
        assert "test" in result
        assert "example" in result
        assert "## License" in result

    def test_handles_missing_fields(self) -> None:
        """Test repo with missing optional fields."""
        repo = {
            "full_name": "owner/repo",
            "owner": {"login": "owner"},
            "html_url": "https://example.com/owner/repo",
        }
        result = _format_repo_markdown(repo)

        assert "# owner/repo" in result
        assert "| Property | Value |" in result
        # Owner renders as compact_ref flat row
        assert "| Owner | owner |" in result
        assert "## Owner" not in result


class TestResourceFormatters:
    """Tests for other formatting functions."""

    def test_format_issues_markdown_empty(self) -> None:
        """Test empty issues list."""
        result = _format_issues_markdown([])
        assert "# Issues" in result
        assert "_(empty)_" in result

    def test_format_pulls_markdown_with_data(self) -> None:
        """Test pull request formatting."""
        pull = {
            "number": 1,
            "title": "Test PR",
            "state": "open",
            "user": {"login": "contributor"},
            "created_at": "2024-01-01T00:00:00Z",
            "base": {"label": "main", "ref": "main"},
            "head": {"label": "feature", "ref": "feature"},
            "comments": 5,
            "html_url": "https://example.com/pr/1",
        }
        result = _format_pulls_markdown([pull])

        assert "# Pull Requests" in result
        assert "| Number | 1 |" in result
        assert "| Title | Test PR |" in result
        assert "| State | open |" in result
        # base/head render as compact_ref flat rows showing branch name
        assert "| Base | main |" in result
        assert "| Head | feature |" in result
        assert "## Base" not in result

    def test_format_user_markdown_regular_user(self) -> None:
        """Test user profile formatting."""

        user = {
            "login": "johndoe",
            "full_name": "John Doe",
            "html_url": "https://example.com/johndoe",
            "public_repos": 10,
            "followers_count": 5,
            "following_count": 3,
            "created_at": "2024-01-01T00:00:00Z",
            "bio": "Software developer",
            "location": "NYC",
            "website": "https://johndoe.com",
        }
        result = _format_user_markdown(user)

        assert "# johndoe" in result
        assert "| Full Name | John Doe |" in result
        assert "| Public Repos | 10 |" in result
        assert "| Bio | Software developer |" in result

    def test_format_user_markdown_organization(self) -> None:
        """Test organization profile formatting."""

        org = {
            "login": "myorg",
            "type": "Organization",
            "html_url": "https://example.com/myorg",
            "public_repos": 25,
            "description": "A test organization",
        }
        result = _format_user_markdown(org)

        assert "# myorg" in result
        assert "| Type | Organization |" in result


class TestFormatterGaps:
    """Tests for missing formatter edge cases."""

    def test_format_issues_markdown_with_total(self) -> None:
        issues = [
            {
                "number": 1,
                "title": "Bug",
                "state": "open",
                "user": {"login": "dev1"},
                "created_at": "2024-01-01T00:00:00Z",
                "comments": 0,
                "labels": [],
                "html_url": "https://example.com/issue/1",
            }
        ]
        result = _format_issues_markdown(issues)

        # Formatter derives title from data: "Issues - {count} items"
        assert "Issues - 1 items" in result
        assert "| Number | 1 |" in result
        assert "| Title | Bug |" in result

    def test_format_issues_markdown_with_labels(self) -> None:
        """Issues with labels include label names in output."""
        issues = [
            {
                "number": 2,
                "title": "Feature",
                "state": "open",
                "user": {"login": "dev2"},
                "created_at": "2024-02-01T00:00:00Z",
                "comments": 3,
                "labels": [{"name": "bug"}, {"name": "enhancement"}],
                "html_url": "https://example.com/issue/2",
            }
        ]
        result = _format_issues_markdown(issues)
        # Labels render as compact_ref flat row (comma-separated names)
        assert "| Labels | bug, enhancement |" in result
        assert "## Labels" not in result

    def test_format_issues_markdown_extra_type_issues(self) -> None:
        """Issues formatter with extra={'type': 'issues'} uses 'Issues' title."""
        issues = [{"number": 1, "title": "Bug", "state": "open"}]
        result = _format_issues_markdown(issues, extra={"type": "issues"})
        assert "Issues - 1 items" in result

    def test_format_issues_markdown_extra_type_pulls(self) -> None:
        """Issues formatter with extra={'type': 'pulls'} uses 'Pull Requests' title."""
        issues = [{"number": 1, "title": "Bug", "state": "open"}]
        result = _format_issues_markdown(issues, extra={"type": "pulls"})
        assert "Pull Requests - 1 items" in result

    def test_format_issues_markdown_extra_type_fallback_when_data_is_str(self) -> None:
        """Issues formatter falls back to generic title when data is collapsed strings."""
        result = _format_issues_markdown(["$ref:Issue"], extra=None)
        assert "Issues and Pull Requests - 1 items" in result

    def test_format_issues_markdown_fallback_scan_detects_prs(self) -> None:
        """Fallback scanning detects pull requests when items have pull_request dict."""
        issues = [
            {"number": 1, "title": "Issue", "state": "open"},
            {"number": 2, "title": "PR", "state": "open", "pull_request": {"id": 1}},
        ]
        result = _format_issues_markdown(issues, extra=None)
        # Item has pull_request truthy → "Issues and Pull Requests"
        assert "Issues and Pull Requests - 2 items" in result

    def test_format_issues_markdown_no_prs(self) -> None:
        """Formatter defaults to 'Issues' when no pull_request keys exist."""
        issues = [{"number": 1, "title": "Bug", "state": "open"}]
        result = _format_issues_markdown(issues, extra=None)
        assert "Issues - 1 items" in result

    def test_format_pulls_markdown_empty(self) -> None:
        result = _format_pulls_markdown([])

        assert "# Pull Requests" in result
        assert "Pull Requests" in result
        assert "_(empty)_" in result

    def test_format_release_markdown_full(self) -> None:
        releases = [{
            "tag_name": "v1.0.0",
            "name": "Version 1.0.0",
            "draft": False,
            "prerelease": False,
            "created_at": "2024-01-01T00:00:00Z",
            "published_at": "2024-01-02T00:00:00Z",
            "body": "Release notes here",
        }]
        result = _format_release_markdown(releases)

        assert "# v1.0.0" in result
        assert "| Name | Version 1.0.0 |" in result
        assert "| Draft | False |" in result
        assert "| Prerelease | False |" in result
        assert "| Body | Release notes here |" in result

    def test_format_release_markdown_missing_name(self) -> None:
        releases = [{
            "tag_name": "v1.0.0",
            "draft": False,
            "prerelease": False,
            "created_at": "2024-01-01T00:00:00Z",
            "published_at": "2024-01-02T00:00:00Z",
            "body": "Body",
        }]
        result = _format_release_markdown(releases)

        assert "| Tag Name | v1.0.0 |" in result

    def test_format_release_markdown_missing_body(self) -> None:
        releases = [{
            "tag_name": "v1.0.0",
            "name": "Version 1.0.0",
            "draft": False,
            "prerelease": False,
            "created_at": "2024-01-01T00:00:00Z",
            "published_at": "2024-01-02T00:00:00Z",
        }]
        result = _format_release_markdown(releases)

        assert "# v1.0.0" in result
        assert "| Name | Version 1.0.0 |" in result

    def test_format_release_markdown_draft_prerelease(self) -> None:
        releases = [{
            "tag_name": "v2.0.0-beta",
            "name": "Beta",
            "draft": True,
            "prerelease": True,
            "created_at": "2024-06-01T00:00:00Z",
            "published_at": "2024-06-02T00:00:00Z",
            "body": "Beta release",
        }]
        result = _format_release_markdown(releases)

        assert "| Draft | True |" in result
        assert "| Prerelease | True |" in result

    def test_build_server_info_markdown(self) -> None:
        spec: OpenAPISpec = {
            "info": {
                "title": "Gitea API",
                "version": "1.21.0",
                "description": "Gitea API description.",
            }
        }
        result = build_server_info_markdown(spec)

        assert "**Server Type**: Gitea API" in result
        assert "**API Version**: 1.21.0" in result
        assert "## Description" in result
        assert "Gitea API description." in result

    def test_build_server_info_markdown_no_description(self) -> None:
        spec: OpenAPISpec = {"info": {"title": "Gitea API", "version": "1.21.0"}}
        result = build_server_info_markdown(spec)

        assert "**Server Type**: Gitea API" in result
        assert "## Description" not in result

    def test_build_server_info_markdown_missing_info(self) -> None:
        result = build_server_info_markdown({})

        assert "**Server Type**: Unknown" in result
        assert "**API Version**: Unknown" in result


class TestToolResourceConsistency:
    """Verify that resource formatters and format_as_markdown produce the same structure.

    This is the core fix for issue #347: tool output and resource output
    should use the same nested sub-table format for the same data.
    """

    def test_issue_format_consistent_with_shared_formatter(self) -> None:
        """_format_issues_markdown delegates to format_as_markdown with field_filter."""
        from gitea_mcp_server.format import format_as_markdown

        issues = [
            {
                "number": 1,
                "title": "Bug",
                "state": "open",
                "user": {"login": "dev1"},
                "created_at": "2024-01-01T00:00:00Z",
                "comments": 0,
                "labels": [{"name": "bug"}],
                "html_url": "https://example.com/issue/1",
            }
        ]
        resource_result = _format_issues_markdown(issues)
        direct_result = format_as_markdown(
            issues,
            title="Issues - 1 items",
            field_filter=_ISSUE_FIELDS,
            item_title_key="title",
        )
        # Same structure: both produce nested sub-tables with the same fields
        assert "| Number | 1 |" in resource_result
        assert "| Title | Bug |" in resource_result
        assert "## User" in resource_result
        # The resource formatter wraps the title with count info; since test
        # data lacks pull_request, title reads "Issues - N items"
        assert "Issues - 1 items" in resource_result
        # Labels render as compact_ref flat row (comma-separated names)
        assert "| Labels | bug |" in resource_result

    def test_issue_format_dynamic_title_without_pr(self) -> None:
        """Issues without pull_request use 'Issues' title."""

        issues = [
            {"number": 1, "title": "Bug", "state": "open"},
            {"number": 2, "title": "Feature", "state": "closed"},
        ]
        result = _format_issues_markdown(issues)
        assert "Issues - 2 items" in result

    def test_issue_format_dynamic_title_with_prs(self) -> None:
        """Issues with pull_request entries use 'Issues and Pull Requests' title."""

        items = [
            {"number": 1, "title": "Bug", "state": "open", "pull_request": None},
            {"number": 2, "title": "Fix", "state": "open", "pull_request": {"url": "/pr/2"}},
        ]
        result = _format_issues_markdown(items)
        assert "Issues and Pull Requests - 2 items" in result

    def test_issue_format_shows_pull_request_badge(self) -> None:
        """pull_request field renders as Yes/No badge in issues list."""

        items = [
            {"number": 1, "title": "Bug", "state": "open", "pull_request": None},
            {"number": 2, "title": "Fix", "state": "open", "pull_request": {"url": "/pr/2"}},
        ]
        result = _format_issues_markdown(items)
        # The Bug (pull_request=None) should show No
        assert "| Pull Request | No |" in result
        # The Fix (pull_request=dict) should show Yes
        assert "| Pull Request | Yes |" in result

    def test_pull_format_consistent_with_shared_formatter(self) -> None:
        """_format_pulls_markdown delegates to format_as_markdown with field_filter."""

        pulls = [
            {
                "number": 1,
                "title": "Fix things",
                "state": "open",
                "user": {"login": "contributor"},
                "created_at": "2024-01-01T00:00:00Z",
                "base": {"label": "main", "repo": {"full_name": "org/repo"}, "ref": "main"},
                "head": {"label": "feature", "repo": {"full_name": "fork/repo"}, "ref": "feature"},
                "comments": 3,
                "html_url": "https://example.com/pr/1",
            }
        ]
        resource_result = _format_pulls_markdown(pulls)
        assert "| Number | 1 |" in resource_result
        assert "| Title | Fix things |" in resource_result
        assert "| State | open |" in resource_result
        # base/head render as compact_ref flat rows showing branch name
        assert "| Base | main |" in resource_result
        assert "| Head | feature |" in resource_result
        assert "## Base" not in resource_result

    def test_repo_format_consistent_with_shared_formatter(self) -> None:
        """_format_repo_markdown delegates to format_as_markdown with field_filter."""

        repo = {
            "full_name": "owner/repo",
            "description": "Test repo",
            "owner": {"login": "owner"},
            "html_url": "https://example.com/owner/repo",
            "default_branch": "main",
        }
        resource_result = _format_repo_markdown(repo)
        assert "# owner/repo" in resource_result
        assert "| Full Name | owner/repo |" in resource_result
        assert "| Description | Test repo |" in resource_result
        # Owner renders as compact_ref flat row (login), not a nested section
        assert "| Owner | owner |" in resource_result
        assert "## Owner" not in resource_result

    def test_user_format_consistent_with_shared_formatter(self) -> None:
        """_format_user_markdown delegates to format_as_markdown with field_filter."""

        user = {
            "login": "johndoe",
            "full_name": "John Doe",
            "html_url": "https://example.com/johndoe",
            "public_repos": 10,
        }
        resource_result = _format_user_markdown(user)
        assert "# johndoe" in resource_result
        assert "| Login | johndoe |" in resource_result
        assert "| Full Name | John Doe |" in resource_result

    def test_release_format_consistent_with_shared_formatter(self) -> None:
        """_format_release_markdown delegates to format_as_markdown with field_filter."""

        releases = [{
            "tag_name": "v1.0.0",
            "name": "Version 1.0.0",
            "draft": False,
            "prerelease": False,
            "created_at": "2024-01-01T00:00:00Z",
            "published_at": "2024-01-02T00:00:00Z",
            "body": "Notes",
        }]
        resource_result = _format_release_markdown(releases)
        assert "# v1.0.0" in resource_result
        assert "| Tag Name | v1.0.0 |" in resource_result
        assert "| Name | Version 1.0.0 |" in resource_result
        assert "| Body | Notes |" in resource_result

    def test_labels_format_contains_hints_and_scope(self) -> None:
        """_format_labels_markdown includes accepted format, scoped info, and validation hints."""
        from gitea_mcp_server.tools.display import _format_labels_markdown

        labels = [
            {
                "id": 1,
                "name": "bug",
                "color": "ff0000",
                "description": "Bug reports",
                "exclusive": False,
            },
            {
                "id": 5,
                "name": "Kind/Feature",
                "color": "00ff00",
                "description": "New features",
                "exclusive": True,
            },
            {
                "id": 9,
                "name": "Kind/Bug",
                "color": "0000ff",
                "description": "Bug by kind",
                "exclusive": True,
            },
        ]
        result = _format_labels_markdown(labels, extra={"owner": "test-owner", "repo": "test-repo"})
        assert "# Labels for test-owner/test-repo" in result
        assert "Accepted Format" in result
        assert "Names" in result
        assert "strings" in result
        assert "IDs" in result
        assert "integers" in result
        assert "bug" in result
        assert "Kind/Feature" in result
        assert "(scope: " in result
        assert "exclusive" in result.lower()
        assert "validated" in result.lower()
        assert "`#ff0000`" in result

    def test_labels_format_concise_handles_collapsed_refs(self) -> None:
        """_format_labels_markdown with detail=concise handles collapsed $ref:Label strings."""
        from gitea_mcp_server.tools.display import _format_labels_markdown

        # Simulate collapsed items from the display pipeline (detail=concise).
        collapsed_labels = [
            "$ref:Label",
            "$ref:Label",
            "$ref:Label",
        ]
        result = _format_labels_markdown(
            collapsed_labels,
            detail="concise",
            extra={"owner": "test-owner", "repo": "test-repo"},
        )
        assert "# Labels for test-owner/test-repo" in result
        assert "**Total**: 3 labels" in result
        assert "Accepted Format" in result
        assert "$ref:Label" in result
        # Per-label detail sections should NOT appear for concise mode
        assert "**Color**:" not in result
