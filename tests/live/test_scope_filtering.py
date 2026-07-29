"""ACT IV — What can a token see?

Scope-limited tokens must not be able to see or call admin tools.
Two interface boundaries, one test each:

1. ``list_tools`` hides admin tools for a read-only token
2. Write operations fail with a read-only token

Design decisions
----------------
- **Server per test**: Each test spawns its own MCP server with a different
  scope-limited token.  The server startup applies scope filtering, which is
  exactly what these tests verify.
- **Self-contained**: Each test creates its own limited user inline (the user
  must exist before a token can be minted).  User creation uses ``admin_token``
  via ``mcp_client()`` — the admin tool path is exercised for setup.
- **One assertion per boundary**: Was 5 tests, now 2.  ``list_tools`` hides
  admin tools (proves scope derivation), write operations fail (proves runtime
  enforcement).  Other combinations (sudo param visibility, call_tool failure)
  test the same scope gating — dropped as redundant.
- **No cleanup**: The limited user and tokens live on a throwaway instance.
"""

from __future__ import annotations

import os

import pytest

from tests.live.conftest import live_available, mcp_client
from tests.live.helpers import create_user_token, ensure_user

pytestmark = pytest.mark.xdist_group("live-act-scope")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_USER = f"live-limited-{_WORKER}"
_PASS = "limited-pass-007"


async def _ensure_limited_user(gitea_url: str, server_args: list[str], admin_token: str) -> None:
    """Create the limited user if not already present."""
    async with mcp_client(gitea_url, server_args, admin_token) as admin:
        await ensure_user(admin, _USER, _PASS, email=f"{_USER}@live-test.local")

async def _limited_token(gitea_url: str, server_args: list[str], admin_token: str) -> str:
    """Ensure user exists, then create a limited read:repository token."""
    await _ensure_limited_user(gitea_url, server_args, admin_token)
    return await create_user_token(
        gitea_url, _USER, _PASS,
        token_name="limited-ci",
        scopes=["read:repository", "read:user", "read:issue"],
    )


# ---------------------------------------------------------------------------
# Tool visibility
# ---------------------------------------------------------------------------


@live_available
class TestToolVisibility:
    """Scope filtering: admin tools must be invisible for limited tokens."""

    @pytest.mark.live
    async def test_admin_tools_not_visible(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """``list_tools`` must not include admin tools for limited token."""
        token = await _limited_token(gitea_url, server_args, admin_token)
        async with mcp_client(gitea_url, server_args, token) as mcp:
            tools = await mcp.list_tools()
            tool_names = [str(t[0]) for t in tools]
            admin_tools = [n for n in tool_names if "admin" in n]
            assert len(admin_tools) == 0, (
                f"Admin tools visible to limited token: {admin_tools}"
            )


# ---------------------------------------------------------------------------
# Write operations fail with read-only token
# ---------------------------------------------------------------------------


@live_available
class TestReadOnlyBlock:
    """Scope enforcement: read-only token must not create resources."""

    @pytest.mark.live
    async def test_create_repo_fails(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Creating a repo requires write:repository — our token lacks it."""
        token = await _limited_token(gitea_url, server_args, admin_token)
        async with mcp_client(gitea_url, server_args, token) as mcp:
            result = await mcp.call_tool(
                "gitea_create_current_user_repo",
                {"name": "should-not-exist", "auto_init": False},
            )
            assert result.isError, (
                "Create repo succeeded with read:repository token — "
                "scope filtering on tools is broken."
            )
