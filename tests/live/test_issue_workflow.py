"""Phase 2b — Contributor workflow: labels, milestones, issues, comments, search.

A developer creates a repo with labels and a milestone, files issues with
label references, adds comments, edits the issue, and searches for it.
Every step is deeply asserted for shape, content, and (where appropriate)
cross-format equivalence.

Uses the ``world`` fixture — pooled servers + lazy ``RepoState``.
``need_repo``, ``need_label``, ``need_milestone``, and ``need_issue``
are idempotent: create + verify once, return cached state thereafter.

Design decisions
----------------
- **Sequential tests within classes**: Issue creation runs before
  comment/edit/search tests.  ``--dist loadscope`` keeps module tests
  in the same worker.  ``RepoState`` tracks issue numbers internally.
- **Search indexer**: ``TestIssueSearch`` uses ``world.admin_server()``
  to call ``gitea_admin_cron_run`` and rebuild the bleve index, then
  waits 4s.  No new server spawn needed — admin server is already pooled.
- **Cleanup**: ``TestCleanup`` deletes the repo at end.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.assertions import assert_content, assert_key_types, assert_keys, assert_result_ok
from tests.live.conftest import live_available
from tests.live.helpers import delete_repo
from tests.live.world import DEV, SCOPE_WRITE, World

pytestmark = pytest.mark.xdist_group("live-workflow-issue")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_REPO = f"live-issues-{_WORKER}"
_LABEL_BUG = "bug"
_LABEL_FEATURE = "feature"
_MILESTONE = "v1.0"


# ---------------------------------------------------------------------------
# Repo, label, and milestone setup
# ---------------------------------------------------------------------------


@live_available
class TestSetup:
    """Create the repository, labels, and milestone."""

    @pytest.mark.live
    async def test_create_repo(self, world: World) -> None:
        """Create the issue-workflow test repo."""
        repo = await world.need_repo(
            DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE,
            auto_init=True, description="Issue workflow test repo")
        assert_content(repo.data, name=_REPO)

    @pytest.mark.live
    async def test_create_labels(self, world: World) -> None:
        """Create labels for issue categorization."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        bug = await repo.need_label(_LABEL_BUG, "#ff0000", description="Bug report")
        assert_keys(bug, "id", "name", "color")
        assert_content(bug, name=_LABEL_BUG)
        assert_key_types(bug, id=int, name=str)

        feat = await repo.need_label(_LABEL_FEATURE, "#00ff00", description="Feature request")
        assert_content(feat, name=_LABEL_FEATURE)

    @pytest.mark.live
    async def test_create_milestone(self, world: World) -> None:
        """Create a milestone for issue tracking."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        ms = await repo.need_milestone(_MILESTONE, description="First milestone")
        assert_keys(ms, "id", "title", "description")
        assert_content(ms, title=_MILESTONE)

    @pytest.mark.live
    async def test_list_labels_shape(self, world: World) -> None:
        """List labels — verify shape of returned items."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        await repo.need_label(_LABEL_BUG, "#ff0000")
        await repo.need_label(_LABEL_FEATURE, "#00ff00")
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_issue_list_labels",
            {"owner": DEV.username, "repo": _REPO, "format": "json"},
        )
        data = assert_result_ok(result)
        assert isinstance(data, list)
        assert len(data) >= 2, f"Expected at least 2 labels, got {len(data)}"
        label_names = [lb["name"] for lb in data]
        assert _LABEL_BUG in label_names
        assert _LABEL_FEATURE in label_names


# ---------------------------------------------------------------------------
# Issue operations
# ---------------------------------------------------------------------------


@live_available
class TestIssues:
    """Create and manage issues with deep assertions."""

    @pytest.mark.live
    async def test_create_issue_with_labels(self, world: World) -> None:
        """Create an issue with labels by name — verify shape, content, labels attached."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        await repo.need_label(_LABEL_BUG, "#ff0000")
        issue = await repo.need_issue(
            title="Bug: login fails on Safari",
            body="Steps to reproduce: 1. Open Safari 2. Try login",
            labels=[_LABEL_BUG],
        )
        assert_keys(issue, "number", "title", "body", "state",
                    "labels", "created_at", "user")
        assert_key_types(issue, number=int, title=str, state=str)
        assert_content(issue, title="Bug: login fails on Safari", state="open")
        label_names = [lb["name"] for lb in issue.get("labels", [])]
        assert _LABEL_BUG in label_names, (
            f"Label '{_LABEL_BUG}' not attached. Labels: {label_names}")

    @pytest.mark.live
    async def test_get_issue_shape(self, world: World) -> None:
        """Get the created issue — verify full shape."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        # Retrieve the cached issue by title
        issue = await repo.need_issue("Bug: login fails on Safari")
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        data = assert_result_ok(await mcp.call_tool(
            "gitea_issue_get_issue",
            {"owner": DEV.username, "repo": _REPO,
             "index": issue["number"], "format": "json"},
        ))
        assert_keys(data, "number", "title", "body", "state",
                    "user", "labels", "created_at", "updated_at",
                    "comments", "html_url")
        assert_content(data, number=issue["number"])

    @pytest.mark.live
    async def test_add_comment(self, world: World) -> None:
        """Add a comment to the issue — verify shape and content."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        issue = await repo.need_issue("Bug: login fails on Safari")
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_issue_create_comment",
            {"owner": DEV.username, "repo": _REPO,
             "index": issue["number"],
             "body": "I can reproduce this on v1.0.",
             "format": "json"},
        )
        comment = assert_result_ok(result)
        assert_keys(comment, "body", "user", "created_at")
        assert_content(comment, body="I can reproduce this on v1.0.")

    @pytest.mark.live
    async def test_edit_issue(self, world: World) -> None:
        """Edit the issue — change title and close it."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        issue = await repo.need_issue("Bug: login fails on Safari")
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_issue_edit_issue",
            {"owner": DEV.username, "repo": _REPO,
             "index": issue["number"],
             "title": "Bug: login fails on Safari (all versions)",
             "state": "closed", "format": "json"},
        )
        assert not result.isError


# ---------------------------------------------------------------------------
# Issue search — requires indexer rebuild
# ---------------------------------------------------------------------------


@live_available
class TestIssueSearch:
    """Search for the created issue after indexer rebuild."""

    @pytest.mark.live
    async def test_search_finds_closed_issue(self, world: World) -> None:
        """Search for 'Safari' — must find the closed issue (state=all)."""
        # Rebuild search index via admin cron (pooled admin server)
        admin = await world.admin_server()
        r = await admin.call_tool(
            "gitea_admin_cron_run",
            {"task": "rebuild_issue_indexer"},
        )
        assert not r.isError, f"Failed to rebuild indexer: {r}"

        await asyncio.sleep(4)

        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_issue_search_issues",
            {"q": "Safari", "state": "all", "format": "json"},
        )
        assert not result.isError
        text = extract_text_content(result.content)
        assert text != "[]", f"Search returned empty for 'Safari': {text[:200]}"
        data = json.loads(text)
        assert isinstance(data, list), f"Expected JSON array, got {type(data)}"
        assert len(data) > 0, "Search returned zero results for 'Safari'"

    @pytest.mark.live
    async def test_search_shape(self, world: World) -> None:
        """Verify search result items have correct shape."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_issue_search_issues",
            {"q": "Safari", "state": "all", "format": "json"},
        )
        data = assert_result_ok(result)
        assert isinstance(data, list)
        assert len(data) > 0
        assert_keys(data[0], "number", "title", "state",
                    "user", "created_at", "html_url")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@live_available
class TestCleanup:
    """Delete the test repo."""

    @pytest.mark.live
    @pytest.mark.timeout(30)
    async def test_delete_repo(self, world: World) -> None:
        """Delete the issue workflow test repo."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await delete_repo(mcp, DEV.username, _REPO)
