"""Metatests — verify the test infrastructure itself works correctly.

These tests are infrastructure diagnostics, not an ordered finalization
phase.  They assert invariants about the World fixture that prove
session-scoped pooling is working:

1. ``World.start()`` was called exactly once (not once per module).
2. Pooled servers remain available until session teardown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.live.conftest import live_available

if TYPE_CHECKING:
    from tests.live.world import World

# ---------------------------------------------------------------------------
# Bootstrap-once assertion
# ---------------------------------------------------------------------------


@live_available
class TestWorldBootstrapOnce:
    """The World must be bootstrapped exactly once per session."""

    @pytest.mark.live
    async def test_world_bootstrapped_exactly_once(self, world: World) -> None:
        """World.start() runs exactly once — proves session-scoped pooling."""
        assert world.bootstrap_count == 1, (
            f"World bootstrapped {world.bootstrap_count} times — "
            f"expected exactly 1.  Session-scoped event loop or "
            f"World fixture scope may not be working."
        )

    @pytest.mark.live
    async def test_world_users_cached(self, world: World) -> None:
        """After bootstrap, all four canonical users exist in the state graph."""
        assert len(world._users) >= 4, (
            f"Expected >=4 cached users after bootstrap, got {len(world._users)}. "
            f"Users: {sorted(world._users.keys())}"
        )

    @pytest.mark.live
    async def test_world_servers_alive(self, world: World) -> None:
        """Pooled servers are still reachable at session end."""
        # Admin server should always be available
        admin = await world.admin_server()
        result = await admin.call_tool(
            "gitea_user_get_current", {"format": "json"},
        )
        assert not getattr(result, "isError", False), (
            "Admin server unreachable at session end — "
            "server pool may have been prematurely closed."
        )
