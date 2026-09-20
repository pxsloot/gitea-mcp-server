"""Real-spec rendering tests for the generic schema-anchored view (#771).

The generic view is exercised elsewhere against synthetic ``Widget`` specs.
This module converts the repository's real ``swagger.v1.json`` and renders
representative payloads for the curated types, asserting the **agent-facing
text** — the channel an agent actually reads.  It is the safety net for the
view's parity with the curated views it replaced: a regression in headings,
field selection, or relation compaction fails here.

Payloads use real API field names and shapes (verified against the spec).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from gitea_mcp_server.format import _generic_collection_view
from gitea_mcp_server.openapi_converter.core import convert_swagger_to_openapi_v3

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec, SwaggerV2Spec


@pytest.fixture(scope="module")
def real_spec() -> OpenAPISpec:
    """The repository's real spec, converted once for the module."""
    spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
    if not spec_path.exists():
        pytest.skip("swagger.v1.json not found")
    with spec_path.open() as f:
        raw = json.load(f)
    return cast("OpenAPISpec", convert_swagger_to_openapi_v3(cast("SwaggerV2Spec", raw)))


def _render(
    data: Any,
    response_type: str,
    spec: OpenAPISpec,
    extra: dict[str, Any] | None = None,
) -> str:
    return _generic_collection_view(
        data, response_type=response_type, openapi_spec=spec, extra=extra
    )


# ---------------------------------------------------------------------------
# Representative payloads (real API shapes)
# ---------------------------------------------------------------------------

_ISSUE = {
    "id": 1174,
    "number": 768,
    "title": "Compact nested user objects",
    "body": "Follow-up from the review.",
    "state": "open",
    "comments": 0,
    "created_at": "2026-09-18T17:58:41Z",
    "updated_at": "2026-09-18T21:44:36Z",
    "html_url": "https://git.home.lan/mcp-server/gitea-mcp-server/issues/768",
    "url": "https://git.home.lan/api/v1/repos/mcp-server/gitea-mcp-server/issues/768",
    "user": {"id": 12, "login": "dev2", "full_name": "dev2", "email": "dev2@home.lan"},
    "labels": [{"id": 184, "name": "Kind/Bug", "color": "ee0701"}],
    "milestone": {"id": 29, "title": "One Agent-Facing Contract", "state": "open"},
    "repository": {"id": 115, "name": "gitea-mcp-server", "owner": "mcp-server"},
    "assignee": None,
    "assignees": None,
    "pull_request": None,
    "original_author": "",
    "original_author_id": 0,
    "pin_order": 0,
    "ref": "",
    "assets": [],
    "due_date": None,
    "closed_at": None,
    "is_locked": False,
}

_PULL = {
    "id": 1176,
    "number": 770,
    "title": "Unify the agent-facing $ref marker shape",
    "body": "## Summary\n\nUnifies the two marker shapes.",
    "state": "closed",
    "comments": 0,
    "created_at": "2026-09-19T19:22:31Z",
    "updated_at": "2026-09-20T17:11:45Z",
    "html_url": "https://git.home.lan/mcp-server/gitea-mcp-server/pulls/770",
    "url": "https://git.home.lan/api/v1/repos/mcp-server/gitea-mcp-server/pulls/770",
    "user": {"id": 12, "login": "dev2"},
    "base": {"label": "main", "ref": "main", "sha": "abc123", "repo_id": 115},
    "head": {"label": "cleanup/763", "ref": "cleanup/763", "sha": "def456", "repo_id": 115},
    "labels": [{"id": 211, "name": "Kind/Cleanup"}],
    "milestone": None,
    "merged": True,
    "merged_at": "2026-09-20T17:11:42Z",
    "merge_commit_sha": "b9214aa",
    "additions": 100,
    "deletions": 20,
    "changed_files": 5,
    "diff_url": "https://x.diff",
    "patch_url": "https://x.patch",
    "draft": False,
    "mergeable": True,
    "pin_order": 0,
    "flow": 0,
    "requested_reviewers": [],
    "requested_reviewers_teams": [],
    "merged_by": {"id": 12, "login": "dev2"},
}

_REPO = {
    "id": 115,
    "name": "gitea-mcp-server",
    "full_name": "mcp-server/gitea-mcp-server",
    "description": "MCP server",
    "owner": {"id": 26, "login": "mcp-server", "username": "mcp-server"},
    "html_url": "https://git.home.lan/mcp-server/gitea-mcp-server",
    "default_branch": "main",
    "stars_count": 3,
    "forks_count": 0,
    "open_issues_count": 4,
    "size": 1024,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-09-20T00:00:00Z",
    "topics": ["mcp", "gitea"],
    "private": False,
    "archived": False,
    "empty": False,
    "fork": False,
    "template": False,
    "internal": False,
    "mirror": False,
    "url": "https://x",
    "clone_url": "https://x.git",
    "ssh_url": "git@x",
    "website": "",
    "language": "Python",
    "watchers_count": 3,
    "release_counter": 2,
    "has_issues": True,
    "has_wiki": True,
    "has_pull_requests": True,
    "has_actions": True,
    "permissions": {"admin": True, "push": True, "pull": True},
}

_USER = {
    "id": 12,
    "login": "dev2",
    "full_name": "dev2",
    "email": "dev2@home.lan",
    "avatar_url": "https://x/a.png",
    "followers_count": 0,
    "following_count": 0,
    "created": "2022-02-05T10:29:15Z",
    "is_admin": False,
    "visibility": "public",
    "location": "",
    "website": "",
    "description": "",
    "language": "en-US",
    "last_login": "2026-09-18T17:14:14Z",
    "restricted": False,
    "active": True,
    "prohibit_login": False,
    "starred_repos_count": 0,
    "source_id": 1,
    "login_name": "dev2",
}

_ORG = {
    "id": 26,
    "username": "mcp-server",
    "name": "mcp-server",
    "full_name": "MCP Server",
    "description": "The org",
    "email": "org@example.com",
    "avatar_url": "https://x/a.png",
    "website": "https://example.com",
    "location": "Earth",
    "visibility": "public",
    "repo_admin_change_team_access": True,
    "created": "2026-03-21T21:10:48Z",
}

_RELEASE = {
    "id": 5,
    "tag_name": "v1.0",
    "name": "One",
    "body": "notes",
    "draft": False,
    "prerelease": False,
    "created_at": "2026-01-01T00:00:00Z",
    "published_at": "2026-01-02T00:00:00Z",
    "author": {"id": 12, "login": "dev2"},
    "assets": [{"id": 9, "name": "bin.tar.gz", "size": 100}],
    "html_url": "https://x/releases/v1.0",
    "target_commitish": "main",
    "tarball_url": "https://x/tarball",
    "zipball_url": "https://x/zipball",
    "url": "https://x",
    "upload_url": "https://x/upload",
    "hide_archive_links": False,
    "archive_download_count": {"zip": 1, "tar_gz": 2},
}


class TestIssueRendering:
    def test_collection_omits_body_and_compacts_labels(self, real_spec: OpenAPISpec) -> None:
        out = _render([_ISSUE], "Issue", real_spec)
        assert "Issues - 1 items" in out
        assert "| Title | Compact nested user objects |" in out
        assert "| Labels | Kind/Bug |" in out
        # body is noise in a list view
        assert "Follow-up from the review." not in out
        # relations compact, not nested
        assert "| User | dev2 |" in out
        assert "| Milestone | One Agent-Facing Contract |" in out
        assert "## User" not in out

    def test_detail_heading_has_number_and_title(self, real_spec: OpenAPISpec) -> None:
        out = _render(_ISSUE, "Issue", real_spec)
        assert out.startswith("# Issue #768: Compact nested user objects")
        # detail keeps the body
        assert "Follow-up from the review." in out

    def test_detail_pr_heading(self, real_spec: OpenAPISpec) -> None:
        issue_pr = {**_ISSUE, "pull_request": {"merged": False}}
        out = _render(issue_pr, "Issue", real_spec)
        assert out.startswith("# Pull Request #768: Compact nested user objects")

    def test_type_filter_retitles_collection(self, real_spec: OpenAPISpec) -> None:
        out = _render([_ISSUE], "Issue", real_spec, extra={"type": "pulls"})
        assert "Pull Requests - 1 items" in out

    def test_mixed_list_is_labelled_issues_and_pull_requests(self, real_spec: OpenAPISpec) -> None:
        """Gitea's /issues returns PRs too; the label must say so."""
        mixed = [_ISSUE, {**_ISSUE, "number": 2, "pull_request": {"merged": False}}]
        out = _render(mixed, "Issue", real_spec)
        assert "Issues and Pull Requests - 2 items" in out


class TestPullRequestRendering:
    def test_collection_heading_and_relations(self, real_spec: OpenAPISpec) -> None:
        out = _render([_PULL], "PullRequest", real_spec)
        assert "Pull Requests - 1 items" in out
        assert "| Base | main |" in out
        assert "| Head | cleanup/763 |" in out
        assert "| User | dev2 |" in out
        # no Python reprs
        assert "{'" not in out

    def test_detail_heading_has_number_and_title(self, real_spec: OpenAPISpec) -> None:
        out = _render(_PULL, "PullRequest", real_spec)
        assert out.startswith("# Pull Request #770: Unify the agent-facing $ref marker shape")


class TestRepositoryRendering:
    def test_collection_heading_and_owner_compaction(self, real_spec: OpenAPISpec) -> None:
        out = _render([_REPO], "Repository", real_spec)
        assert "Repositories - 1 items" in out
        assert "| Owner | mcp-server |" in out
        assert "## Owner" not in out
        # noise omitted
        assert "clone_url" not in out.lower()
        assert "permissions" not in out.lower()

    def test_detail_heading_is_full_name(self, real_spec: OpenAPISpec) -> None:
        out = _render(_REPO, "Repository", real_spec)
        assert out.startswith("# mcp-server/gitea-mcp-server")


class TestUserRendering:
    def test_collection_heading(self, real_spec: OpenAPISpec) -> None:
        out = _render([_USER], "User", real_spec)
        assert "Users - 1 items" in out
        assert "| Login | dev2 |" in out

    def test_detail_heading_is_login(self, real_spec: OpenAPISpec) -> None:
        out = _render(_USER, "User", real_spec)
        assert out.startswith("# dev2")

    def test_detail_heading_when_full_name_empty(self, real_spec: OpenAPISpec) -> None:
        """An empty full_name must not suppress the heading."""
        out = _render({**_USER, "full_name": ""}, "User", real_spec)
        assert out.startswith("# dev2")


class TestOrganizationRendering:
    def test_collection_heading(self, real_spec: OpenAPISpec) -> None:
        out = _render([_ORG], "Organization", real_spec)
        assert "Organizations - 1 items" in out
        assert "mcp-server" in out

    def test_detail_heading_is_username(self, real_spec: OpenAPISpec) -> None:
        out = _render(_ORG, "Organization", real_spec)
        assert out.startswith("# mcp-server")

    def test_detail_heading_when_full_name_empty(self, real_spec: OpenAPISpec) -> None:
        out = _render({**_ORG, "full_name": ""}, "Organization", real_spec)
        assert out.startswith("# mcp-server")


class TestReleaseRendering:
    def test_collection_heading(self, real_spec: OpenAPISpec) -> None:
        out = _render([_RELEASE], "Release", real_spec)
        assert "Releases - 1 items" in out
        assert "| Tag Name | v1.0 |" in out

    def test_detail_heading_is_tag_name(self, real_spec: OpenAPISpec) -> None:
        out = _render(_RELEASE, "Release", real_spec)
        assert out.startswith("# Release v1.0")

    def test_detail_heading_when_name_empty(self, real_spec: OpenAPISpec) -> None:
        out = _render({**_RELEASE, "name": ""}, "Release", real_spec)
        assert out.startswith("# Release v1.0")


class TestRelationDerivation:
    """Relations are derived from the schema, not a curated per-type list."""

    def test_unhinted_relation_compacts(self, real_spec: OpenAPISpec) -> None:
        """``Comment.user`` is a ``$ref`` relation and must compact.

        It is not in the curated compact table, so this proves the renderer
        derives relations from the schema (#771 review finding 6).
        """
        comment = {"id": 1, "body": "hi", "user": {"id": 12, "login": "dev2", "email": "x@y"}}
        out = _render([comment], "Comment", real_spec)
        assert "| User | dev2 |" in out
        assert "## User" not in out

    def test_scalar_anyof_is_not_a_relation(self, real_spec: OpenAPISpec) -> None:
        """``Issue.state`` is ``anyOf[StateType]`` but StateType is a string.

        It must render as a scalar, not compact to a marker.
        """
        out = _render([_ISSUE], "Issue", real_spec)
        assert "| State | open |" in out
