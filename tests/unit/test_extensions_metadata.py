"""Tests for ExtensionMetadataTransform."""

from collections.abc import Sequence
from typing import Any

import pytest
from fastmcp.tools.base import Tool

from gitea_mcp_server.tools.extensions_metadata import ExtensionMetadataTransform


def _make_tool(name: str, description: str = "", tags: set[str] | None = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        tags=tags or set(),
        parameters={},
    )


class TestExtensionMetadataTransform:
    """Tests for the ExtensionMetadataTransform transform."""

    TOOL_NAMES: dict[str, dict[str, str]] = {
        "search": {"description": "Unified search across tools, docs, and resources"},
        "create_issue": {"description": "Create a new issue in the repository"},
    }

    @pytest.mark.asyncio
    async def test_list_tools_applies_description_override(self):
        """list_tools should override description for matching tools."""
        transform = ExtensionMetadataTransform(self.TOOL_NAMES)
        tools = [
            _make_tool("search", "old description"),
            _make_tool("other_tool", "kept description"),
        ]
        result = await transform.list_tools(tools)
        assert len(result) == 2
        assert result[0].description == "Unified search across tools, docs, and resources"
        assert result[1].description == "kept description"

    @pytest.mark.asyncio
    async def test_list_tools_passthrough_unknown_tools(self):
        """list_tools should leave tools without overrides unchanged."""
        transform = ExtensionMetadataTransform({})
        tool = _make_tool("search", "original desc")
        result = await transform.list_tools([tool])
        assert result[0].description == "original desc"

    @pytest.mark.asyncio
    async def test_list_tools_preserves_name_and_tags(self):
        """list_tools should only modify description, not other fields."""
        transform = ExtensionMetadataTransform(self.TOOL_NAMES)
        tool = _make_tool("search", "old", tags={"synthetic"})
        result = await transform.list_tools([tool])
        assert result[0].name == "search"
        assert result[0].tags == {"synthetic"}
        assert result[0].description == "Unified search across tools, docs, and resources"

    @pytest.mark.asyncio
    async def test_get_tool_applies_description_override(self):
        """get_tool should override description for a matching tool."""

        async def call_next(name: str, **kwargs: Any) -> Tool | None:
            return _make_tool(name, "old description")

        transform = ExtensionMetadataTransform(self.TOOL_NAMES)
        tool = await transform.get_tool("search", call_next)
        assert tool is not None
        assert tool.description == "Unified search across tools, docs, and resources"

    @pytest.mark.asyncio
    async def test_get_tool_passthrough_unknown_tool(self):
        """get_tool should return unchanged for tools without overrides."""

        async def call_next(name: str, **kwargs: Any) -> Tool | None:
            return _make_tool(name, "original desc")

        transform = ExtensionMetadataTransform(self.TOOL_NAMES)
        tool = await transform.get_tool("unknown_tool", call_next)
        assert tool is not None
        assert tool.description == "original desc"

    @pytest.mark.asyncio
    async def test_get_tool_returns_none_when_missing(self):
        """get_tool should return None when downstream returns None."""

        async def call_next(name: str, **kwargs: Any) -> Tool | None:
            return None

        transform = ExtensionMetadataTransform(self.TOOL_NAMES)
        tool = await transform.get_tool("search", call_next)
        assert tool is None

    @pytest.mark.asyncio
    async def test_list_tools_with_prefixed_names(self):
        """list_tools should match prefixed names when prefix is set."""
        transform = ExtensionMetadataTransform(self.TOOL_NAMES, prefix="gitea_")
        tool = _make_tool("gitea_search", "old")
        result = await transform.list_tools([tool])
        assert result[0].description == "Unified search across tools, docs, and resources"

    @pytest.mark.asyncio
    async def test_get_tool_with_prefixed_names(self):
        """get_tool should match prefixed names when prefix is set."""

        async def call_next(name: str, **kwargs: Any) -> Tool | None:
            return _make_tool(name, "old")

        transform = ExtensionMetadataTransform(self.TOOL_NAMES, prefix="gitea_")
        tool = await transform.get_tool("gitea_search", call_next)
        assert tool is not None
        assert tool.description == "Unified search across tools, docs, and resources"

    @pytest.mark.asyncio
    async def test_list_tools_also_matches_unprefixed_with_prefix(self):
        """With prefix set, unprefixed names should also match."""
        transform = ExtensionMetadataTransform(self.TOOL_NAMES, prefix="gitea_")
        tool = _make_tool("search", "old")
        result = await transform.list_tools([tool])
        assert result[0].description == "Unified search across tools, docs, and resources"

    @pytest.mark.asyncio
    async def test_get_tool_also_matches_unprefixed_with_prefix(self):
        """With prefix set, unprefixed names should also match in get_tool."""

        async def call_next(name: str, **kwargs: Any) -> Tool | None:
            return _make_tool(name, "old")

        transform = ExtensionMetadataTransform(self.TOOL_NAMES, prefix="gitea_")
        tool = await transform.get_tool("search", call_next)
        assert tool is not None
        assert tool.description == "Unified search across tools, docs, and resources"

