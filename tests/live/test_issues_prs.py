"""ACT III — Contributor workflow: issues, labels, milestones, PRs.

A contributor creates labels and milestones, files issues with label
references, adds comments, opens a pull request, and reviews the diff.
Every tool call is also an assertion.

Design decisions
----------------
- **Server per test**: Each test spawns its own MCP server.  Startup is part
  of the test — it fetches the spec, converts it, applies scope filtering.
- **Sequential tests**: ``TestSetup`` creates the repo and resources (labels,
  milestones) that ``TestIssues`` and ``TestPullRequests`` depend on.  Tests
  within ``TestIssues`` are sequential (create issue → comment → get → edit).
  ``--dist loadscope`` keeps module tests in the same worker.
- **``pytest.*`` state**: ``pytest.bug_issue_index`` and ``pytest.pr_index``  # type: ignore[attr-defined]
  pass issue/PR numbers between sequential tests within the same class.  This
  is safe because those tests always run in order in the same worker.
- **Token per test**: The ``_user_token()`` helper creates a fresh token for
  every test.  This exercises the token-creation path repeatedly and keeps
  tests independent.
- **Depends on ACT I**: The user created here (``_USER``) is unique to this
  module (``live-issues-{worker}``), keeping acts independent.
- **Cleanup only repos**: The ``TestCleanup`` class deletes the test repo.
  Labels, milestones, users, and tokens persist on the throwaway instance.
"""

from __future__ import annotations

import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.conftest import live_available, mcp_client
from tests.live.helpers import (
    add_comment,
    create_branch,
    create_file,
    create_issue,
    create_label,
    create_milestone,
    create_pull_request,
    create_repo,
    create_user_token,
    delete_repo,
    ensure_user,
    purge_repo,
)

pytestmark = pytest.mark.xdist_group("live-act-issues")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_USER = f"live-issues-{_WORKER}"
_PASS = "issues-pass-007"
_REPO = f"live-issues-{_WORKER}"
_BRANCH = "feature/pr-content"
_PR_FILE = "pr-feature.py"
_LABEL_BUG = "bug"
_LABEL_FEATURE = "feature"
_MILESTONE = "v1.0"


async def _user_token(gitea_url: str) -> str:
    """Get a write-scoped token for the test user (creates if needed)."""
    return await create_user_token(
        gitea_url, _USER, _PASS,
        token_name="issues-ci",
        scopes=["write:repository", "write:issue", "write:user"],
    )


# ---------------------------------------------------------------------------
# Repo and branch setup
# ---------------------------------------------------------------------------


@live_available
class TestSetup:
    """Create the repository and labels for issue/PR testing."""

    @pytest.mark.live
    async def test_create_user_and_repo(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create the test user and repo."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            await ensure_user(admin, _USER, _PASS, email=f"{_USER}@live-test.local")

        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            await purge_repo(user, _USER, _REPO)
            repo = await create_repo(user, _REPO, auto_init=True,
                                      description="Issues/PRs live test repo")
            assert repo["name"] == _REPO

    @pytest.mark.live
    async def test_create_pr_branch(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a branch for the PR."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            branch = await create_branch(user, _USER, _REPO, _BRANCH)
            assert branch["name"] == _BRANCH

    @pytest.mark.live
    async def test_create_pr_file(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a file on the PR branch."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await create_file(
                user, _USER, _REPO, _PR_FILE,
                content="# PR feature\nprint('hello from live test')\n",
                branch=_BRANCH,
                message="Add PR feature file",
            )
            assert result["commit"]["sha"] is not None

    @pytest.mark.live
    async def test_create_labels(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create labels for issue categorization."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            label1 = await create_label(user, _USER, _REPO, _LABEL_BUG, "#ff0000",
                                         description="Bug report")
            assert label1["name"] == _LABEL_BUG

            label2 = await create_label(user, _USER, _REPO, _LABEL_FEATURE, "#00ff00",
                                         description="Feature request")
            assert label2["name"] == _LABEL_FEATURE

    @pytest.mark.live
    async def test_create_milestone(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a milestone for issue tracking."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            ms = await create_milestone(user, _USER, _REPO, _MILESTONE,
                                         description="First release")
            assert ms["title"] == _MILESTONE


# ---------------------------------------------------------------------------
# Issue operations
# ---------------------------------------------------------------------------


@live_available
class TestIssues:
    """Create and manage issues."""

    @pytest.mark.live
    async def test_create_issue_with_labels_by_name(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create an issue with labels by name string."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            issue = await create_issue(user, _USER, _REPO,
                                        title="Bug: login fails",
                                        body="Detailed bug description.",
                                        labels=[_LABEL_BUG])
            assert issue["title"] == "Bug: login fails"
            pytest.bug_issue_index = issue["number"]  # type: ignore[attr-defined]

    @pytest.mark.live
    async def test_add_comment_to_issue(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Add a comment to the bug issue."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            comment = await add_comment(user, _USER, _REPO,
                                         index=pytest.bug_issue_index,  # type: ignore[attr-defined]
                                         body="I can reproduce this on v1.0.")
            assert comment["body"] == "I can reproduce this on v1.0."

    @pytest.mark.live
    async def test_get_issue(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Get the bug issue to verify it exists."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_issue_get_issue",
                {"owner": _USER, "repo": _REPO, "index": pytest.bug_issue_index},  # type: ignore[attr-defined]
            )
            assert not result.isError
            text = extract_text_content(result.content)
            assert "Bug: login fails" in text, f"Expected issue title, got: {text[:200]}"

    @pytest.mark.live
    async def test_edit_issue(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Edit the bug issue title and state."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_issue_edit_issue",
                {
                    "owner": _USER,
                    "repo": _REPO,
                    "index": pytest.bug_issue_index,  # type: ignore[attr-defined]
                    "title": "Bug: login fails on Safari",
                    "state": "closed",
                },
            )
            assert not result.isError

    @pytest.mark.live
    async def test_search_issues(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Search for the issue created in this act via the cross-repo
        search endpoint.  Rebuilds the search index first so the
        freshly-created issue is findable regardless of indexer timing.
        """
        # Rebuild the search index using the admin token so the
        # freshly-created issue is indexed immediately.
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            r = await admin.call_tool(
                "gitea_admin_cron_run",
                {"task": "rebuild_issue_indexer"},
            )
            assert not r.isError, f"Failed to rebuild issue indexer: {r}"

        import asyncio
        await asyncio.sleep(4)  # Let the indexer finish

        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_issue_search_issues",
                {"q": "login fails", "format": "json", "state": "all"},
            )
            assert not result.isError
            text = extract_text_content(result.content)
            assert text != "[]", f"Search returned empty: {text[:200]}"
            import json
            data = json.loads(text)
            assert isinstance(data, list), f"Expected JSON array, got {type(data)}"
            assert len(data) > 0, f"Search returned zero results"


# ---------------------------------------------------------------------------
# Pull request workflow
# ---------------------------------------------------------------------------


@live_available
class TestPullRequests:
    """Create and interact with a pull request."""

    @pytest.mark.live
    async def test_create_pull_request(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Open a PR from the feature branch to main."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            pr = await create_pull_request(user, _USER, _REPO,
                                            head=_BRANCH, base="main",
                                            title="Feature: add hello script",
                                            body="This PR adds a simple hello script.")
            assert pr["title"] == "Feature: add hello script"
            assert pr["state"] == "open"
            pytest.pr_index = pr["number"]  # type: ignore[attr-defined]

    @pytest.mark.live
    async def test_download_pull_diff(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Download the diff of the PR."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_repo_download_pull_diff_or_patch",
                {
                    "owner": _USER,
                    "repo": _REPO,
                    "index": pytest.pr_index,  # type: ignore[attr-defined]
                    "diffType": "diff",
                },
            )
            assert not result.isError, "Failed to download PR diff"
            text = extract_text_content(result.content)
            assert "diff --git" in text, (
                f"Expected raw diff output, got: {text[:200]!r}"
            )

    @pytest.mark.live
    async def test_comment_on_pr(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Add a review comment to the PR."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            comment = await add_comment(user, _USER, _REPO,
                                         index=pytest.pr_index,  # type: ignore[attr-defined]
                                         body="Looks good to me!")
            assert comment["body"] == "Looks good to me!"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@live_available
class TestCleanup:
    """Delete the test repo."""

    @pytest.mark.live
    @pytest.mark.timeout(30)
    async def test_delete_repo(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Delete the issues test repo."""
        token = await _user_token(gitea_url)
        async with mcp_client(gitea_url, server_args, token) as user:
            await delete_repo(user, _USER, _REPO)
