"""Phase 2a — Repo owner workflow: repos, branches, files, tags, commit status.

A developer (``DEV`` from ``world.py``) creates a repository, branches,
tags, files, commit statuses, and branch protection rules.  Every step
is deeply asserted for shape, content, and cross-format equivalence.

Uses the ``world`` fixture — one pooled server per token scope (zero
per-test server spawns).  ``need_repo`` creates the repo once and
returns a cached ``RepoState``; all subsequent tests reuse it.

Design decisions
----------------
- **Pooled servers**: ``world.server_for(DEV, SCOPE_WRITE)`` returns the
  same server session for every test in this file.
- **Lazy state**: ``world.need_repo()`` creates (testing the tool) on
  first call; every subsequent call returns the cached ``RepoState``.
- **``RepoState.need_*``**: ``need_branch``, ``need_file``, ``need_tag``
  are idempotent — create + verify once, return cached state thereafter.
- **Cross-format**: Read operations also verify json↔markdown equivalence.
- **Regression guards**: Commit status ``pending`` state (B1 fix),
  ``tag_name``→``name`` naming divergence, ``filepath`` parameter naming.
- **Cleanup**: ``TestCleanup`` deletes the repo at the end.
"""

from __future__ import annotations

import pytest

from tests.live.assertions import (
    assert_content,
    assert_formats_equivalent,
    assert_key_types,
    assert_keys,
    assert_result_ok,
)
from tests.live.conftest import live_available
from tests.live.helpers import delete_repo
from tests.live.world import DEV, SCOPE_WRITE, World

_REPO = "live-repo-local"
_BRANCH = "feature/new-stuff"
_TAG = "v0.1.0"
_FILE = "generated-info.md"


# ---------------------------------------------------------------------------
# Repo creation
# ---------------------------------------------------------------------------


@live_available
class TestRepoCreate:
    """Create and verify a repository."""

    @pytest.mark.live
    async def test_create_repo(self, world: World) -> None:
        """Create a repo — verify shape, content."""
        repo = await world.need_repo(
            DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE,
            auto_init=True, description="Workflow test playground")
        assert_content(repo.data, name=_REPO)

    @pytest.mark.live
    async def test_repo_shape(self, world: World) -> None:
        """Get repo — verify full shape and key types."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        data = assert_result_ok(await mcp.call_tool(
            "gitea_repo_get",
            {"owner": DEV.username, "repo": _REPO, "format": "json"},
        ))
        assert_keys(data, "id", "name", "full_name", "owner",
                    "description", "private", "fork", "html_url",
                    "default_branch", "created_at", "updated_at")
        assert_key_types(data, id=int, name=str, private=bool, fork=bool)
        assert_content(data, name=_REPO, full_name=f"{DEV.username}/{_REPO}")

    @pytest.mark.live
    async def test_repo_cross_format(self, world: World) -> None:
        """``gitea_repo_get`` — json ↔ markdown equivalence."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await assert_formats_equivalent(
            mcp, "gitea_repo_get",
            {"owner": DEV.username, "repo": _REPO},
        )


# ---------------------------------------------------------------------------
# Branch and file operations
# ---------------------------------------------------------------------------


@live_available
class TestBranchAndFiles:
    """Create branches, add files, read contents."""

    @pytest.mark.live
    async def test_create_branch(self, world: World) -> None:
        """Create a feature branch from main — verify shape."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        branch = await repo.need_branch(_BRANCH)
        assert_keys(branch, "name", "commit")
        assert_content(branch, name=_BRANCH)
        assert_key_types(branch, name=str)

    @pytest.mark.live
    async def test_create_file_on_branch(self, world: World) -> None:
        """Create a file on the feature branch (regression: ``filepath`` param)."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        await repo.need_branch(_BRANCH)
        result = await repo.need_file(
            _FILE,
            content="# Generated Info\n\nWorkflow test file.\n",
            branch=_BRANCH,
            message="Add workflow test file",
        )
        assert_keys(result, "commit")
        assert result["commit"]["sha"] is not None

    @pytest.mark.live
    async def test_list_contents_shape(self, world: World) -> None:
        """List root directory — verify shape and cross-format."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        await repo.need_file(
            _FILE,
            content="# Generated Info\n\nWorkflow test file.\n",
            branch=_BRANCH,
        )
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_get_contents_list",
            {"owner": DEV.username, "repo": _REPO, "format": "json"},
        )
        data = assert_result_ok(result)
        assert isinstance(data, list), (
            f"Expected list, got {type(data).__name__}"
        )
        assert len(data) > 0, "Expected at least one file in root"
        assert_keys(data[0], "name", "path", "type", "size")

        # Cross-format
        await assert_formats_equivalent(
            mcp, "gitea_repo_get_contents_list",
            {"owner": DEV.username, "repo": _REPO},
        )

    @pytest.mark.live
    async def test_get_file_contents(self, world: World) -> None:
        """Read the file we created — verify content."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        await repo.need_file(
            _FILE,
            content="# Generated Info\n\nWorkflow test file.\n",
            branch=_BRANCH,
        )
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_get_contents",
            {"owner": DEV.username, "repo": _REPO,
             "filepath": _FILE, "ref": _BRANCH, "format": "json"},
        )
        data = assert_result_ok(result)
        assert_keys(data, "name", "path", "content", "encoding")
        assert_content(data, name=_FILE)


# ---------------------------------------------------------------------------
# Tag operations
# ---------------------------------------------------------------------------


@live_available
class TestTags:
    """Create and verify tags."""

    @pytest.mark.live
    async def test_create_annotated_tag(self, world: World) -> None:
        """Create an annotated tag — verify shape (regression: ``tag_name``→``name``)."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        tag = await repo.need_tag(_TAG, message="First workflow tag")
        # Response uses 'name', not 'tag_name' (naming divergence)
        assert_keys(tag, "name", "message", "commit")
        assert_content(tag, name=_TAG)

    @pytest.mark.live
    async def test_list_tags(self, world: World) -> None:
        """List tags — verify the tag appears and cross-format."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        await repo.need_tag(_TAG, message="First workflow tag")
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_list_tags",
            {"owner": DEV.username, "repo": _REPO, "format": "json"},
        )
        data = assert_result_ok(result)
        assert isinstance(data, list)
        tag_names = [t.get("name") for t in data]
        assert _TAG in tag_names, (
            f"Tag {_TAG} not in list: {tag_names}"
        )

        await assert_formats_equivalent(
            mcp, "gitea_repo_list_tags",
            {"owner": DEV.username, "repo": _REPO},
        )


# ---------------------------------------------------------------------------
# Commit status — B1 regression guard
# ---------------------------------------------------------------------------


@live_available
class TestCommitStatus:
    """Set commit status with valid states (B1 regression)."""

    @pytest.mark.live
    async def test_set_pending_status(self, world: World) -> None:
        """Set a pending CI status — regression: enum must accept 'pending'."""
        repo = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        await repo.need_branch(_BRANCH)
        mcp = await world.server_for(DEV, SCOPE_WRITE)

        # Get the latest commit SHA on the feature branch
        branch_data = assert_result_ok(await mcp.call_tool(
            "gitea_repo_get_branch",
            {"owner": DEV.username, "repo": _REPO,
             "branch": _BRANCH, "format": "json"},
        ))
        sha = branch_data["commit"]["id"]

        # Set pending status
        result = await mcp.call_tool(
            "gitea_repo_create_status",
            {"owner": DEV.username, "repo": _REPO, "sha": sha,
             "state": "pending", "context": "ci/workflow-test",
             "description": "Workflow CI check", "format": "json"},
        )
        data = assert_result_ok(result)
        # API returns 'status' not 'state' for commit status
        assert_keys(data, "status", "context", "description")
        assert_content(data, status="pending", context="ci/workflow-test")


# ---------------------------------------------------------------------------
# Branch protection
# ---------------------------------------------------------------------------


@live_available
class TestBranchProtection:
    """Create and list branch protection rules."""

    @pytest.mark.live
    async def test_create_branch_protection(self, world: World) -> None:
        """Create a branch protection rule — verify it succeeds."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_create_branch_protection",
            {"owner": DEV.username, "repo": _REPO,
             "rule_name": "main", "required_approvals": 1,
             "enable_push": False, "enable_force_push": False,
             "format": "json"},
        )
        assert not result.isError, (
            "Branch protection creation failed. Check param names."
        )

    @pytest.mark.live
    async def test_list_branch_protection(self, world: World) -> None:
        """List branch protections — verify the rule appears."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_list_branch_protection",
            {"owner": DEV.username, "repo": _REPO, "format": "json"},
        )
        data = assert_result_ok(result)
        assert isinstance(data, list)
        rule_names = [r.get("rule_name") for r in data]
        assert "main" in rule_names, (
            f"Branch protection 'main' not found: {rule_names}"
        )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@live_available
class TestCleanup:
    """Delete the test repo."""

    @pytest.mark.live
    @pytest.mark.timeout(30)
    async def test_delete_repo(self, world: World) -> None:
        """Delete the workflow test repo."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await delete_repo(mcp, DEV.username, _REPO)
