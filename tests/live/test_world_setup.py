"""Phase 1: Verify the bootstrapped world — error-path tests only.

The World fixture (``conftest.py``) bootstraps the canonical users, org,
and team during session startup.  Shape assertions and token round-trip
verification run inside ``World.start()`` — those are tested once at
bootstrap time, not repeated per test.

This file contains the **error-path** tests that don't fit inside the
bootstrap path: verifying that invalid inputs are rejected correctly.

Tests that moved elsewhere
--------------------------
- User shape assertions → ``World.start()`` (need_user)
- Org shape assertions → ``World.start()`` (org re-read)
- Team creation + shape → ``World.start()`` (need_team)
- Token round-trip (dev/peer/ro) → ``World.start()`` (dev only;
  other users covered by scope tests)
- Cross-format equivalence → ``test_cross_format.py``
"""

from __future__ import annotations

import pytest

from tests.live.conftest import live_available
from tests.live.world import (
    ORG_NAME,
    TEAM_NAME,
    World,
)

# ---------------------------------------------------------------------------
# Error-path: team creation without required units_map
# ---------------------------------------------------------------------------


@live_available
class TestTeamErrors:
    """Error handling for team creation — not covered by World.start()."""

    @pytest.mark.live
    async def test_team_without_units_map_errors(self, world: World) -> None:
        """Creating a team without ``units_map`` must return an error.

        The World bootstrap creates the canonical team WITH units_map
        (that path is tested).  This test verifies that omitting
        units_map is correctly rejected.
        """
        admin = await world.admin_server()
        result = await admin.call_tool(
            "gitea_org_create_team",
            {
                "org": ORG_NAME,
                "name": f"{TEAM_NAME}-broken",
                "permission": "read",
            },
        )
        assert result.isError, (
            "Expected error when creating team without units_map, "
            "but the call succeeded.  The API may have changed."
        )
