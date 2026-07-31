"""Phase 2c — Pull request workflow: PR creation, diff download, review comment.

A developer creates a branch with a file change, opens a pull request,
downloads the diff, and adds a review comment.  Every step is deeply
asserted for shape and content.

Uses the ``world`` fixture — pooled servers + lazy ``RepoState``.
``need_repo`` with ``branch`` + ``files`` kwargs creates the repo,
branch, and file in one atomic call.  Subsequent tests reuse the
cached ``RepoState``.

Design decisions
----------------
- **Shared world**: Imports ``DEV``, ``SCOPE_WRITE`` from ``world.py``.
- **Single actor**: All steps use ``DEV`` — the PR is from a feature
  branch to main on the same repo.  Peer/PR-counterpart testing belongs
  in a future org/collaboration workflow.
- **Raw diff output**: ``gitea_repo_download_pull_diff_or_patch`` returns
  non-JSON text — assertions check for ``diff --git`` markers.
- **Cleanup**: ``TestCleanup`` deletes the repo at end.
"""

from __future__ import annotations

import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.assertions import assert_content, assert_key_types, assert_keys
from tests.live.conftest import live_available
from tests.live.helpers import delete_repo
from tests.live.world import DEV, SCOPE_WRITE, World

pytestmark = pytest.mark.xdist_group("live-workflow-pr")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_REPO = f"live-pr-{_WORKER}"
_BRANCH = "feature/pr-content"
_PR_FILE = "pr-feature.py"
_PR_BODY = """# PR feature
print('hello from live test')
"""


# ---------------------------------------------------------------------------
# Repo, branch, and file setup (atomic via need_repo kwargs)
# ---------------------------------------------------------------------------


@live_available
class TestSetup:
    """Create the repo, branch, and file for the PR."""

    @pytest.mark.live
    async def test_create_repo(self, world: World) -> None:
        """Create the PR workflow test repo with branch + file in one call."""
        repo = await world.need_repo(
            DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE,
            auto_init=True, description="PR workflow test repo",
            branch=_BRANCH,
            files={_PR_FILE: _PR_BODY},
        )
        assert_content(repo.data, name=_REPO)

        # Verify the branch was created
        assert _BRANCH in repo.branches, (
            f"Branch {_BRANCH} not created: {list(repo.branches.keys())}"
        )
        assert_content(repo.branches[_BRANCH], name=_BRANCH)


# ---------------------------------------------------------------------------
# Pull request operations
# ---------------------------------------------------------------------------


@live_available
class TestPullRequest:
    """Open a PR, download the diff, and add a review comment."""

    @pytest.mark.live
    async def test_create_pull_request(self, world: World) -> None:
        """Open a PR from the feature branch to main — verify shape."""
        repo = await world.need_repo(
            DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE,
            branch=_BRANCH, files={_PR_FILE: _PR_BODY},
        )
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_create_pull_request",
            {"owner": DEV.username, "repo": _REPO,
             "head": _BRANCH, "base": "main",
             "title": "Feature: add hello script",
             "body": "This PR adds a simple hello script for testing.",
             "format": "json"},
        )
        from tests.live.assertions import assert_result_ok
        pr = assert_result_ok(result)
        assert_keys(pr, "number", "title", "state", "head", "base",
                    "body", "user", "created_at", "html_url",
                    "mergeable", "merged")
        assert_key_types(pr, number=int, title=str, state=str)
        assert_content(pr, title="Feature: add hello script", state="open")
        pytest.pr_index = pr["number"]  # type: ignore[attr-defined]

    @pytest.mark.live
    async def test_download_pull_diff(self, world: World) -> None:
        """Download the PR diff — verify raw diff markers."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_download_pull_diff_or_patch",
            {"owner": DEV.username, "repo": _REPO,
             "index": pytest.pr_index,  # type: ignore[attr-defined]
             "diffType": "diff"},
        )
        assert not result.isError, "Failed to download PR diff"
        text = extract_text_content(result.content)
        assert "diff --git" in text, (
            f"Expected raw diff output, got: {text[:200]!r}"
        )
        assert _PR_FILE in text, (
            f"Expected {_PR_FILE} in diff, got: {text[:200]!r}"
        )

    @pytest.mark.live
    async def test_comment_on_pr(self, world: World) -> None:
        """Add a review comment to the PR — verify content."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_issue_create_comment",
            {"owner": DEV.username, "repo": _REPO,
             "index": pytest.pr_index,  # type: ignore[attr-defined]
             "body": "Looks good to me! +1",
             "format": "json"},
        )
        from tests.live.assertions import assert_result_ok
        comment = assert_result_ok(result)
        assert_keys(comment, "body", "user", "created_at")
        assert_content(comment, body="Looks good to me! +1")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@live_available
class TestCleanup:
    """Delete the test repo."""

    @pytest.mark.live
    @pytest.mark.timeout(30)
    async def test_delete_repo(self, world: World) -> None:
        """Delete the PR workflow test repo."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await delete_repo(mcp, DEV.username, _REPO)
