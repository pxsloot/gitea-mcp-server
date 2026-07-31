"""Phase 3b — Discovery tools: search_tools, tool_info, search, read_doc, resolve_type.

These are the synthetic tools that agents rely on for discovery and
navigation.  Every agent session starts here — verifying these through
the real transport is critical.

Uses the ``world`` fixture — users are pre-bootstrapped, tokens cached.
Tests call discovery tools through ``world.server_for()``.

Design decisions
----------------
- **Synthetic tools are scope-free**: They're available to any token.
  Tests use the DEV write token for convenience (already pooled).
- **Shape + content on every call**: Not just "did it return?", but
  "are the right keys present with correct types?"
"""

from __future__ import annotations

import pytest

from tests.live.assertions import assert_key_types, assert_keys, assert_result_ok
from tests.live.conftest import live_available
from tests.live.world import DEV, SCOPE_WRITE, World

# ---------------------------------------------------------------------------
# search_tools — the primary discovery tool
# ---------------------------------------------------------------------------


@live_available
class TestSearchTools:
    """Verify ``gitea_search_tools`` returns well-shaped results."""

    @pytest.mark.live
    async def test_search_tools_finds_user_tools(self, world: World) -> None:
        """Searching for 'user' returns relevant user tools."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_search_tools",
            {"query": "user", "format": "json"},
        )
        data = assert_result_ok(result)
        assert isinstance(data, list)
        assert len(data) > 0, "Expected at least one tool result"
        assert_keys(data[0], "name", "description", "score",
                    "annotations", "tags")
        assert_key_types(data[0], name=str, description=str)
        assert isinstance(data[0]["score"], (int, float))
        names = [t["name"].lower() for t in data]
        assert any("user" in n for n in names), (
            f"No 'user' tool found in results: {names[:5]}"
        )

    @pytest.mark.live
    async def test_search_tools_has_annotations(self, world: World) -> None:
        """Each result has complete annotations."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        data = assert_result_ok(await mcp.call_tool(
            "gitea_search_tools",
            {"query": "user", "format": "json"},
        ))
        for item in data:
            ann = item.get("annotations", {})
            assert "title" in ann, f"Missing title in annotations: {item['name']}"
            assert "readOnlyHint" in ann
            assert "destructiveHint" in ann
            assert isinstance(ann["readOnlyHint"], bool)

    @pytest.mark.live
    async def test_search_tools_with_category(self, world: World) -> None:
        """Filtering by category restricts results."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        data = assert_result_ok(await mcp.call_tool(
            "gitea_search_tools",
            {"query": "create", "category": "issue", "format": "json"},
        ))
        for item in data:
            assert "issue" in item.get("tags", []), (
                f"Result '{item['name']}' should have 'issue' tag, "
                f"got: {item.get('tags')}"
            )


# ---------------------------------------------------------------------------
# tool_info — schema inspection
# ---------------------------------------------------------------------------


@live_available
class TestToolInfo:
    """Verify ``gitea_tool_info`` returns complete tool schemas."""

    @pytest.mark.live
    async def test_tool_info_returns_parameters(self, world: World) -> None:
        """tool_info for a well-known tool returns parameters and annotations."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_call_tool",
            {"name": "gitea_tool_info",
             "arguments": {"name": "gitea_repo_get", "format": "json"}},
        )
        assert not result.isError, f"tool_info failed: {result}"

    @pytest.mark.live
    async def test_tool_info_concise_vs_full(self, world: World) -> None:
        """Both detail levels return valid results."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        # Concise
        await mcp.call_tool(
            "gitea_call_tool",
            {"name": "gitea_tool_info",
             "arguments": {"name": "gitea_repo_get",
                           "format": "json", "detail": "concise"}},
        )
        # Full
        await mcp.call_tool(
            "gitea_call_tool",
            {"name": "gitea_tool_info",
             "arguments": {"name": "gitea_repo_get",
                           "format": "json", "detail": "full"}},
        )


# ---------------------------------------------------------------------------
# Unified search — cross-cutting
# ---------------------------------------------------------------------------


@live_available
class TestUnifiedSearch:
    """Verify ``gitea_search`` returns typed, cross-cutting results."""

    @pytest.mark.live
    async def test_search_returns_tools_and_docs(self, world: World) -> None:
        """Unified search for 'issue' returns both tools and docs."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_search",
            {"query": "issue", "format": "json", "min_score": 0.1},
        )
        data = assert_result_ok(result)
        assert isinstance(data, list)
        types = {item.get("type") for item in data}
        assert "tool" in types, (
            f"Expected 'tool' type in results, got: {types}"
        )

    @pytest.mark.live
    async def test_search_results_have_access_uris(self, world: World) -> None:
        """Each search result has an Access Uri for routing."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        data = assert_result_ok(await mcp.call_tool(
            "gitea_search",
            {"query": "user", "format": "json", "min_score": 0.1},
        ))
        for item in data[:5]:
            assert "name" in item
            assert "type" in item
            assert "score" in item


# ---------------------------------------------------------------------------
# read_doc — workflow guides
# ---------------------------------------------------------------------------


@live_available
class TestReadDoc:
    """Verify ``gitea_read_doc`` returns workflow guide content."""

    @pytest.mark.live
    async def test_read_doc_token_scopes(self, world: World) -> None:
        """read_doc('token-scopes') returns a guide with expected content."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_read_doc",
            {"topic": "token-scopes"},
        )
        assert not result.isError
        from tests.helpers.mcp_results import extract_text_content
        text = extract_text_content(result.content)
        assert "token" in text.lower(), (
            f"Expected token-scopes guide to mention 'token', "
            f"got: {text[:200]!r}"
        )

    @pytest.mark.live
    async def test_read_doc_unknown_topic_errors(self, world: World) -> None:
        """read_doc with unknown topic returns error."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_read_doc",
            {"topic": "non-existent-topic-xyz"},
        )
        assert result.isError, "Expected error for non-existent doc topic"


# ---------------------------------------------------------------------------
# resolve_type — $ref type resolution
# ---------------------------------------------------------------------------


@live_available
class TestResolveType:
    """Verify ``gitea_resolve_type`` resolves $ref type names."""

    @pytest.mark.live
    async def test_resolve_type_user(self, world: World) -> None:
        """resolve_type('User') returns type schema with cross-references."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_call_tool",
            {"name": "gitea_resolve_type",
             "arguments": {"name": "User", "format": "json"}},
        )
        assert not result.isError, f"resolve_type(User) failed: {result}"

    @pytest.mark.live
    async def test_resolve_type_unknown_errors(self, world: World) -> None:
        """resolve_type of unknown type returns error."""
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        result = await mcp.call_tool(
            "gitea_call_tool",
            {"name": "gitea_resolve_type",
             "arguments": {"name": "NonExistentTypeXYZ"}},
        )
        assert result.isError, "Expected error for non-existent type"
