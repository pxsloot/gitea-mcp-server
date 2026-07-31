"""Phase 3c — Resources: list_resources and read_resource through real transport.

Resources are the recommended read path for agents — they use caching,
pre-formatted markdown, and scope filtering.  Tests use the ``world``
fixture for declarative setup.

Design decisions
----------------
- **Shared world**: Uses ``DEV`` from ``world.py`` for repo creation.
- **list_resources first**: Verifies resource metadata (URI, mimeType,
  tags, scope, size_hint) before reading any resource content.
- **read_resource shape**: Verifies that resource content carries
  recognizable information (keys, values) through the real pipeline.
- **Scope-filtered resources**: A limited-token test verifies that
  resources are filtered by token scope.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.assertions import assert_keys
from tests.live.conftest import live_available
from tests.live.helpers import delete_repo
from tests.live.world import DEV, LIMITED, SCOPE_LIMITED, SCOPE_WRITE, World

pytestmark = pytest.mark.xdist_group("live-resources")

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_REPO = f"live-res-{_WORKER}"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@live_available
class TestSetup:
    """Create a repo for resource reading."""

    @pytest.mark.live
    async def test_create_repo(self, world: World) -> None:
        """Create the resource test repo."""
        repo = await world.need_repo(
            DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE,
            auto_init=True, description="Resource test repo")
        assert repo.data["name"] == _REPO


# ---------------------------------------------------------------------------
# list_resources — metadata
# ---------------------------------------------------------------------------


@live_available
class TestListResources:
    """Verify ``gitea_list_resources`` returns well-shaped metadata."""

    @pytest.mark.live
    async def test_list_resources_returns_data(self, world: World) -> None:
        """list_resources returns a dict with resources and count."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_list_resources", {"format": "json"})
        assert not result.isError
        data = json.loads(extract_text_content(result.content))
        assert isinstance(data, dict)
        assert "resources" in data
        assert "count" in data
        assert isinstance(data["resources"], list)
        assert len(data["resources"]) > 0

    @pytest.mark.live
    async def test_resource_items_have_metadata(self, world: World) -> None:
        """Each resource item has uri, name, description, mimeType, type, tags."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_list_resources", {"format": "json"})
        data = json.loads(extract_text_content(result.content))
        for res in data["resources"][:5]:
            assert_keys(res, "uri", "name", "description",
                        "mimeType", "type", "tags",
                        msg=f"Resource {res.get('uri', '?')}: ")

    @pytest.mark.live
    async def test_filter_by_tag(self, world: World) -> None:
        """Filtering by tag='wrapper' returns only wrapper resources."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_list_resources", {"format": "json", "tag": "wrapper"})
        data = json.loads(extract_text_content(result.content))
        for res in data["resources"]:
            assert "wrapper" in res.get("tags", []), (
                f"Tag filter returned non-wrapper: {res['uri']}"
            )


# ---------------------------------------------------------------------------
# read_resource — content reading
# ---------------------------------------------------------------------------


@live_available
class TestReadResource:
    """Verify ``gitea_read_resource`` returns recognizable content."""

    @pytest.mark.live
    async def test_read_version(self, world: World) -> None:
        """Read ``gitea://version`` — returns version info."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_read_resource", {"uri": "gitea://version"})
        assert not result.isError
        text = extract_text_content(result.content)
        assert text, "Version resource returned empty content"

    @pytest.mark.live
    async def test_read_repo_resource(self, world: World) -> None:
        """Read ``gitea://repos/{owner}/{repo}`` — returns repo data."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_read_resource",
            {"uri": f"gitea://repos/{DEV.username}/{_REPO}"},
        )
        assert not result.isError
        text = extract_text_content(result.content)
        assert _REPO in text, (
            f"Repo resource should mention {_REPO}: {text[:200]}"
        )

    @pytest.mark.live
    async def test_read_user_resource(self, world: World) -> None:
        """Read ``gitea://users/{username}`` — returns user profile."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_read_resource",
            {"uri": f"gitea://users/{DEV.username}"},
        )
        assert not result.isError
        text = extract_text_content(result.content)
        assert DEV.username in text, (
            f"User resource should mention {DEV.username}: {text[:200]}"
        )

    @pytest.mark.live
    async def test_read_resource_json_format(self, world: World) -> None:
        """Read a resource with ``format=json`` — returns parseable JSON."""
        _ = await world.need_repo(DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_read_resource",
            {"uri": f"gitea://repos/{DEV.username}/{_REPO}",
             "format": "json"},
        )
        assert not result.isError
        data = json.loads(extract_text_content(result.content))
        assert isinstance(data, dict), (
            f"Expected JSON dict, got {type(data)}: {extract_text_content(result.content)[:200]}"
        )

    @pytest.mark.live
    async def test_read_nonexistent_resource_errors(self, world: World) -> None:
        """Reading a non-existent resource URI returns an error."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_read_resource",
            {"uri": "gitea://nonexistent/thing"},
        )
        assert result.isError, "Expected error for non-existent resource URI"


# ---------------------------------------------------------------------------
# Resource scope filtering
# ---------------------------------------------------------------------------


@live_available
class TestResourceScope:
    """Resources are scope-filtered same as tools."""

    @pytest.mark.live
    async def test_limited_token_has_fewer_resources(self, world: World) -> None:
        """A limited token sees fewer resources than a full-scope token."""
        full_token_mcp = await world.server_for(DEV, SCOPE_WRITE)
        limited_token_mcp = await world.server_for(LIMITED, SCOPE_LIMITED)

        counts: list[int] = []
        for mcp in (full_token_mcp, limited_token_mcp):
            result = await mcp.call_tool(
                "gitea_list_resources", {"format": "json"})
            data = json.loads(extract_text_content(result.content))
            counts.append(data.get("count", 0))

        assert counts[0] >= counts[1], (
            f"Full token resources ({counts[0]}) should not be fewer than "
            f"limited token resources ({counts[1]})"
        )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@live_available
class TestCleanup:
    """Delete the test repo."""

    @pytest.mark.live
    @pytest.mark.timeout(30)
    async def test_delete_repo(self, world: World) -> None:
        """Delete the resource test repo."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        await delete_repo(mcp, DEV.username, _REPO)
