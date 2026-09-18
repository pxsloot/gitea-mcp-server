"""Tests for display formatters (tools/display.py).

Covers:
    - the type-binding registry (``register_formatter(types=...)``)
    - _format_user_markdown created_at fallback
    - _format_repo_markdown
    - _format_issues_markdown, _format_pulls_markdown, _format_release_markdown
    - shape tolerance: collection (list) vs detail (dict) views
    - Formatter edge cases
    - Tool/resource formatting consistency
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from gitea_mcp_server.format import (
    _FORMATTERS,
    _TYPE_FORMATTERS,
    build_server_info_markdown,
    get_formatter_for_type,
    register_formatter,
)

if TYPE_CHECKING:
    from collections.abc import Generator

from gitea_mcp_server.tools.display import (
    _ISSUE_FIELDS,
    _format_issues_markdown,
    _format_labels_markdown,
    _format_org_markdown,
    _format_pulls_markdown,
    _format_release_markdown,
    _format_repo_markdown,
    _format_user_markdown,
)
from tests.helpers.spec_fixtures import make_openapi_spec


@pytest.fixture(autouse=True)
def _clean_formatters() -> Generator[None, None, None]:
    """Save and restore the global formatter registries around each test.

    Tests register ad-hoc formatters via ``@register_formatter`` which
    mutates the module-level ``_FORMATTERS`` and (with ``types=``)
    ``_TYPE_FORMATTERS`` dicts.  This fixture ensures each test starts with
    a clean slate and does not leak registrations to subsequent tests.
    """
    saved_formatters = dict(_FORMATTERS)
    saved_types = dict(_TYPE_FORMATTERS)
    yield
    _FORMATTERS.clear()
    _FORMATTERS.update(saved_formatters)
    _TYPE_FORMATTERS.clear()
    _TYPE_FORMATTERS.update(saved_types)


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
            extra={"owner": "org", "repo": "repo"},
        )
        assert "No labels configured for this repository" in result

    def test_empty_data_labels_no_extra(self) -> None:
        """Empty labels list with no extra still works (uses ? placeholders)."""
        result = _format_labels_markdown([])
        assert "?/?" in result


class TestTypeBindingRegistry:
    """``register_formatter(types=...)`` populates the type index."""

    def test_types_populate_index(self) -> None:
        @register_formatter("thing", types=["Thing"])
        def _fmt(data: Any) -> str:
            return "thing!"

        assert _TYPE_FORMATTERS["Thing"] == "thing"
        assert get_formatter_for_type("Thing") is _fmt

    def test_multiple_types_bind_one_formatter(self) -> None:
        @register_formatter("who", types=["User", "Organization"])
        def _fmt(data: Any) -> str:
            return "who!"

        assert get_formatter_for_type("User") is _fmt
        assert get_formatter_for_type("Organization") is _fmt

    def test_unbound_type_returns_none(self) -> None:
        """Unregistered types keep the generic fallback (pipeline tier 3)."""
        assert get_formatter_for_type("Widget") is None

    def test_registered_name_without_types(self) -> None:
        """Plain ``register_formatter(name)`` binds nothing by type."""

        @register_formatter("hint_only")
        def _fmt(data: Any) -> str:
            return "x"

        assert "hint_only" in _FORMATTERS
        assert not any(v == "hint_only" for v in _TYPE_FORMATTERS.values())

    def test_shipped_domain_bindings(self) -> None:
        """Each domain type binds to its resource-sibling formatter."""
        expected = {
            "Issue": "issues",
            "PullRequest": "pull_requests",
            "Repository": "repository",
            "User": "user",
            "Organization": "organization",
            "Label": "labels",
            "Release": "release",
        }
        for type_name, formatter_name in expected.items():
            assert _TYPE_FORMATTERS.get(type_name) == formatter_name
            assert get_formatter_for_type(type_name) is not None

    def test_duplicate_type_binding_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Rebinding a type to a different formatter warns; last registration wins."""

        @register_formatter("first", types=["Dup"])
        def _first(data: Any) -> str:
            return "first"

        with caplog.at_level(logging.WARNING, logger="gitea_mcp_server.format"):

            @register_formatter("second", types=["Dup"])
            def _second(data: Any) -> str:
                return "second"

        assert "already bound" in caplog.text
        assert get_formatter_for_type("Dup") is _second


class TestFormatterShapeTolerance:
    """a list renders the collection view, a dict the detail view.

    The pipeline hands a type-bound formatter either shape: list tools
    (``repo_list_*``) and list resources produce lists; detail tools
    (``repo_get``, ``issue_get_issue``, …) produce dicts.
    """

    def test_repo_list_renders_collection(self) -> None:
        repos = [
            {"name": "a", "full_name": "o/a", "description": "first"},
            {"name": "b", "full_name": "o/b", "description": "second"},
        ]
        result = _format_repo_markdown(repos)
        assert "Repositories - 2 items" in result
        assert "o/a" in result
        assert "o/b" in result

    def test_repo_empty_list(self) -> None:
        result = _format_repo_markdown([])
        assert "Repositories" in result

    def test_repo_dict_renders_detail(self) -> None:
        result = _format_repo_markdown({"name": "r", "full_name": "o/r"})
        assert result.startswith("# o/r")

    def test_repo_dict_detail_keeps_state_and_permissions(self) -> None:
        """A single-repo read keeps the full payload the collection drops."""
        repo = {
            "full_name": "o/r",
            "default_branch": "main",
            "private": True,
            "archived": True,
            "stars_count": 7,
            "permissions": {"admin": True, "push": False, "pull": True},
        }
        result = _format_repo_markdown(repo)
        assert "| Private | True |" in result
        assert "| Archived | True |" in result
        assert "| Stars Count | 7 |" in result
        # Detail renders every payload field; nested objects become sections.
        assert "## Permissions" in result
        assert "| Admin | True |" in result
        assert "| Push | False |" in result
        assert "| Pull | True |" in result

    def test_repo_scalar_passthrough_no_crash(self) -> None:
        """Unexpected scalar shape renders through the generic path (#574)."""
        result = _format_repo_markdown(42)
        assert "42" in result

    def test_issues_dict_detail_keeps_body(self) -> None:
        """The detail view must not drop the payload (``body``)."""
        issue = {
            "number": 1,
            "title": "Bug",
            "state": "open",
            "body": "THE PAYLOAD",
            "labels": [{"name": "Kind/Bug"}],
        }
        result = _format_issues_markdown(issue)
        assert result.startswith("# Issue #1: Bug")
        assert "THE PAYLOAD" in result
        assert "Kind/Bug" in result

    def test_issues_dict_pr_gets_pr_title(self) -> None:
        issue = {"number": 2, "title": "Feat", "pull_request": {"merged": False}}
        result = _format_issues_markdown(issue)
        assert result.startswith("# Pull Request #2: Feat")

    def test_issues_dict_pr_title_from_extra(self) -> None:
        issue = {"number": 3, "title": "Feat"}
        result = _format_issues_markdown(issue, extra={"type": "pulls"})
        assert result.startswith("# Pull Request #3: Feat")

    def test_issues_dict_no_title(self) -> None:
        issue = {"number": 4}
        result = _format_issues_markdown(issue)
        assert result.startswith("# Issue #4")

    def test_issues_list_collection_drops_body(self) -> None:
        """Collection view keeps the curated whitelist (resource parity)."""
        issues = [{"number": 1, "title": "T", "body": "NOT IN LIST VIEW"}]
        result = _format_issues_markdown(issues)
        assert "NOT IN LIST VIEW" not in result
        assert "Issues - 1 items" in result

    def test_pulls_dict_detail_keeps_body(self) -> None:
        pr = {
            "number": 9,
            "title": "PR",
            "body": "PR PAYLOAD",
            "additions": 12,
            "deletions": 3,
            "changed_files": 2,
            "base": {"ref": "main"},
            "head": {"ref": "feat"},
        }
        result = _format_pulls_markdown(pr)
        assert result.startswith("# Pull Request #9: PR")
        assert "PR PAYLOAD" in result
        # Diff stats are payload the collection view drops (#767).
        assert "| Additions | 12 |" in result
        assert "| Deletions | 3 |" in result
        assert "| Changed Files | 2 |" in result
        # Detail renders the full payload; base/head become sections.
        assert "## Base" in result
        assert "main" in result

    def test_pulls_dict_no_title(self) -> None:
        result = _format_pulls_markdown({"number": 10})
        assert result.startswith("# Pull Request #10")

    def test_release_dict_detail(self) -> None:
        rel = {"tag_name": "v1.0", "name": "One", "body": "notes"}
        result = _format_release_markdown(rel)
        assert result.startswith("# Release v1.0")
        assert "notes" in result

    def test_release_dict_falls_back_to_name(self) -> None:
        result = _format_release_markdown({"name": "OnlyName"})
        assert result.startswith("# Release OnlyName")

    def test_user_list_renders_collection(self) -> None:
        users = [{"login": "a"}, {"login": "b"}]
        result = _format_user_markdown(users)
        assert "Users - 2 items" in result
        assert "| Login | a |" in result

    def test_user_dict_detail_keeps_email_and_visibility(self) -> None:
        """A single-user read keeps profile fields the collection drops."""
        user = {
            "login": "dev2",
            "email": "dev2@home.lan",
            "visibility": "public",
            "is_admin": False,
        }
        result = _format_user_markdown(user)
        assert "# dev2" in result
        assert "| Email | dev2@home.lan |" in result
        assert "| Visibility | public |" in result
        assert "| Is Admin | False |" in result

    def test_user_list_normalizes_created(self) -> None:
        """Per-item created→created_at normalization applies in lists too."""
        users = [{"login": "a", "created": "2024-06-01T00:00:00Z"}]
        result = _format_user_markdown(users)
        assert "2024-06-01" in result
        assert "Created At" in result

    def test_user_empty_list(self) -> None:
        result = _format_user_markdown([])
        assert "Users" in result

    def test_org_dict_renders_org_fields(self) -> None:
        """A single org read keeps username/name/description/visibility (#766)."""
        org = {
            "id": 26,
            "username": "mcp-server",
            "name": "mcp-server",
            "full_name": "MCP Server",
            "description": "The org",
            "email": "org@example.com",
            "avatar_url": "https://example.com/a.png",
            "website": "https://example.com",
            "location": "Earth",
            "visibility": "public",
            "repo_admin_change_team_access": True,
            "created": "2026-03-21T21:10:48Z",
        }
        result = _format_org_markdown(org)
        assert result.startswith("# mcp-server")
        assert "| Username | mcp-server |" in result
        assert "| Description | The org |" in result
        assert "| Visibility | public |" in result
        assert "| Created At | 2026-03-21" in result
        # Never the user heading.
        assert "# User" not in result

    def test_org_list_renders_org_names(self) -> None:
        """An org list titles each item by username, not login (#766)."""
        orgs = [{"username": "alpha"}, {"username": "beta"}]
        result = _format_org_markdown(orgs)
        assert "Organizations - 2 items" in result
        assert "alpha" in result
        assert "beta" in result

    def test_org_empty_list(self) -> None:
        result = _format_org_markdown([])
        assert "Organizations" in result

    def test_org_scalar_passthrough_no_crash(self) -> None:
        """Unexpected scalar shape renders through the generic path (#574)."""
        result = _format_org_markdown(42)
        assert "42" in result

    def test_label_dict_detail_view(self) -> None:
        label = {
            "id": 1,
            "name": "Kind/Bug",
            "color": "ee0701",
            "description": "desc",
            "exclusive": True,
            "is_archived": True,
        }
        result = _format_labels_markdown(label, extra={"owner": "o", "repo": "r"})
        assert result.startswith("# Label: Kind/Bug (#1) *(archived)* for o/r")
        assert "scope: `Kind`" in result
        assert "**Exclusive**: Yes" in result
        # No collection scaffolding on a detail read.
        assert "Accepted Format" not in result
        assert "**Total**" not in result

    def test_label_dict_without_extra(self) -> None:
        """Graceful without repo context — no dangling 'for' clause."""
        result = _format_labels_markdown({"id": 2, "name": "bug"})
        assert result.startswith("# Label: bug (#2)")
        assert " for " not in result.splitlines()[0]

    def test_label_list_org_scope(self) -> None:
        """Org-scoped label tools pass ``org`` — heading shows it (#766)."""
        result = _format_labels_markdown(
            [{"id": 1, "name": "bug", "color": "red"}], extra={"org": "mcp-server"}
        )
        assert "# Labels for mcp-server" in result
        assert "?/?" not in result

    def test_label_detail_org_scope(self) -> None:
        result = _format_labels_markdown({"id": 1, "name": "bug"}, extra={"org": "mcp-server"})
        assert result.startswith("# Label: bug (#1) for mcp-server")

    def test_label_scope_prefers_owner_over_org(self) -> None:
        """When both are present, owner/repo wins (repo-scoped tools)."""
        result = _format_labels_markdown(
            [{"id": 1, "name": "bug"}],
            extra={"owner": "o", "repo": "r", "org": "ignored"},
        )
        assert "# Labels for o/r" in result

    def test_release_dict_detail_keeps_author_and_assets(self) -> None:
        """A single-release read keeps author, assets, and download URLs (#766)."""
        rel = {
            "id": 5,
            "tag_name": "v1.0",
            "name": "One",
            "body": "notes",
            "author": {"login": "dev2"},
            "assets": [{"id": 9, "name": "bin.tar.gz", "size": 100}],
            "html_url": "https://example.com/releases/v1.0",
            "target_commitish": "main",
            "tarball_url": "https://example.com/tarball",
            "zipball_url": "https://example.com/zipball",
        }
        result = _format_release_markdown(rel)
        assert result.startswith("# Release v1.0")
        # Nested objects render as sections; scalars as table rows.
        assert "## Author" in result
        assert "dev2" in result
        assert "## Assets" in result
        assert "bin.tar.gz" in result
        assert "| Html Url |" in result
        assert "| Target Commitish | main |" in result


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
            "stars_count": 42,
            "forks_count": 10,
            "open_issues_count": 5,
            "size": 1024,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T00:00:00Z",
            "topics": ["test", "example"],
        }
        result = _format_repo_markdown(repo)

        assert "# owner/repo" in result
        assert "| Description | Test repo |" in result
        # Detail renders the full payload; nested owner becomes a section.
        assert "## Owner" in result
        assert "owner" in result
        assert "| Stars Count | 42 |" in result
        assert "test" in result
        assert "example" in result

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
        # Detail renders the full payload; nested owner becomes a section.
        assert "## Owner" in result
        assert "owner" in result


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
            "followers_count": 5,
            "following_count": 3,
            "created_at": "2024-01-01T00:00:00Z",
            "description": "Software developer",
            "location": "NYC",
            "website": "https://johndoe.com",
        }
        result = _format_user_markdown(user)

        assert "# johndoe" in result
        assert "| Full Name | John Doe |" in result
        assert "| Followers Count | 5 |" in result
        assert "| Description | Software developer |" in result

    def test_format_user_markdown_organization(self) -> None:
        """Organization is a distinct shape — the user formatter must not claim it.

        The org formatter is registered separately (#766); the user formatter
        renders whatever dict it is handed, so this locks that the *binding*
        (not the function) routes Organization to the org view.
        """
        from gitea_mcp_server.format import get_formatter_for_type

        assert get_formatter_for_type("Organization") is not get_formatter_for_type("User")


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

    def test_format_issues_markdown_defensive_on_string_items(self) -> None:
        """Issues formatter tolerates string items (defensive, not the contract).

        Since #759 the concise contract summarizes items as dicts — bare
        ``$ref:Issue`` strings no longer reach formatters through the
        pipeline.  If an unexpected shape arrives, the scan simply finds no
        pull requests and renders the plain "Issues" title.
        """
        result = _format_issues_markdown(["$ref:Issue"], extra=None)
        assert "Issues - 1 items" in result

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
        releases = [
            {
                "tag_name": "v1.0.0",
                "name": "Version 1.0.0",
                "draft": False,
                "prerelease": False,
                "created_at": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-02T00:00:00Z",
                "body": "Release notes here",
            }
        ]
        result = _format_release_markdown(releases)

        assert "# v1.0.0" in result
        assert "| Name | Version 1.0.0 |" in result
        assert "| Draft | False |" in result
        assert "| Prerelease | False |" in result
        assert "| Body | Release notes here |" in result

    def test_format_release_markdown_missing_name(self) -> None:
        releases = [
            {
                "tag_name": "v1.0.0",
                "draft": False,
                "prerelease": False,
                "created_at": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-02T00:00:00Z",
                "body": "Body",
            }
        ]
        result = _format_release_markdown(releases)

        assert "| Tag Name | v1.0.0 |" in result

    def test_format_release_markdown_missing_body(self) -> None:
        releases = [
            {
                "tag_name": "v1.0.0",
                "name": "Version 1.0.0",
                "draft": False,
                "prerelease": False,
                "created_at": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-02T00:00:00Z",
            }
        ]
        result = _format_release_markdown(releases)

        assert "# v1.0.0" in result
        assert "| Name | Version 1.0.0 |" in result

    def test_format_release_markdown_draft_prerelease(self) -> None:
        releases = [
            {
                "tag_name": "v2.0.0-beta",
                "name": "Beta",
                "draft": True,
                "prerelease": True,
                "created_at": "2024-06-01T00:00:00Z",
                "published_at": "2024-06-02T00:00:00Z",
                "body": "Beta release",
            }
        ]
        result = _format_release_markdown(releases)

        assert "| Draft | True |" in result
        assert "| Prerelease | True |" in result

    def test_build_server_info_markdown(self) -> None:
        spec = make_openapi_spec(
            info={
                "title": "Gitea API",
                "version": "1.21.0",
                "description": "Gitea API description.",
            }
        )
        result = build_server_info_markdown(spec)

        assert "**Server Type**: Gitea API" in result
        assert "**API Version**: 1.21.0" in result
        assert "## Description" in result
        assert "Gitea API description." in result

    def test_build_server_info_markdown_no_description(self) -> None:
        spec = make_openapi_spec(info={"title": "Gitea API", "version": "1.21.0"})
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
        """_format_repo_markdown: collection whitelist for lists, full dict detail."""

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
        # Detail renders the full payload; nested owner becomes a section.
        assert "## Owner" in resource_result
        assert "owner" in resource_result

    def test_user_format_consistent_with_shared_formatter(self) -> None:
        """_format_user_markdown delegates to format_as_markdown with field_filter."""

        user = {
            "login": "johndoe",
            "full_name": "John Doe",
            "html_url": "https://example.com/johndoe",
            "followers_count": 10,
        }
        resource_result = _format_user_markdown(user)
        assert "# johndoe" in resource_result
        assert "| Login | johndoe |" in resource_result
        assert "| Full Name | John Doe |" in resource_result

    def test_release_format_consistent_with_shared_formatter(self) -> None:
        """_format_release_markdown delegates to format_as_markdown with field_filter."""

        releases = [
            {
                "tag_name": "v1.0.0",
                "name": "Version 1.0.0",
                "draft": False,
                "prerelease": False,
                "created_at": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-02T00:00:00Z",
                "body": "Notes",
            }
        ]
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

    def test_labels_format_concise_receives_item_dicts(self) -> None:
        """Under detail=concise items are summarized dicts (#759), not labels.

        The ``Label`` schema has no nested ``$ref`` fields, so a concise
        item is the full scalar dict — the formatter renders the same
        per-label sections on both detail levels (no shape detection).
        """
        from gitea_mcp_server.tools.display import _format_labels_markdown

        concise_labels = [
            {"id": 1, "name": "Kind/Bug", "color": "ee0701", "description": "Bugs"},
            {"id": 2, "name": "Priority/High", "color": "e64a19", "description": ""},
        ]
        result = _format_labels_markdown(
            concise_labels,
            extra={"owner": "test-owner", "repo": "test-repo"},
        )
        assert "# Labels for test-owner/test-repo" in result
        assert "**Total**: 2 labels" in result
        assert "### Kind/Bug (#1)" in result
        assert "**Color**:" in result

    def test_labels_format_defensive_on_string_items(self) -> None:
        """The labels formatter renders unexpected non-dict items as bullets.

        Defensive guard only — the pipeline never delivers collapsed
        strings as items since #759 (root-list items are dicts).
        """
        from gitea_mcp_server.tools.display import _format_labels_markdown

        result = _format_labels_markdown(
            ["$ref:Label", "$ref:Label"],
            extra={"owner": "test-owner", "repo": "test-repo"},
        )
        assert "**Total**: 2 labels" in result
        assert "- $ref:Label" in result
        # Per-label detail sections must NOT appear for non-dict items
        assert "**Color**:" not in result
