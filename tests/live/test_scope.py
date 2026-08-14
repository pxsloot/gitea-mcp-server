"""Scope enforcement: tool visibility and write blocking.

Token scopes determine what tools an agent can see and call.  These
tests verify that the scope gating pipeline works end-to-end through
the real transport.

Uses the ``world`` fixture — users are pre-bootstrapped, tokens cached.
Each test gets a server with the appropriate token scope via
``world.server_for()``.

Design decisions
----------------
- **RO token**: Read-only victim for visibility + write enforcement.
- **DEV token**: Non-admin token to verify admin tools are hidden.
- **Synthetic tools are scope-free**: search_tools, call_tool available
  to all tokens regardless of scope — tested explicitly.
"""

from __future__ import annotations

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.conftest import live_available
from tests.live.world import DEV, RO, SCOPE_READ, SCOPE_WRITE, World

# ---------------------------------------------------------------------------
# Tool visibility — list_tools + search_tools
# ---------------------------------------------------------------------------


@live_available
class TestToolVisibility:
    """Scope filtering: admin tools invisible for non-admin tokens."""

    @pytest.mark.live
    async def test_admin_tools_not_visible(self, world: World) -> None:
        """``list_tools`` must not include admin tools for a dev (non-sudo) token."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        tool_result = await mcp.list_tools()
        tool_names = [tool.name for tool in tool_result.tools]
        admin_tools = [n for n in tool_names if "admin" in n.lower()]
        assert len(admin_tools) == 0, f"Admin tools visible to non-admin token: {admin_tools}"

    @pytest.mark.live
    async def test_synthetic_tools_always_visible(self, world: World) -> None:
        """Synthetic tools (search_tools, call_tool) visible even for read-only token."""
        mcp = await world.server_for(RO, SCOPE_READ)

        # search_tools should work
        result = await mcp.call_tool("gitea_search_tools", {"query": "repo", "format": "json"})
        assert not result.isError, (
            f"search_tools failed for read-only token: "
            f"{extract_text_content(result.content) if result.content else 'no content'}"
        )

        # call_tool should work (even if the proxied tool fails on scope)
        result = await mcp.call_tool(
            "gitea_call_tool",
            {"name": "gitea_search_tools", "arguments": {"query": "user", "format": "json"}},
        )
        assert not result.isError, (
            f"call_tool failed for read-only token: "
            f"{extract_text_content(result.content) if result.content else 'no content'}"
        )


# ---------------------------------------------------------------------------
# Write enforcement — read-only token must not write
# ---------------------------------------------------------------------------


@live_available
class TestWriteBlocked:
    """Scope enforcement: read-only token must not create or modify resources."""

    @pytest.mark.live
    async def test_create_repo_blocked(self, world: World) -> None:
        """Creating a repo requires write:repository — read-only token lacks it."""
        mcp = await world.server_for(RO, SCOPE_READ)
        result = await mcp.call_tool(
            "gitea_create_current_user_repo",
            {"name": "should-not-exist", "auto_init": False},
        )
        assert result.isError, (
            "Create repo succeeded with read-only token — scope filtering on write tools is broken."
        )

    @pytest.mark.live
    async def test_create_issue_blocked(self, world: World) -> None:
        """Creating an issue requires write:issue — read-only token lacks it."""
        mcp = await world.server_for(RO, SCOPE_READ)
        result = await mcp.call_tool(
            "gitea_issue_create_issue",
            {
                "owner": "mcp-server",
                "repo": "gitea-mcp-server",
                "title": "Test — should not be created",
            },
        )
        if not result.isError:
            pytest.fail(
                "Create issue succeeded with read-only token — scope enforcement is broken."
            )
