"""ACT II — A developer's day: repos, branches, tags, files.

A developer creates a repository, adds branches and tags, creates files,
sets commit statuses, and configures branch protection.  Every MCP session
is created inline (not via fixture) to avoid anyio TaskGroup issues.

Design decisions
----------------
- **Server per test**: Each test spawns its own MCP server.  Startup is part
  of the test — it fetches the spec, converts it, applies scope filtering.
- **Sequential tests**: ``test_create_user_and_repo`` must run first to create
  the user and repo that all subsequent tests depend on.  ``--dist loadscope``
  keeps module tests in the same worker.  Individual test methods within each
  ``Test*`` class also depend on earlier tests within that class.
- **Token per test**: Every test calls ``create_user_token()`` for its own
  scope-limited token.  This exercises the full token-creation path and keeps
  each test independently verifiable.
- **Fail-hard helpers**: ``create_repo``, ``create_tag``, ``create_branch``,
  ``create_file`` do NOT handle "already exists" — that would mask broken
  cleanup.  ``purge_repo`` pre-cleanup ensures a clean slate before each
  scenario's setup.  ``ensure_user`` handles "already exists" for users
  (which persist across sessions).
- **Depends on ACT I**: The user created here (``_USER``) is unique to this
  module (``live-repo-{worker}``) and does not depend on Admin-created users.
  This keeps acts independent for parallel execution.
- **Cleanup only repos**: The ``TestCleanup`` class deletes the test repo at
  the end.  Users and tokens are left on the throwaway instance.

Bug regressions
---------------
- Commit status with invalid state enum (``pending``/``success``/``error``/``failure``/``warning``)
- Param naming divergence (``filepath``, ``tag_name``, ``rule_name``)
"""

from __future__ import annotations

import json
import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.conftest import live_available, mcp_client
from tests.live.helpers import (
    create_branch,
    create_file,
    create_repo,
    create_tag,
    create_user_token,
    delete_repo,
    ensure_user,
    purge_repo,
)

pytestmark = pytest.mark.xdist_group("live-act-repos")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_USER = f"live-repo-{_WORKER}"
_PASS = "repo-pass-007"
_REPO = f"live-playground-{_WORKER}"
_BRANCH_FEATURE = "feature/new-stuff"
_TAG_V1 = "v0.1.0"
_FILE_README = "generated-info.md"


# ---------------------------------------------------------------------------
# Repo Creation
# ---------------------------------------------------------------------------


@live_available
class TestRepoCreation:
    """Create a repo via tool calls."""

    @pytest.mark.live
    async def test_create_user_and_repo(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create the test user and repo in one flow."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            await ensure_user(admin, _USER, _PASS, email=f"{_USER}@live-test.local")

        token = await create_user_token(
            gitea_url, _USER, _PASS,
            token_name="repo-ci",
            scopes=["write:repository", "write:issue", "write:user", "read:issue"],
        )

        async with mcp_client(gitea_url, server_args, token) as user:
            await purge_repo(user, _USER, _REPO)
            repo = await create_repo(
                user, _REPO,
                auto_init=True,
                description="Live test playground",
            )
            assert repo["name"] == _REPO

    @pytest.mark.live
    async def test_get_repo_contents_list(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """List the root directory of the repo."""
        token = await create_user_token(
            gitea_url, _USER, _PASS,
            token_name="repo-ci",
            scopes=["write:repository", "write:issue", "write:user", "read:issue"],
        )
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_repo_get_contents_list",
                {"owner": _USER, "repo": _REPO},
            )
            assert not result.isError
            text = extract_text_content(result.content)
            assert "README.md" in text, f"Expected README.md in root, got: {text[:200]}"


# ---------------------------------------------------------------------------
# Branch and file operations
# ---------------------------------------------------------------------------


@live_available
class TestBranchAndFiles:
    """Create branches, add files, verify contents."""

    @pytest.mark.live
    async def test_create_branch(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a feature branch from main."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            branch = await create_branch(user, _USER, _REPO, _BRANCH_FEATURE)
            assert branch["name"] == _BRANCH_FEATURE

    @pytest.mark.live
    async def test_create_file_on_branch(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a file on the feature branch (I3 regression: filepath param)."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await create_file(
                user, _USER, _REPO, _FILE_README,
                content="# Generated Info\n\nCreated by live integration tests.\n",
                branch=_BRANCH_FEATURE,
                message="Add generated info file",
            )
            assert result["commit"]["sha"] is not None

    @pytest.mark.live
    async def test_get_file_contents(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Read the file we just created."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_repo_get_contents",
                {
                    "owner": _USER,
                    "repo": _REPO,
                    "filepath": _FILE_README,
                    "ref": _BRANCH_FEATURE,
                },
            )
            assert not result.isError
            text = extract_text_content(result.content)
            assert "generated-info" in text, f"Expected file content, got: {text[:200]}"


# ---------------------------------------------------------------------------
# Tag operations
# ---------------------------------------------------------------------------


@live_available
class TestTags:
    """Create and verify tags."""

    @pytest.mark.live
    async def test_create_annotated_tag(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create an annotated tag on main (I3 regression: tag_name param)."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            tag = await create_tag(user, _USER, _REPO, _TAG_V1, message="First release")
            assert tag["name"] == _TAG_V1

    @pytest.mark.live
    async def test_get_tag_list(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """List tags on the repository."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_repo_list_tags",
                {"owner": _USER, "repo": _REPO},
            )
            assert not result.isError
            text = extract_text_content(result.content)
            assert _TAG_V1 in text, f"Expected tag in list, got: {text[:300]}"


# ---------------------------------------------------------------------------
# Commit status — B1 regression
# ---------------------------------------------------------------------------


@live_available
class TestCommitStatus:
    """Set commit statuses with valid states (B1 regression)."""

    @pytest.mark.live
    async def test_set_pending_status(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Set a pending CI status on latest commit."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_repo_get_branch",
                {"owner": _USER, "repo": _REPO, "branch": _BRANCH_FEATURE, "format": "json"},
            )
            assert not result.isError, "Failed to get branch info"

            text = extract_text_content(result.content)
            branch_data = json.loads(text)
            # format=json may return {"result": {...}} or the raw object
            if isinstance(branch_data, dict) and "result" in branch_data:
                sha = branch_data["result"]["commit"]["id"]
            else:
                sha = branch_data["commit"]["id"]

            status = await user.call_tool(
                "gitea_repo_create_status",
                {
                    "owner": _USER,
                    "repo": _REPO,
                    "sha": sha,
                    "state": "pending",
                    "context": "ci/live-test",
                    "description": "Live test CI check",
                },
            )
            assert not status.isError, (
                "B1 regression: gitea_repo_create_status rejected 'pending'. "
                "The state enum likely still uses issue states (open/closed/all)."
            )


# ---------------------------------------------------------------------------
# Branch protection — I3 regression
# ---------------------------------------------------------------------------


@live_available
class TestBranchProtection:
    """Create and verify branch protection rules."""

    @pytest.mark.live
    async def test_create_branch_protection(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a branch protection rule (I3: rule_name param)."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_repo_create_branch_protection",
                {
                    "owner": _USER,
                    "repo": _REPO,
                    "rule_name": "main",
                    "required_approvals": 1,
                    "enable_push": False,
                    "enable_force_push": False,
                },
            )
            assert not result.isError, (
                "Failed to create branch protection. Check param names."
            )

    @pytest.mark.live
    async def test_list_branch_protections(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """List branch protection rules."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_repo_list_branch_protection",
                {"owner": _USER, "repo": _REPO},
            )
            assert not result.isError
            text = extract_text_content(result.content)
            assert "main" in text, f"Expected 'main' in protections, got: {text[:300]}"


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
        """Delete the playground repo."""
        token = await create_user_token(gitea_url, _USER, _PASS, "ci", ["write:repository", "write:user", "read:issue"])
        async with mcp_client(gitea_url, server_args, token) as user:
            await delete_repo(user, _USER, _REPO)
