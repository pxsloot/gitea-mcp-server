"""Phase 3a — Cross-format equivalence: systematic json↔markdown↔raw verification.

Tests that the three format options produce equivalent information across
the full transport stack.  Uses ``assert_formats_equivalent`` helper to
test edge cases: empty results, nested objects, raw format, and detail
levels.

Uses the ``world`` fixture — setup is declarative via ``need_repo``.
Cross-format calls use ``world.server_for()`` for pooled server access.

Design decisions
----------------
- **Read-only tools**: All format tests use read operations (no side effects).
  ``DEV`` token with ``SCOPE_WRITE`` is sufficient.
- **One assertion per format edge case**: Empty results, nested objects, raw
  format, concise detail — each a distinct boundary.
- **Cross-format is about information content, not field names**: The
  assertions match on leaf values, not camelCase↔Title Case mappings.
"""

from __future__ import annotations

import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.assertions import assert_formats_equivalent, assert_keys, assert_result_ok
from tests.live.conftest import live_available
from tests.live.helpers import delete_repo
from tests.live.world import DEV, SCOPE_WRITE, World

pytestmark = pytest.mark.xdist_group("live-cross-format")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_REPO = f"live-fmt-{_WORKER}"


# ---------------------------------------------------------------------------
# Setup — a repo to read
# ---------------------------------------------------------------------------


@live_available
class TestSetup:
    """Create a repo for format tests to read."""

    @pytest.mark.live
    async def test_create_repo(self, world: World) -> None:
        """Create a minimal repo."""
        repo = await world.need_repo(
            DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE, auto_init=True)
        assert repo.data["name"] == _REPO


# ---------------------------------------------------------------------------
# Core format equivalence
# ---------------------------------------------------------------------------


@live_available
class TestJsonMarkdownEquivalence:
    """json ↔ markdown for different data shapes."""

    @pytest.mark.live
    async def test_single_object_equivalence(self, world: World) -> None:
        """Single object (repo get): json and markdown carry same data."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await assert_formats_equivalent(
            mcp, "gitea_repo_get",
            {"owner": DEV.username, "repo": _REPO},
        )

    @pytest.mark.live
    async def test_list_equivalence(self, world: World) -> None:
        """List result (list labels): json and markdown carry same data."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await assert_formats_equivalent(
            mcp, "gitea_issue_list_labels",
            {"owner": DEV.username, "repo": _REPO},
        )

    @pytest.mark.live
    async def test_empty_list_equivalence(self, world: World) -> None:
        """Empty search result: json=[] → markdown shows clear empty state."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await assert_formats_equivalent(
            mcp, "gitea_issue_search_issues",
            {"q": "zzzzthisdoesnotexist9999", "owner": DEV.username,
             "repo": _REPO},
            skip_values=True,  # empty is just []
        )


# ---------------------------------------------------------------------------
# Raw format
# ---------------------------------------------------------------------------


@live_available
class TestRawFormat:
    """Raw format returns API-raw responses (may be wrapped or markdown)."""

    @pytest.mark.live
    async def test_raw_format_returns_content(self, world: World) -> None:
        """Format=raw on a repo get returns content (possibly wrapped)."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_get",
            {"owner": DEV.username, "repo": _REPO, "format": "raw"},
        )
        assert not result.isError
        import json
        text = extract_text_content(result.content)
        data = json.loads(text)
        if isinstance(data, dict) and list(data.keys()) == ["result"]:
            data = data["result"]
        assert_keys(data, "name", "full_name", "id")
        assert data["name"] == _REPO

    @pytest.mark.live
    async def test_raw_list_returns_parseable(self, world: World) -> None:
        """Format=raw on list labels returns parseable content."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_issue_list_labels",
            {"owner": DEV.username, "repo": _REPO, "format": "raw"},
        )
        assert not result.isError
        text = extract_text_content(result.content)
        import json
        try:
            data = json.loads(text)
            assert isinstance(data, (dict, list))
        except json.JSONDecodeError:
            assert len(text) > 0, "Raw list returned empty content"


# ---------------------------------------------------------------------------
# Detail levels
# ---------------------------------------------------------------------------


@live_available
class TestDetailLevels:
    """Concise vs full detail on nested objects."""

    @pytest.mark.live
    async def test_concise_collapses_refs(self, world: World) -> None:
        """Detail=concise collapses $ref:nested objects to type labels."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_get",
            {"owner": DEV.username, "repo": _REPO,
             "format": "json", "detail": "concise"},
        )
        data = assert_result_ok(result)
        owner = data.get("owner")
        assert isinstance(owner, str), (
            f"Concise detail should collapse 'owner' to $ref:Type, "
            f"got {type(owner).__name__}: {owner!r}"
        )
        assert "$ref:" in owner, (
            f"Expected $ref: prefix, got {owner!r}"
        )

    @pytest.mark.live
    async def test_full_detail_expands_all(self, world: World) -> None:
        """Detail=full expands all nested objects."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_repo_get",
            {"owner": DEV.username, "repo": _REPO,
             "format": "json", "detail": "full"},
        )
        data = assert_result_ok(result)
        owner = data.get("owner")
        assert isinstance(owner, dict), (
            f"Full detail should expand 'owner' to dict, "
            f"got {type(owner).__name__}: {owner!r}"
        )
        assert "login" in owner


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@live_available
class TestCleanup:
    """Delete the test repo."""

    @pytest.mark.live
    @pytest.mark.timeout(30)
    async def test_delete_repo(self, world: World) -> None:
        """Delete the format test repo."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await delete_repo(mcp, DEV.username, _REPO)
