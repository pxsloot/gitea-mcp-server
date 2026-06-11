"""Tests for ExtensionMetadataTransform."""

from collections.abc import Sequence
from typing import Any

import pytest
from fastmcp.tools.base import Tool, ToolAnnotations

from gitea_mcp_server.tools.extensions_metadata import ExtensionMetadataTransform


def _make_tool(
    name: str,
    description: str = "",
    tags: set[str] | None = None,
    title: str | None = None,
    annotations: ToolAnnotations | None = None,
) -> Tool:
    return Tool(
        name=name,
        description=description,
        tags=tags or set(),
        title=title,
        annotations=annotations,
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


class TestExtensionMetadataTransformNewFields:
    """Tests for the expanded field support in ExtensionMetadataTransform."""

    @pytest.mark.asyncio
    async def test_overrides_title(self):
        """title override should propagate to tool title."""
        transform = ExtensionMetadataTransform({"search": {"title": "Custom Title"}})
        tool = _make_tool("search", title="Old Title")
        result = await transform.list_tools([tool])
        assert result[0].title == "Custom Title"

    @pytest.mark.asyncio
    async def test_overrides_tags(self):
        """tags override should propagate (YAML list coerced to set)."""
        transform = ExtensionMetadataTransform({"search": {"tags": ["custom", "tag"]}})
        tool = _make_tool("search", tags={"old"})
        result = await transform.list_tools([tool])
        assert result[0].tags == {"custom", "tag"}

    @pytest.mark.asyncio
    async def test_overrides_read_only_hint(self):
        """readOnlyHint should propagate into ToolAnnotations."""
        transform = ExtensionMetadataTransform({"search": {"readOnlyHint": True}})
        tool = _make_tool("search")
        result = await transform.list_tools([tool])
        assert result[0].annotations is not None
        assert result[0].annotations.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_overrides_destructive_hint(self):
        """destructiveHint should propagate into ToolAnnotations."""
        transform = ExtensionMetadataTransform({"search": {"destructiveHint": False}})
        tool = _make_tool("search")
        result = await transform.list_tools([tool])
        assert result[0].annotations is not None
        assert result[0].annotations.destructiveHint is False

    @pytest.mark.asyncio
    async def test_overrides_idempotent_hint(self):
        """idempotentHint should propagate into ToolAnnotations."""
        transform = ExtensionMetadataTransform({"search": {"idempotentHint": True}})
        tool = _make_tool("search")
        result = await transform.list_tools([tool])
        assert result[0].annotations is not None
        assert result[0].annotations.idempotentHint is True

    @pytest.mark.asyncio
    async def test_overrides_open_world_hint(self):
        """openWorldHint should propagate into ToolAnnotations."""
        transform = ExtensionMetadataTransform({"search": {"openWorldHint": False}})
        tool = _make_tool("search")
        result = await transform.list_tools([tool])
        assert result[0].annotations is not None
        assert result[0].annotations.openWorldHint is False

    @pytest.mark.asyncio
    async def test_merges_annotations_with_existing(self):
        """Annotation overrides merge with existing annotations, not replace them."""
        transform = ExtensionMetadataTransform({"search": {"idempotentHint": True}})
        existing = ToolAnnotations(readOnlyHint=True, destructiveHint=True)
        tool = _make_tool("search", annotations=existing)
        result = await transform.list_tools([tool])
        assert result[0].annotations is not None
        assert result[0].annotations.readOnlyHint is True  # preserved
        assert result[0].annotations.destructiveHint is True  # preserved
        assert result[0].annotations.idempotentHint is True  # overridden

    @pytest.mark.asyncio
    async def test_creates_annotations_when_none(self):
        """When tool has no annotations, override creates them."""
        transform = ExtensionMetadataTransform({"search": {"readOnlyHint": True}})
        tool = _make_tool("search", annotations=None)
        result = await transform.list_tools([tool])
        assert result[0].annotations is not None
        assert result[0].annotations.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_combined_component_and_annotation_overrides(self):
        """title, description, tags, and hints can all be overridden in one entry."""
        transform = ExtensionMetadataTransform({
            "search": {
                "title": "New Title",
                "description": "New description",
                "tags": ["a", "b"],
                "readOnlyHint": True,
                "idempotentHint": True,
            }
        })
        tool = _make_tool("search", description="Old", tags={"x"}, title="Old Title")
        result = await transform.list_tools([tool])
        assert result[0].title == "New Title"
        assert result[0].description == "New description"
        assert result[0].tags == {"a", "b"}
        assert result[0].annotations is not None
        assert result[0].annotations.readOnlyHint is True
        assert result[0].annotations.idempotentHint is True

    @pytest.mark.asyncio
    async def test_override_does_not_clear_unspecified_fields(self):
        """Only fields present in the override are changed; others pass through."""
        transform = ExtensionMetadataTransform({"search": {"title": "Only Title"}})
        tool = _make_tool("search", description="Keep me", tags={"keep"})
        result = await transform.list_tools([tool])
        assert result[0].title == "Only Title"
        assert result[0].description == "Keep me"
        assert result[0].tags == {"keep"}

