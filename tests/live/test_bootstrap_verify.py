"""Unit tests for bootstrap verification logic in ``tests/live/world.py``.

These tests verify every :class:`BootstrapVerificationError` path in
``World.need_user``, ``World.need_org``, and ``World.need_team`` using
mocked ``ClientSession.call_tool`` responses.  No live Gitea instance
is required — the paths that previously could only be exercised by
pre-existing entities with mismatched config are now testable in CI.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import TextContent

from tests.live.conflict import BootstrapVerificationError
from tests.live.identities import User
from tests.live.world import World

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_result(
    is_error: bool = False,
    data: dict[str, object] | list[dict[str, object]] | None = None,
    error_text: str = "",
) -> MagicMock:
    """Build a mock ``CallToolResult`` with ``.isError`` and ``.content``."""
    result = MagicMock()
    result.isError = is_error
    if is_error:
        result.content = [TextContent(type="text", text=error_text)]
    else:
        result.content = [TextContent(
            type="text", text=json.dumps(data or {}),
        )]
    return result


async def _mock_admin_session(*responses: MagicMock) -> MagicMock:
    """Build a mock admin ``ClientSession`` whose ``call_tool`` returns *responses*."""
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=responses)
    return session


def _new_world() -> World:
    """Build a minimal ``World`` with no real servers."""
    return World("http://localhost:3000", "fake-token", ["python", "-m", "gitea_mcp_server"])


def _test_user() -> User:
    """Create a user with predictable credentials for testing."""
    # User.__slots__ prevents attribute assignment, so we can't mock it.
    # Instead use a real User — the namespace suffix is deterministic.
    return User("bootstrap-test", "test-pass")


# ---------------------------------------------------------------------------
# need_user — bootstrap verification
# ---------------------------------------------------------------------------


class TestNeedUserBootstrap:
    """Bootstrap verification paths for ``World.need_user``."""

    @pytest.mark.asyncio
    async def test_email_mismatch(self) -> None:
        """Pre-existing user has wrong email → BootstrapVerificationError."""
        world = _new_world()
        user = _test_user()

        mock_admin = await _mock_admin_session(
            # admin_create_user → "already exists"
            _mock_result(is_error=True, error_text="user already exists"),
            # user_get → success, but wrong email
            _mock_result(data={
                "id": 1, "login": user.username, "username": user.username,
                "email": "wrong@test.local", "active": True,
                "prohibit_login": False,
            }),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_user(user)
        assert exc.value.field == "email"
        assert exc.value.expected == user.email
        assert exc.value.observed == "wrong@test.local"

    @pytest.mark.asyncio
    async def test_active_false(self) -> None:
        """Pre-existing user is inactive → BootstrapVerificationError."""
        world = _new_world()
        user = _test_user()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="user already exists"),
            _mock_result(data={
                "id": 1, "login": user.username, "username": user.username,
                "email": user.email, "active": False,
                "prohibit_login": False,
            }),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_user(user)
        assert exc.value.field == "active"
        assert exc.value.expected is True
        assert exc.value.observed is False

    @pytest.mark.asyncio
    async def test_prohibit_login_true(self) -> None:
        """Pre-existing user has prohibit_login=True → BootstrapVerificationError."""
        world = _new_world()
        user = _test_user()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="user already exists"),
            _mock_result(data={
                "id": 1, "login": user.username, "username": user.username,
                "email": user.email, "active": True,
                "prohibit_login": True,
            }),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_user(user)
        assert exc.value.field == "prohibit_login"
        assert exc.value.expected is False
        assert exc.value.observed is True

    @pytest.mark.asyncio
    async def test_login_mismatch(self) -> None:
        """Pre-existing user has different login → AssertionError from _assert_content."""
        world = _new_world()
        user = _test_user()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="user already exists"),
            _mock_result(data={
                "id": 1, "login": "wrong-login", "username": user.username,
                "email": user.email, "active": True,
                "prohibit_login": False,
            }),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(AssertionError, match="Key 'login'"):
            await world.need_user(user)

    @pytest.mark.asyncio
    async def test_user_not_readable(self) -> None:
        """user_get returns error → BootstrapVerificationError(readable)."""
        world = _new_world()
        user = _test_user()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="user already exists"),
            _mock_result(is_error=True, error_text="not found"),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_user(user)
        assert exc.value.field == "readable"

    @pytest.mark.asyncio
    async def test_already_exists_adopted_successfully(self) -> None:
        """Pre-existing user matches expected config → adopted without error."""
        world = _new_world()
        user = _test_user()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="user already exists"),
            _mock_result(data={
                "id": 1, "login": user.username, "username": user.username,
                "email": user.email, "active": True,
                "prohibit_login": False,
            }),
        )
        world._servers["__admin__"] = mock_admin

        result = await world.need_user(user)
        assert result["id"] == 1
        assert result["username"] == user.username
        assert world._users[user.username] is result

    @pytest.mark.asyncio
    async def test_idempotent_second_call_returns_cached(self) -> None:
        """Second call to need_user returns cached data without API calls."""
        world = _new_world()
        user = _test_user()

        # First call — create user
        mock_admin = await _mock_admin_session(
            _mock_result(data={
                "id": 1, "login": user.username, "username": user.username,
                "email": user.email, "active": True,
                "prohibit_login": False,
            }),
        )
        world._servers["__admin__"] = mock_admin
        first = await world.need_user(user)
        assert first["id"] == 1

        # Second call — returns cached without calling admin_create_user
        second = await world.need_user(user)
        assert second is first
        # Only one call_tool call was made (the first one)
        assert mock_admin.call_tool.call_count == 1


# ---------------------------------------------------------------------------
# need_org — bootstrap verification
# ---------------------------------------------------------------------------


class TestNeedOrgBootstrap:
    """Bootstrap verification paths for ``World.need_org``."""

    @pytest.mark.asyncio
    async def test_full_name_mismatch(self) -> None:
        """Pre-existing org has wrong full_name → BootstrapVerificationError."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="org already exists"),
            _mock_result(data={
                "id": 1, "username": "live-org", "visibility": "public",
                "full_name": "Wrong Name",
            }),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_org("live-org", full_name="Expected Name")
        assert exc.value.field == "full_name"
        assert exc.value.expected == "Expected Name"
        assert exc.value.observed == "Wrong Name"

    @pytest.mark.asyncio
    async def test_username_mismatch(self) -> None:
        """Pre-existing org has wrong username → AssertionError from _assert_content."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="org already exists"),
            _mock_result(data={
                "id": 1, "username": "wrong-org", "visibility": "public",
                "full_name": "Live Org",
            }),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(AssertionError, match="Key 'username'"):
            await world.need_org("live-org", full_name="Live Org")

    @pytest.mark.asyncio
    async def test_org_not_readable(self) -> None:
        """org_get returns error → BootstrapVerificationError(readable)."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="org already exists"),
            _mock_result(is_error=True, error_text="not found"),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_org("live-org")
        assert exc.value.field == "readable"

    @pytest.mark.asyncio
    async def test_full_name_none_skips_check(self) -> None:
        """When full_name is None, the check is skipped — no error raised."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="org already exists"),
            _mock_result(data={
                "id": 1, "username": "live-org", "visibility": "public",
                "full_name": "Whatever Name",
            }),
        )
        world._servers["__admin__"] = mock_admin

        # full_name=None means "don't care" — the guard is skipped
        result = await world.need_org("live-org", full_name=None)
        assert result["username"] == "live-org"

    @pytest.mark.asyncio
    async def test_already_exists_adopted_successfully(self) -> None:
        """Pre-existing org matches expected config → adopted without error."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="org already exists"),
            _mock_result(data={
                "id": 1, "username": "live-org", "visibility": "public",
                "full_name": "Live Org",
            }),
        )
        world._servers["__admin__"] = mock_admin

        result = await world.need_org("live-org", full_name="Live Org")
        assert result["id"] == 1
        assert result["username"] == "live-org"
        assert world._orgs["live-org"] is result

    @pytest.mark.asyncio
    async def test_idempotent_second_call_returns_cached(self) -> None:
        """Second call to need_org returns cached data without API calls."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(data={
                "id": 1, "username": "live-org", "visibility": "public",
                "full_name": "Live Org",
            }),
        )
        world._servers["__admin__"] = mock_admin

        first = await world.need_org("live-org", full_name="Live Org")
        assert first["id"] == 1

        second = await world.need_org("live-org", full_name="Live Org")
        assert second is first
        assert mock_admin.call_tool.call_count == 1


# ---------------------------------------------------------------------------
# need_team — bootstrap verification
# ---------------------------------------------------------------------------


class TestNeedTeamBootstrap:
    """Bootstrap verification paths for ``World.need_team``."""

    @pytest.mark.asyncio
    async def test_permission_mismatch(self) -> None:
        """Pre-existing team has wrong permission → BootstrapVerificationError."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            # org_create_team → "already exists"
            _mock_result(is_error=True, error_text="team name already exists"),
            # org_list_teams → team with wrong permission
            _mock_result(data=[
                {"id": 42, "name": "dev-team", "permission": "read",
                 "units_map": {"repo.code": "write", "repo.issues": "write"}},
            ]),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_team(
                "live-org", "dev-team", permission="write",
                units_map={"repo.code": "write", "repo.issues": "write"},
            )
        assert exc.value.field == "permission"
        assert exc.value.expected == "write"
        assert exc.value.observed == "read"

    @pytest.mark.asyncio
    async def test_units_map_repo_code_mismatch(self) -> None:
        """Pre-existing team has wrong units_map entry → BootstrapVerificationError."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="conflict"),
            _mock_result(data=[
                {"id": 42, "name": "dev-team", "permission": "write",
                 "units_map": {"repo.code": "read", "repo.issues": "write"}},
            ]),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_team(
                "live-org", "dev-team", permission="write",
                units_map={"repo.code": "write", "repo.issues": "write"},
            )
        assert exc.value.field == "units_map.repo.code"
        assert exc.value.expected == "write"
        assert exc.value.observed == "read"

    @pytest.mark.asyncio
    async def test_units_map_repo_issues_mismatch(self) -> None:
        """Pre-existing team has wrong units_map.repo.issues → BootstrapVerificationError."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="conflict"),
            _mock_result(data=[
                {"id": 42, "name": "dev-team", "permission": "write",
                 "units_map": {"repo.code": "write", "repo.issues": "read"}},
            ]),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_team(
                "live-org", "dev-team", permission="write",
                units_map={"repo.code": "write", "repo.issues": "write"},
            )
        assert exc.value.field == "units_map.repo.issues"
        assert exc.value.expected == "write"
        assert exc.value.observed == "read"

    @pytest.mark.asyncio
    async def test_team_not_listable(self) -> None:
        """org_list_teams returns error → BootstrapVerificationError(listable)."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="already exists"),
            _mock_result(is_error=True, error_text="permission denied"),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_team("live-org", "dev-team", permission="write")
        assert exc.value.field == "listable"

    @pytest.mark.asyncio
    async def test_team_not_found_in_list(self) -> None:
        """Team not found in org_list_teams response → BootstrapVerificationError(found)."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="already exists"),
            # List returns teams but not the one we're looking for
            _mock_result(data=[
                {"id": 1, "name": "other-team", "permission": "read",
                 "units_map": {}},
            ]),
        )
        world._servers["__admin__"] = mock_admin

        with pytest.raises(BootstrapVerificationError) as exc:
            await world.need_team("live-org", "dev-team", permission="write")
        assert exc.value.field == "found"

    @pytest.mark.asyncio
    async def test_already_exists_adopted_successfully(self) -> None:
        """Pre-existing team matches expected config → adopted without error."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(is_error=True, error_text="conflict"),
            _mock_result(data=[
                {"id": 42, "name": "dev-team", "permission": "write",
                 "units_map": {"repo.code": "write", "repo.issues": "write"}},
            ]),
        )
        world._servers["__admin__"] = mock_admin

        result = await world.need_team(
            "live-org", "dev-team", permission="write",
            units_map={"repo.code": "write", "repo.issues": "write"},
        )
        assert result["id"] == 42
        assert result["name"] == "dev-team"

    @pytest.mark.asyncio
    async def test_idempotent_second_call_returns_cached(self) -> None:
        """Second call to need_team returns cached data without API calls."""
        world = _new_world()

        mock_admin = await _mock_admin_session(
            _mock_result(data={"id": 42, "name": "dev-team", "permission": "write"}),
        )
        world._servers["__admin__"] = mock_admin

        first = await world.need_team(
            "live-org", "dev-team", permission="write",
            units_map={"repo.code": "write", "repo.issues": "write"},
        )
        assert first["id"] == 42

        second = await world.need_team(
            "live-org", "dev-team", permission="write",
            units_map={"repo.code": "write", "repo.issues": "write"},
        )
        assert second is first
        assert mock_admin.call_tool.call_count == 1
