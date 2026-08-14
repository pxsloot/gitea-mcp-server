"""Canonical test identities, scope constants, and namespace utilities.

This module defines every identity, scope list, and namespace suffix
used by the live test suite.  Extracted from ``world.py`` to keep that
module focused on the ``World`` orchestration facade.

All names imported from this module in existing test files must remain
stable — ``from tests.live.world import DEV`` continues to work because
``world.py`` re-exports everything defined here.
"""

from __future__ import annotations

import os
import re
import uuid

# ---------------------------------------------------------------------------
# Worker and run namespaces
# ---------------------------------------------------------------------------

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
"""The xdist worker id, or ``local`` outside xdist."""

_RUN_ID: str = (
    re.sub(
        r"[^a-z0-9-]",
        "-",
        os.getenv("GITEA_LIVE_RUN_ID", uuid.uuid4().hex[:8]).lower(),
    ).strip("-")[:16]
    or uuid.uuid4().hex[:8]
)
"""Run namespace; override with ``GITEA_LIVE_RUN_ID`` in CI."""

_NAMESPACE: str = f"{_RUN_ID}-{_WORKER}"
"""Unique suffix preventing concurrent live runs from sharing entities."""

# =============================================================================
# Canonical scope lists
# =============================================================================

SCOPE_WRITE = ["write:repository", "write:issue", "write:user"]
"""Full write access — the primary actor scopes."""

SCOPE_READ = ["read:repository", "read:user", "read:issue"]
"""Read-only access — for scope gating tests."""

SCOPE_LIMITED = ["write:repository", "read:issue"]
"""Partial write — can create repos but not issues."""

# =============================================================================
# Test identities
# =============================================================================


class User:
    """A test user identity — username, password, email."""

    __slots__ = ("email", "password", "username")

    def __init__(self, base: str, password: str) -> None:
        self.username = f"{base}-{_NAMESPACE}"
        self.password = password
        self.email = f"{self.username}@live-test.local"


DEV = User("live-dev", "dev-pass-007")
"""Primary actor for workflow tests."""

PEER = User("live-peer", "peer-pass-007")
"""PR counterpart / second actor."""

RO = User("live-ro", "ro-pass-007")
"""Read-only victim for scope gating."""

LIMITED = User("live-limited", "limited-pass-007")
"""Partial-scope victim for scope gating."""

ALL_USERS = (DEV, PEER, RO, LIMITED)

# =============================================================================
# Org and team
# =============================================================================

ORG_NAME = f"live-org-{_NAMESPACE}"
"""Test organization name."""

TEAM_NAME = f"live-team-{_NAMESPACE}"
"""Test team within the organization."""

# =============================================================================
# Re-export — world.py imports these for backward-compatible test imports
# =============================================================================

__all__ = [
    "ALL_USERS",
    "DEV",
    "LIMITED",
    "ORG_NAME",
    "PEER",
    "RO",
    "SCOPE_LIMITED",
    "SCOPE_READ",
    "SCOPE_WRITE",
    "TEAM_NAME",
    "_NAMESPACE",
    "_RUN_ID",
    "_WORKER",
    "User",
]
