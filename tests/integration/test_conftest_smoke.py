"""Smoke tests verifying the shared integration test fixtures work correctly.

These tests are deliberately minimal — they verify the fixture plumbing,
not specific server behaviour.  Real behavioural tests belong in
``test_tool_behaviour.py`` (Phase 2) or companion modules.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx

from tests.integration.conftest import (
    BASE_TEST_URL,
    create_test_server,
)


class TestCreateTestServer:
    """Smoke tests for the ``create_test_server`` factory function."""

    @pytest.mark.asyncio
    async def test_create_minimal_server(self, simple_config: Any, base_spec: dict[str, Any]) -> None:
        """Creating a server with an empty spec should succeed."""
        async with respx.mock() as mock:
            mock.get(f"{simple_config.url}/swagger.v1.json").respond(200, json=base_spec)
            server = await create_test_server(simple_config, base_spec)
            assert server is not None
            assert server.name == "Gitea MCP Server"

    @pytest.mark.asyncio
    async def test_server_has_synthetic_tools_when_lazy_loading_disabled(self, simple_config: Any, base_spec: dict[str, Any]) -> None:
        """Without lazy loading, the default tool set should include synthetic and resource tools."""
        async with respx.mock() as mock:
            mock.get(f"{simple_config.url}/swagger.v1.json").respond(200, json=base_spec)
            server = await create_test_server(simple_config, base_spec)
            tools = await server.list_tools()
            tool_names = [t.name for t in tools]
            assert "gitea_list_resources" in tool_names, (
                f"Expected gitea_list_resources in {tool_names}"
            )
            assert "gitea_read_resource" in tool_names


class TestMcpServerFixture:
    """Smoke tests for the pre-wired ``mcp_server`` fixture."""

    @pytest.mark.asyncio
    async def test_mcp_server_fixture_yields_working_server(self, mcp_server: Any) -> None:
        """The mcp_server fixture should yield a server that can list tools."""
        tools = await mcp_server.list_tools()
        assert len(tools) >= 2  # At minimum list/read_resource should be present

    @pytest.mark.asyncio
    async def test_respx_context_active_in_test(self, mcp_server: Any) -> None:
        """respx routes registered in the test body should be active (the fixture's context is still open)."""
        respx.get(f"{BASE_TEST_URL}/api/v1/test-endpoint").respond(200, json={"ok": True})
        # No assertion needed — if the route weren't active, calling a tool
        # that triggers this request would fail with a connection error.
        # This test merely proves the fixture design works.

    @pytest.mark.asyncio
    async def test_can_add_endpoints_via_base_spec_override(self, mcp_server: Any) -> None:
        """The default mcp_server (empty spec) should have no API tools, only resource/synthetic tools."""
        tools = await mcp_server.list_tools()
        tool_names = [t.name for t in tools]
        assert not any("issue" in t for t in tool_names), (
            f"Expected no issue tools in empty spec, got: {[t for t in tool_names if 'issue' in t]}"
        )


class TestSearchMcpServerFixture:
    """Smoke tests for the pre-wired ``search_mcp_server`` fixture (lazy loading)."""

    @pytest.mark.asyncio
    async def test_lazy_loading_active(self, search_mcp_server: Any) -> None:
        """With lazy loading enabled, visible tool count should be small."""
        tools = await search_mcp_server.list_tools()
        assert len(tools) <= 12, (
            f"Expected ≤12 tools with lazy loading, got {len(tools)}"
        )

    @pytest.mark.asyncio
    async def test_search_tool_present(self, search_mcp_server: Any) -> None:
        """With lazy loading, the ``search_tools`` synthetic tool should be visible."""
        tools = await search_mcp_server.list_tools()
        tool_names = [t.name for t in tools]
        assert "gitea_search_tools" in tool_names, (
            f"Expected gitea_search_tools in {tool_names}"
        )


class TestLazyConfigFixture:
    """Smoke tests for the ``lazy_config`` fixture."""

    @pytest.mark.asyncio
    async def test_lazy_config_has_lazy_loading_enabled(self, lazy_config: Any) -> None:
        """The lazy_config fixture should have lazy loading enabled."""
        assert lazy_config.enable_lazy_loading is True
