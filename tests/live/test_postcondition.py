"""Unit tests for mutable postcondition verification in ``tests/live/state.py``.

These tests verify :class:`RepoState.need_issue` and
:class:`RepoState.need_pull_request` postcondition semantics — state
transition detection, re-read verification, and irreversible-transition
guards.  All tests use mocked ``ClientSession.call_tool`` responses;
no live Gitea instance is required.
"""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import TextContent

from tests.live.conflict import (
    ConflictError,
    IrreversibleTransitionError,
    PostconditionError,
)
from tests.live.state import RepoState

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


async def _mock_session(*responses: MagicMock) -> MagicMock:
    """Build a mock ``ClientSession`` whose ``call_tool`` returns *responses*."""
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=responses)
    return session


def _new_state(**overrides: object) -> RepoState:
    """Build a minimal ``RepoState`` with monkeypatched ``_server``."""
    kwargs: dict[str, object] = {
        "owner": "dev",
        "name": "repo",
        "data": {"id": 1, "name": "repo"},
        "_world": MagicMock(),
        "_user": MagicMock(),
        "_scopes": ["write:issue"],
    }
    kwargs.update(overrides)
    return RepoState(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PostconditionError and IrreversibleTransitionError
# ---------------------------------------------------------------------------


class TestPostconditionError:
    """PostconditionError carries entity, field, expected, observed."""

    def test_all_fields_accessible(self) -> None:
        err = PostconditionError(
            "issue #1 ('Bug')", "state", "closed", "open",
        )
        assert err.entity == "issue #1 ('Bug')"
        assert err.field == "state"
        assert err.expected == "closed"
        assert err.observed == "open"
        assert isinstance(err, AssertionError)

    def test_str_includes_all_context(self) -> None:
        err = PostconditionError(
            "PR #2 ('Feature')", "state", "open", "closed",
        )
        text = str(err)
        assert "PR #2 ('Feature')" in text
        assert "state" in text
        assert "'open'" in text
        assert "'closed'" in text


class TestIrreversibleTransitionError:
    """IrreversibleTransitionError carries entity, field, expected, observed."""

    def test_all_fields_accessible(self) -> None:
        err = IrreversibleTransitionError(
            "PR #2 ('Fix')", "merged", False, True,
        )
        assert err.entity == "PR #2 ('Fix')"
        assert err.field == "merged"
        assert err.expected is False
        assert err.observed is True
        assert isinstance(err, AssertionError)

    def test_str_describes_permanence(self) -> None:
        err = IrreversibleTransitionError(
            "PR #1 ('Feature')", "merged", False, True,
        )
        text = str(err)
        assert "Irreversible" in text
        assert "cannot return" in text
        assert "merged" in text


# ---------------------------------------------------------------------------
# Issue postcondition — need_issue with state parameter
# ---------------------------------------------------------------------------


class TestIssuePostcondition:
    """Postcondition verification for ``RepoState.need_issue(state=...)``."""

    @pytest.mark.asyncio
    async def test_state_matches_postcondition_re_reads_and_passes(
        self,
    ) -> None:
        """Cache has ``open``; caller expects ``closed``; re-read confirms ``closed``."""
        state = _new_state()
        state.issues[1] = {"number": 1, "title": "Bug", "state": "open"}
        state._issue_options[1] = {"body": "desc"}
        state._issue_postcondition[1] = "open"

        mock_s = await _mock_session(
            _mock_result(data={"number": 1, "title": "Bug", "state": "closed"}),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        result = await state.need_issue("Bug", state="closed")
        assert result["state"] == "closed"
        assert state.issues[1]["state"] == "closed"

    @pytest.mark.asyncio
    async def test_state_mismatch_after_re_read_raises(self) -> None:
        """Cache has ``open``; caller expects ``closed``; re-read still ``open``."""
        state = _new_state()
        state.issues[1] = {"number": 1, "title": "Bug", "state": "open"}
        state._issue_options[1] = {"body": "desc"}
        state._issue_postcondition[1] = "open"

        mock_s = await _mock_session(
            _mock_result(data={"number": 1, "title": "Bug", "state": "open"}),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        with pytest.raises(PostconditionError) as exc:
            await state.need_issue("Bug", state="closed")
        assert exc.value.expected == "closed"
        assert exc.value.observed == "open"

    @pytest.mark.asyncio
    async def test_re_read_failure_raises_postcondition_error(self) -> None:
        """Re-read call returns an error — treated as unreadable entity."""
        state = _new_state()
        state.issues[1] = {"number": 1, "title": "Bug", "state": "open"}
        state._issue_options[1] = {"body": "desc"}
        state._issue_postcondition[1] = "open"

        mock_s = await _mock_session(
            _mock_result(is_error=True, error_text="not found"),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        with pytest.raises(PostconditionError) as exc:
            await state.need_issue("Bug", state="closed")
        assert exc.value.field == "readable"

    @pytest.mark.asyncio
    async def test_no_state_requested_skips_postcondition(self) -> None:
        """When state is None, no postcondition check happens — cached returned."""
        state = _new_state()
        state.issues[1] = {"number": 1, "title": "Bug", "state": "open"}
        state._issue_options[1] = {"body": "desc"}

        # _server should never be called
        state._server = AsyncMock()  # type: ignore[method-assign]

        result = await state.need_issue("Bug")
        assert result["state"] == "open"
        state._server.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_matches_cache_no_postcondition_needed(self) -> None:
        """Cache already has ``closed`` and caller expects ``closed`` — skip re-read."""
        state = _new_state()
        state.issues[1] = {"number": 1, "title": "Bug", "state": "closed"}
        state._issue_options[1] = {"body": "desc"}
        state._issue_postcondition[1] = "closed"

        state._server = AsyncMock()  # type: ignore[method-assign]

        result = await state.need_issue("Bug", state="closed")
        assert result["state"] == "closed"
        state._server.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_stored_postcondition_on_successful_re_read(
        self,
    ) -> None:
        """After re-read, _issue_postcondition reflects the new postcondition."""
        state = _new_state()
        state.issues[1] = {"number": 1, "title": "Bug", "state": "open"}
        state._issue_options[1] = {"body": "desc"}
        state._issue_postcondition[1] = "open"

        mock_s = await _mock_session(
            _mock_result(data={"number": 1, "title": "Bug", "state": "closed"}),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        await state.need_issue("Bug", state="closed")
        assert state._issue_postcondition[1] == "closed"

    @pytest.mark.asyncio
    async def test_create_new_issue_stores_state_in_options(self) -> None:
        """First creation of an issue stores state in _issue_postcondition."""
        state = _new_state()
        mock_s = await _mock_session(
            # list_issues returns empty
            _mock_result(data=cast("list[dict[str, object]]", [])),
            # create_issue succeeds
            _mock_result(
                data={"number": 2, "title": "New Bug", "state": "open"},
            ),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        result = await state.need_issue("New Bug", state="closed")
        assert result["state"] == "open"  # created issue is always open
        assert state._issue_postcondition[2] == "closed"
        assert "closed" not in result.values()  # state in postcondition, not in creation response

    @pytest.mark.asyncio
    async def test_adoption_finds_closed_issue_with_state_all(self) -> None:
        """Adoption helper lists issues with ``state=all``, finding closed issues."""
        state = _new_state()
        mock_s = await _mock_session(
            # list_issues with state=all returns a closed issue
            _mock_result(data=cast("list[dict[str, object]]", [
                {"number": 1, "title": "Old Bug", "state": "closed"},
            ])),
            # create_issue — must NOT be called (adoption path wins)
            _mock_result(
                data={"number": 2, "title": "Old Bug", "state": "open"},
            ),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        result = await state.need_issue("Old Bug", state="closed")
        # Adopted, not created
        assert result["number"] == 1
        assert result["state"] == "closed"
        assert state.issues[1]["title"] == "Old Bug"
        assert state._issue_postcondition[1] == "closed"
        # Only the list call was made — create never fired
        mock_s.call_tool.assert_called_once()


# ---------------------------------------------------------------------------
# PR postcondition — need_pull_request with state parameter
# ---------------------------------------------------------------------------


class TestPRPostcondition:
    """Postcondition verification for ``RepoState.need_pull_request(state=...)``."""

    @pytest.mark.asyncio
    async def test_state_matches_postcondition_re_reads_and_passes(
        self,
    ) -> None:
        """Cache has ``open``; caller expects ``closed``; re-read confirms ``closed``."""
        state = _new_state()
        state.pull_requests[1] = {
            "number": 1, "title": "PR", "state": "open", "merged": False,
        }
        state._pr_options[1] = {
            "head": "feat", "base": "main", "body": "desc",
        }
        state._pr_postcondition[1] = "open"

        mock_s = await _mock_session(
            _mock_result(data={
                "number": 1, "title": "PR", "state": "closed", "merged": False,
            }),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        result = await state.need_pull_request(
            "PR", head="feat", base="main", state="closed",
        )
        assert result["state"] == "closed"

    @pytest.mark.asyncio
    async def test_state_mismatch_after_re_read_raises(self) -> None:
        """Cache has ``open``; caller expects ``closed``; re-read still ``open``."""
        state = _new_state()
        state.pull_requests[1] = {
            "number": 1, "title": "PR", "state": "open", "merged": False,
        }
        state._pr_options[1] = {
            "head": "feat", "base": "main", "body": "desc",
        }
        state._pr_postcondition[1] = "open"

        mock_s = await _mock_session(
            _mock_result(data={
                "number": 1, "title": "PR", "state": "open", "merged": False,
            }),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        with pytest.raises(PostconditionError) as exc:
            await state.need_pull_request(
                "PR", head="feat", base="main", state="closed",
            )
        assert exc.value.expected == "closed"
        assert exc.value.observed == "open"

    @pytest.mark.asyncio
    async def test_merged_pr_requested_open_raises_irreversible(self) -> None:
        """Cache has ``closed``; caller expects ``open``; PR is merged."""
        state = _new_state()
        state.pull_requests[1] = {
            "number": 1, "title": "PR", "state": "closed", "merged": True,
        }
        state._pr_options[1] = {
            "head": "feat", "base": "main", "body": "desc",
        }
        state._pr_postcondition[1] = "closed"

        mock_s = await _mock_session(
            _mock_result(data={
                "number": 1, "title": "PR", "state": "closed", "merged": True,
            }),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        with pytest.raises(IrreversibleTransitionError) as exc:
            await state.need_pull_request(
                "PR", head="feat", base="main", state="open",
            )
        assert exc.value.expected is False  # merged flag
        assert exc.value.observed is True

    @pytest.mark.asyncio
    async def test_closed_unmerged_pr_can_be_reopened(self) -> None:
        """PR was closed without merging — reopening (open) is valid."""
        state = _new_state()
        state.pull_requests[1] = {
            "number": 1, "title": "PR", "state": "closed", "merged": False,
        }
        state._pr_options[1] = {
            "head": "feat", "base": "main", "body": "desc",
        }
        state._pr_postcondition[1] = "closed"

        mock_s = await _mock_session(
            _mock_result(data={
                "number": 1, "title": "PR", "state": "open", "merged": False,
            }),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        result = await state.need_pull_request(
            "PR", head="feat", base="main", state="open",
        )
        assert result["state"] == "open"

    @pytest.mark.asyncio
    async def test_re_read_failure_raises_postcondition_error(self) -> None:
        """Re-read returns error for a PR — PostconditionError."""
        state = _new_state()
        state.pull_requests[1] = {
            "number": 1, "title": "PR", "state": "open", "merged": False,
        }
        state._pr_options[1] = {
            "head": "feat", "base": "main", "body": "desc",
        }
        state._pr_postcondition[1] = "open"

        mock_s = await _mock_session(
            _mock_result(is_error=True, error_text="not found"),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        with pytest.raises(PostconditionError) as exc:
            await state.need_pull_request(
                "PR", head="feat", base="main", state="closed",
            )
        assert exc.value.field == "readable"

    @pytest.mark.asyncio
    async def test_no_state_requested_skips_postcondition(self) -> None:
        """When state is None for PR, no postcondition check happens."""
        state = _new_state()
        state.pull_requests[1] = {
            "number": 1, "title": "PR", "state": "open", "merged": False,
        }
        state._pr_options[1] = {
            "head": "feat", "base": "main", "body": "desc",
        }
        state._server = AsyncMock()  # type: ignore[method-assign]

        result = await state.need_pull_request(
            "PR", head="feat", base="main",
        )
        assert result["state"] == "open"
        state._server.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_new_pr_stores_state_in_options(self) -> None:
        """First creation of a PR stores state in _pr_postcondition."""
        state = _new_state()
        mock_s = await _mock_session(
            # list_pull_requests returns empty
            _mock_result(data=cast("list[dict[str, object]]", [])),
            # create_pull_request succeeds
            _mock_result(data={
                "number": 2, "title": "New PR",
                "state": "open", "head": {"ref": "feat"}, "base": {"ref": "main"},
            }),
        )
        state._server = AsyncMock(return_value=mock_s)  # type: ignore[method-assign]

        result = await state.need_pull_request(
            "New PR", head="feat", base="main", state="closed",
        )
        assert state._pr_postcondition[2] == "closed"


# ---------------------------------------------------------------------------
# Immutable param conflict (existing behavior unchanged)
# ---------------------------------------------------------------------------


class TestImmutableConflictPreserved:
    """Immutable creation parameters still raise ConflictError (unchanged)."""

    def test_issue_body_conflict(self) -> None:
        """Different body on cached issue raises ConflictError."""
        from tests.live.conflict import check_conflict

        state = _new_state()
        state.issues[1] = {"number": 1, "title": "Bug", "state": "open"}
        state._issue_options[1] = {"body": "original"}
        state._issue_postcondition[1] = "open"
        state._server = AsyncMock()  # type: ignore[method-assign]

        # check_conflict raises synchronously for immutable fields
        with pytest.raises(ConflictError) as exc:
            check_conflict(
                "issue", "#1 ('Bug')",
                state._issue_options[1],
                {"body": "changed", "labels": None, "milestone": None, "assignees": None},
            )
        assert "body" in exc.value.detail

    def test_pr_head_conflict(self) -> None:
        """Different head on cached PR raises ConflictError."""
        from tests.live.conflict import check_conflict

        with pytest.raises(ConflictError) as exc:
            check_conflict(
                "pull_request", "#1 ('PR')",
                {"head": "feat", "base": "main", "body": "desc"},
                {"head": "other", "base": "main", "body": "desc"},
            )
        assert "head" in exc.value.detail

    def test_conflict_still_fires_regardless_of_state(self) -> None:
        """``check_conflict`` raises ``ConflictError`` on immutable mismatch even
        when ``state`` would also differ — the mutable postcondition path is
        never reached because the immutable check fires first."""
        from tests.live.conflict import check_conflict

        with pytest.raises(ConflictError) as exc:
            check_conflict(
                "issue", "#1 ('Bug')",
                {"body": "original"},
                {"body": "changed", "labels": None, "milestone": None, "assignees": None},
            )
        assert "body" in exc.value.detail
