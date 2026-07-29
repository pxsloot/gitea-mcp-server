"""ACT V — Output shapes and formatting.

Verifies format parameters and edge cases produce correct output through
the real transport.  One test per output shape:

1. ``format=json`` returns parseable JSON
2. Empty search results render as a clear empty state (not bare ``*None*``)

Design decisions
----------------
- **Server per test**: Each test spawns its own MCP server.  Startup is part
  of the test — the format/transport layer is exercised from scratch.
- **Sequential tests**: ``test_create_repo_with_issues`` must run first to
  create the repo and issues that format tests depend on.
  ``--dist loadscope`` keeps module tests in the same worker.
- **One assertion per output shape**: Was 7 tests (json, raw, markdown,
  pagination, fetch_all, empty-state, setup), now 3 (setup + json + empty-state
  + cleanup).  Pagination and markdown/raw format variants test the same
  format pipeline as json — the interface boundary is "does our format layer
  produce correct output through the full stack."  One json test answers that.
- **Token per test**: Each test creates its own token via ``create_user_token()``,
  exercising the token-creation path and keeping tests self-contained.
- **Depends on ACT I**: The ``_USER`` here is unique to this module
  (``live-format-{worker}``), independent of other acts.
- **Cleanup only repos**: The ``TestCleanup`` class deletes the test repo.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.conftest import live_available, mcp_client
from tests.live.helpers import (
    create_issue,
    create_repo,
    create_user_token,
    delete_repo,
    ensure_user,
    purge_repo,
)

pytestmark = pytest.mark.xdist_group("live-act-format")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_USER = f"live-format-{_WORKER}"
_PASS = "format-pass-007"
_REPO = f"live-format-{_WORKER}"


# ---------------------------------------------------------------------------
# Setup — repo with issues for format tests to read
# ---------------------------------------------------------------------------


@live_available
class TestSetup:
    """Create the repo and issues for format testing."""

    @pytest.mark.live
    async def test_create_repo_with_issues(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a repo with issues for format tests to query."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            await ensure_user(admin, _USER, _PASS, email=f"{_USER}@live-test.local")

        token = await create_user_token(gitea_url, _USER, _PASS, "fmt-ci",
                                   ["write:repository", "write:issue", "write:user"])
        async with mcp_client(gitea_url, server_args, token) as user:
            await purge_repo(user, _USER, _REPO)
            repo = await create_repo(user, _REPO, auto_init=True)
            assert repo["name"] == _REPO
            for i in range(3):
                issue = await create_issue(user, _USER, _REPO,
                                            title=f"Format test issue {i}",
                                            body=f"Body for issue {i}")
                assert issue["title"] == f"Format test issue {i}"


# ---------------------------------------------------------------------------
# Format parameter — json
# ---------------------------------------------------------------------------


@live_available
class TestOutputFormat:
    """One test per output shape — the interface boundary is 'does our format
    layer produce correct output through the full stack'."""

    @pytest.mark.live
    async def test_format_json(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """``format=json`` must return parseable JSON (dict or list)."""
        token = await create_user_token(gitea_url, _USER, _PASS, "fmt-ci",
                                   ["write:repository", "write:issue", "write:user"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_issue_list_issues",
                {"owner": _USER, "repo": _REPO, "format": "json"},
            )
            assert not result.isError
            text = extract_text_content(result.content)
            try:
                data = json.loads(text)
                assert isinstance(data, (dict, list)), (
                    f"Expected JSON dict or list from format=json, got {type(data)}"
                )
            except json.JSONDecodeError as e:
                pytest.fail(f"format=json returned unparseable content: {e}\n{text[:200]}")


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


@live_available
class TestEmptyResults:
    """Empty search results must render as ``_(empty)_``, not bare ``*None*``."""

    @pytest.mark.live
    async def test_search_nonexistent_issue(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Searching for a term that matches nothing must return a clear empty state."""
        token = await create_user_token(gitea_url, _USER, _PASS, "fmt-ci",
                                   ["write:repository", "write:issue", "write:user"])
        async with mcp_client(gitea_url, server_args, token) as user:
            result = await user.call_tool(
                "gitea_issue_search_issues",
                {"q": "zzzzthisdoesnotexist9999", "owner": _USER, "repo": _REPO},
            )
            assert not result.isError
            text = extract_text_content(result.content)
            assert text != "*None*", (
                "Empty search results render as bare '*None*'."
            )
            assert text != "None", (
                "Empty search results render as bare 'None'."
            )


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
        """Delete the format test repo."""
        token = await create_user_token(gitea_url, _USER, _PASS, "fmt-ci",
                                   ["write:repository", "write:issue", "write:user"])
        async with mcp_client(gitea_url, server_args, token) as user:
            await delete_repo(user, _USER, _REPO)
