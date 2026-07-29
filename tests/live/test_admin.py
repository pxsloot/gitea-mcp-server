"""ACT I — Admin bootstraps the world.

The administrator creates users, organizations, and teams using admin-level
MCP tools.  Every tool call is also an assertion — if the admin tools don't
work, these tests fail.

Design decisions
----------------
- **Server per test**: Each test spawns its own MCP server via ``mcp_client()``.
  Server startup (fetch spec, convert, apply scope filtering) IS part of the
  test.  The cost is runtime.
- **Sequential tests**: Tests within this act are sequential by design —
  ``test_create_dev_user`` must run before ``test_create_dev_token``.  The
  ``--dist loadscope`` pytest setting keeps module tests in the same worker.
- **``pytest.*`` state**: Tests in later acts reference users/orgs created here
  via module-level constants (``DEV_USER``, ``ORG_NAME``, etc.).  No pytest
  attributes needed within this act.
- **No cleanup**: Users and orgs live on a throwaway instance.  Only repos
  are cleaned up (they accumulate on disk).
- **Token per test**: Every ``TestUserTokens`` test calls
  ``create_user_token()`` independently.  This exercises the token-creation
  path and keeps tests self-contained.
- **Admin token**: Tests use ``admin_token`` (from ``.env.dev.local``) via
  ``mcp_client(gitea_url, server_args, admin_token)`` — not a user-created
  token.  Admin tools require the admin token.

Tools exercised
---------------
- ``gitea_admin_create_user``
- ``gitea_org_create``
- ``gitea_org_create_team``
"""

from __future__ import annotations

import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.conftest import live_available, mcp_client
from tests.live.helpers import create_team, create_user_token, ensure_org, ensure_user

pytestmark = pytest.mark.xdist_group("live-act-admin")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
DEV_USER = f"live-dev-{_WORKER}"
DEV_PASS = "dev-pass-007"
READONLY_USER = f"live-ro-{_WORKER}"
READONLY_PASS = "ro-pass-007"
SUDO_USER = f"live-sudo-{_WORKER}"
SUDO_PASS = "sudo-pass-007"
ORG_NAME = f"live-org-{_WORKER}"
TEAM_NAME = f"live-team-{_WORKER}"


# ---------------------------------------------------------------------------
# Admin user creation
# ---------------------------------------------------------------------------


@live_available
class TestAdminUsers:
    """The administrator creates users with the admin tool."""

    @pytest.mark.live
    async def test_create_dev_user(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a regular development user."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            user = await ensure_user(
                admin, DEV_USER, DEV_PASS,
                email=f"{DEV_USER}@live-test.local",
            )
            assert user["username"] == DEV_USER
            assert user["email"] == f"{DEV_USER}@live-test.local"

    @pytest.mark.live
    async def test_create_readonly_user(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a user with no special permissions."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            user = await ensure_user(
                admin, READONLY_USER, READONLY_PASS,
                email=f"{READONLY_USER}@live-test.local",
            )
            assert user["username"] == READONLY_USER

    @pytest.mark.live
    async def test_create_sudo_test_user(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a user for sudo-param visibility tests."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            user = await ensure_user(
                admin, SUDO_USER, SUDO_PASS,
                email=f"{SUDO_USER}@live-test.local",
            )
            assert user["username"] == SUDO_USER


# ---------------------------------------------------------------------------
# Org and team creation
# ---------------------------------------------------------------------------


@live_available
class TestAdminOrgsAndTeams:
    """The administrator creates organizations and teams."""

    @pytest.mark.live
    async def test_create_org(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create an organization."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            org = await ensure_org(admin, ORG_NAME)
            assert org["username"] == ORG_NAME

    @pytest.mark.live
    async def test_create_team(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Create a team within the org with a permission matrix."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            team = await create_team(
                admin, ORG_NAME, TEAM_NAME,
                permission="write",
                units_map={
                    "repo.code": "write",
                    "repo.issues": "write",
                    "repo.pulls": "write",
                },
            )
            assert team["name"] == TEAM_NAME

    @pytest.mark.live
    async def test_team_without_units_map_errors(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Creating a team without ``units_map`` must return an error."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            result = await admin.call_tool(
                "gitea_org_create_team",
                {"org": ORG_NAME, "name": "broken-team", "permission": "read"},
            )
            assert result.isError, (
                "Expected error when creating team without units_map, "
                "but the call succeeded.  The API may have changed."
            )


# ---------------------------------------------------------------------------
# Token creation via Basic Auth
# ---------------------------------------------------------------------------


@live_available
class TestUserTokens:
    """Users mint their own scope-limited tokens via Basic Auth."""

    @pytest.mark.live
    async def test_create_dev_token(self, gitea_url: str) -> None:
        """Dev user creates a write:repository token."""
        token = await create_user_token(
            gitea_url, DEV_USER, DEV_PASS,
            token_name="ci",
            scopes=["write:repository", "write:issue", "write:user"],
        )
        assert len(token) > 20, f"Token too short: {token}"

    @pytest.mark.live
    async def test_create_readonly_token(self, gitea_url: str) -> None:
        """Read-only user creates a read:repository token."""
        token = await create_user_token(
            gitea_url, READONLY_USER, READONLY_PASS,
            token_name="ci",
            scopes=["read:repository", "read:user"],
        )
        assert len(token) > 20, f"Token too short: {token}"

    @pytest.mark.live
    async def test_create_sudo_user_token(self, gitea_url: str) -> None:
        """Create a token for the sudo-test user (no admin scopes)."""
        token = await create_user_token(
            gitea_url, SUDO_USER, SUDO_PASS,
            token_name="ci",
            scopes=["write:repository", "write:issue", "write:user"],
        )
        assert len(token) > 20, f"Token too short: {token}"


# ---------------------------------------------------------------------------
# Cleanup teardown
# ---------------------------------------------------------------------------


@live_available
class TestAdminCleanup:
    """Tear down test data created by the admin bootstrap."""

    @pytest.mark.live
    @pytest.mark.timeout(30)
    async def test_delete_org(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Delete the test org to clean up."""
        async with mcp_client(gitea_url, server_args, admin_token) as admin:
            result = await admin.call_tool(
                "gitea_org_delete",
                {"org": ORG_NAME},
            )
            if result.isError:
                text = extract_text_content(result.content)
                if "404" not in text:
                    pytest.fail(f"Unexpected error deleting org: {text}")
