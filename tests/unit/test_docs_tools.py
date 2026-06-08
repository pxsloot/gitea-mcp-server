"""Tests for docs_tools module."""

import json as json_module
from unittest.mock import MagicMock

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.docs_tools import DocGuide, DocManager, register_doc_tools
from gitea_mcp_server.search import BM25SearchEngine

# Sample guide content for testing
SAMPLE_FRONTMATTER = """\
---
title: Test Guide
description: A test guide description
tags: [test, example, guide]
source: Test Source
---

# Test Guide

This is the body of the test guide."""

SAMPLE_NO_FRONTMATTER = """\
# No Frontmatter Guide

This guide has no YAML frontmatter."""

SAMPLE_INVALID_YAML = """\
---
invalid: yaml: [
---
Content after bad frontmatter."""


class TestDocGuide:
    """Tests for DocGuide class."""

    def test_search_text_includes_all_fields(self):
        guide = DocGuide(
            name="test-guide",
            title="Test Guide",
            description="A test guide",
            tags=["tag1", "tag2"],
            full_content="# content",
            markdown_body="actual body content here",
        )
        text = guide.search_text()
        assert text.count("test-guide") == 3
        assert "Test Guide" in text
        assert "A test guide" in text
        assert "tag1" in text
        assert "tag2" in text
        assert "actual body content" in text


class TestDocManagerParseGuide:
    """Tests for DocManager._parse_guide static method."""

    def test_parses_frontmatter(self):
        guide = DocManager._parse_guide("test-guide", SAMPLE_FRONTMATTER)
        assert guide is not None
        assert guide.name == "test-guide"
        assert guide.title == "Test Guide"
        assert guide.description == "A test guide description"
        assert guide.tags == ["test", "example", "guide"]
        assert "This is the body" in guide.markdown_body

    def test_no_frontmatter(self):
        guide = DocManager._parse_guide("no-fm", SAMPLE_NO_FRONTMATTER)
        assert guide is not None
        assert guide.name == "no-fm"
        assert guide.title == "No Fm"
        assert guide.description == ""
        assert guide.tags == []
        assert guide.markdown_body == SAMPLE_NO_FRONTMATTER

    def test_invalid_yaml_uses_defaults(self):
        guide = DocManager._parse_guide("bad-yaml", SAMPLE_INVALID_YAML)
        assert guide is not None
        assert guide.name == "bad-yaml"
        assert guide.markdown_body is not None
        assert "Content after bad frontmatter" in guide.markdown_body


class TestDocManager:
    """Tests for DocManager search, get, manifest."""

    @staticmethod
    def _make_manager(guides: list[DocGuide] | None = None) -> DocManager:
        mgr = DocManager.__new__(DocManager)
        lst = guides or []
        mgr._guides = lst
        mgr._search_texts = [g.search_text() for g in lst]
        mgr._search_engine = BM25SearchEngine()
        return mgr

    def test_get_finds_by_name_case_insensitive(self):
        guide = DocGuide("Token-Scopes", "Token Scopes", "desc", [], "# content", "body")
        mgr = self._make_manager([guide])
        assert mgr.get("token-scopes") is guide
        assert mgr.get("TOKEN-SCOPES") is guide
        assert mgr.get("Token-Scopes") is guide

    def test_get_returns_none_for_missing(self):
        mgr = self._make_manager([])
        assert mgr.get("nonexistent") is None

    def test_search_returns_all_when_empty_query(self):
        guides = [
            DocGuide("guide-a", "Guide A", "desc a", ["t1"], "# a", "a"),
            DocGuide("guide-b", "Guide B", "desc b", ["t2"], "# b", "b"),
        ]
        mgr = self._make_manager(guides)
        results = mgr.search("")
        assert len(results) == 2

    def test_search_returns_empty_when_no_guides(self):
        mgr = self._make_manager([])
        assert mgr.search("test") == []

    def test_search_limits_results(self):
        guides = [
            DocGuide(f"guide-{i}", f"Guide {i}", "desc", ["t"], "# content", "body")
            for i in range(20)
        ]
        mgr = self._make_manager(guides)
        results = mgr.search("guide", max_results=5)
        assert len(results) <= 5

    def test_search_returns_metadata_dicts(self):
        guide = DocGuide("test-guide", "Test Guide", "A test guide", ["tag1"], "# content", "body")
        mgr = self._make_manager([guide])
        results = mgr.search("test")
        assert len(results) == 1
        entry = results[0]
        assert entry["name"] == "test-guide"
        assert entry["title"] == "Test Guide"
        assert entry["description"] == "A test guide"
        assert entry["tags"] == ["tag1"]

    def test_manifest_markdown_empty_when_no_guides(self):
        mgr = self._make_manager([])
        assert mgr.get_manifest_markdown() == ""

    def test_manifest_markdown_lists_guides(self):
        guide = DocGuide("test-guide", "Test Guide", "A test guide description", ["tag1"], "# content", "body")
        mgr = self._make_manager([guide])
        manifest = mgr.get_manifest_markdown()
        assert "Workflow Guides" in manifest
        assert "test-guide" in manifest
        assert "A test guide description" in manifest
        assert "search_docs" in manifest

    def test_manifest_truncates_long_descriptions(self):
        long_desc = "x" * 100
        guide = DocGuide("test-guide", "Test Guide", long_desc, [], "# content", "body")
        mgr = self._make_manager([guide])
        manifest = mgr.get_manifest_markdown()
        assert len(long_desc) > 80
        assert "..." in manifest


class TestRegisterDocTools:
    """Tests for register_doc_tools registration and tool behavior."""

    @staticmethod
    def _make_manager() -> DocManager:
        mgr = DocManager.__new__(DocManager)
        guides = [
            DocGuide("test", "Test Guide", "A test guide", ["tag1"], "# Test\n\nContent", "Content"),
        ]
        mgr._guides = guides
        mgr._search_texts = [g.search_text() for g in guides]
        mgr._search_engine = BM25SearchEngine()
        return mgr

    def _capture_tool(self, name: str):
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        mcp.tool = tool_decorator
        register_doc_tools(mcp, self._make_manager())
        fn = captured.get(name)
        assert fn is not None, f"Tool '{name}' not found"
        return fn

    def test_registers_two_tools(self):
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)
        register_doc_tools(mcp, self._make_manager())
        assert mcp.tool.call_count == 2

    def test_tools_have_openworld_false(self):
        """search_docs and read_doc should have openWorldHint=False."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)
        register_doc_tools(mcp, self._make_manager())

        for call_args in mcp.tool.call_args_list:
            kwargs = call_args[1]
            annotations = kwargs.get("annotations")
            assert annotations is not None, f"{kwargs.get('name', 'unnamed')} missing annotations"
            assert annotations.openWorldHint is False, f"{kwargs.get('name', 'unnamed')}.openWorldHint should be False"

    def test_registers_one_resource(self):
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)
        register_doc_tools(mcp, self._make_manager())
        assert mcp.resource.call_count == 1

    def test_resource_tags_include_guide_frontmatter_tags(self):
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)
        register_doc_tools(mcp, self._make_manager())
        kwargs = mcp.resource.call_args[1]
        tags = kwargs["tags"]
        assert "tag1" in tags, "guide frontmatter tag should be in resource tags"
        assert "test" in tags, "guide topic name should be in resource tags"
        assert "docs" in tags, "base docs tag should be present"
        assert "guide" in tags, "base guide tag should be present"

    def test_resource_description_includes_guide_topics(self):
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)
        register_doc_tools(mcp, self._make_manager())
        kwargs = mcp.resource.call_args[1]
        desc = kwargs["description"]
        assert "Topics:" in desc
        assert "test" in desc
        assert "search_docs()" in desc
        assert "read_doc(topic)" in desc

    @pytest.mark.asyncio
    async def test_search_docs_returns_markdown_by_default(self):
        fn = self._capture_tool("search_docs")
        result = await fn(query="test")
        assert isinstance(result, ToolResult)
        assert result.structured_content["result"] is not None
        assert result.content is not None
        assert any("Test Guide" in str(c.text) for c in result.content)

    @pytest.mark.asyncio
    async def test_search_docs_raw_format(self):
        fn = self._capture_tool("search_docs")
        result = await fn(query="test", format="raw")
        assert isinstance(result, ToolResult)
        assert isinstance(result.structured_content["result"], list)
        # ToolResult copies structured_content to content when content is None

    @pytest.mark.asyncio
    async def test_search_docs_json_format(self):
        fn = self._capture_tool("search_docs")
        result = await fn(query="test", format="json")
        assert isinstance(result, ToolResult)
        assert result.content is not None
        text = "".join(c.text for c in result.content if hasattr(c, "text"))
        parsed = json_module.loads(text)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "test"

    @pytest.mark.asyncio
    async def test_read_doc_returns_content(self):
        fn = self._capture_tool("read_doc")
        result = await fn(topic="test")
        assert isinstance(result, ToolResult)
        assert "# Test" in result.structured_content["result"]

    @pytest.mark.asyncio
    async def test_read_doc_case_insensitive(self):
        fn = self._capture_tool("read_doc")
        result = await fn(topic="TEST")
        assert "# Test" in result.structured_content["result"]

    @pytest.mark.asyncio
    async def test_read_doc_not_found_raises(self):
        fn = self._capture_tool("read_doc")
        with pytest.raises(ValueError, match="Guide 'unknown' not found"):
            await fn(topic="unknown")

    @pytest.mark.asyncio
    async def test_read_doc_error_includes_available_guides(self):
        fn = self._capture_tool("read_doc")
        with pytest.raises(ValueError) as exc_info:
            await fn(topic="unknown")
        msg = str(exc_info.value)
        assert "Available guides:" in msg
        assert "test" in msg  # the test guide name
        assert "search_docs()" in msg

    @pytest.mark.asyncio
    async def test_read_doc_raw_format(self):
        fn = self._capture_tool("read_doc")
        result = await fn(topic="test", format="raw")
        assert "# Test" in result.structured_content["result"]
        assert "Content" in result.structured_content["result"]

    @pytest.mark.asyncio
    async def test_read_doc_raw_and_markdown_match(self):
        """raw and markdown formats should return identical content (both with frontmatter)."""
        fn = self._capture_tool("read_doc")
        raw = await fn(topic="test", format="raw")
        md = await fn(topic="test", format="markdown")
        assert raw.structured_content["result"] == md.structured_content["result"]

    @pytest.mark.asyncio
    async def test_read_doc_markdown_includes_frontmatter(self):
        fn = self._capture_tool("read_doc")
        result = await fn(topic="test", format="markdown")
        assert "# Test" in result.structured_content["result"]

    @pytest.mark.asyncio
    async def test_search_docs_invalid_format_raises(self):
        fn = self._capture_tool("search_docs")
        with pytest.raises(ValueError, match="Unsupported format 'xml'"):
            await fn(query="test", format="xml")

    @pytest.mark.asyncio
    async def test_read_doc_invalid_format_raises(self):
        fn = self._capture_tool("read_doc")
        with pytest.raises(ValueError, match="Unsupported format 'xml'"):
            await fn(topic="test", format="xml")

    @pytest.mark.asyncio
    async def test_search_docs_markdown_includes_cross_link_footer(self):
        fn = self._capture_tool("search_docs")
        result = await fn(query="test")
        assert result.content is not None
        text = "".join(c.text for c in result.content)
        assert "Cross-linking hints" in text
        assert "search_tools" in text
        assert "search_resources" in text

    @pytest.mark.asyncio
    async def test_search_docs_empty_result_has_helpful_hint(self):
        fn = self._capture_tool("search_docs")
        result = await fn(query="zzz_nonexistent")
        assert result.content is not None
        text = "".join(c.text for c in result.content)
        assert "No workflow guides found" in text
        assert "search_tools" in text
        assert "search_resources" in text
        assert result.structured_content is not None
        assert result.structured_content["result"] == []

    def test_resource_tags_aggregated_across_multiple_guides(self):
        """Tags from multiple guides should all appear in resource template tags."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)
        mgr = DocManager.__new__(DocManager)
        guides = [
            DocGuide("wiki", "Wiki Guide", "Wiki docs", ["wiki", "documentation"], "# Wiki", "Wiki body"),
            DocGuide("labels", "Labels Guide", "Labels docs", ["labels", "issue"], "# Labels", "Labels body"),
        ]
        mgr._guides = guides
        mgr._search_texts = [g.search_text() for g in guides]
        mgr._search_engine = BM25SearchEngine()
        register_doc_tools(mcp, mgr)
        kwargs = mcp.resource.call_args[1]
        tags = kwargs["tags"]
        for tag in ("wiki", "labels", "documentation", "issue", "docs", "guide", "workflow"):
            assert tag in tags, f"Expected tag '{tag}' in aggregated resource tags"
        desc = kwargs["description"]
        assert "Topics:" in desc
        assert "wiki" in desc
        assert "labels" in desc

    def test_resource_description_includes_multiple_topics(self):
        """Description should list all guide topics when multiple guides exist."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)
        mgr = DocManager.__new__(DocManager)
        guides = [
            DocGuide("alpha", "Alpha", "First guide", [], "# A", "A"),
            DocGuide("beta", "Beta", "Second guide", ["tag-x"], "# B", "B"),
        ]
        mgr._guides = guides
        mgr._search_texts = [g.search_text() for g in guides]
        mgr._search_engine = BM25SearchEngine()
        register_doc_tools(mcp, mgr)
        kwargs = mcp.resource.call_args[1]
        desc = kwargs["description"]
        assert "alpha" in desc
        assert "beta" in desc
        assert "Topics:" in desc


class TestDocResource:
    """Tests for the doc resource function."""

    def _capture_resource(self):
        mcp = MagicMock()
        captured: dict[str, object] = {}

        def tool_decorator(**kwargs):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        mcp.tool = tool_decorator
        resource_registry: dict[str, object] = {}

        def resource_decorator(**kwargs):
            def deco(fn):
                resource_registry[fn.__name__] = fn
                return fn
            return deco

        mcp.resource = resource_decorator
        register_doc_tools(mcp, TestRegisterDocTools._make_manager())
        assert "doc_resource" in resource_registry
        return resource_registry["doc_resource"]

    @pytest.mark.asyncio
    async def test_resource_returns_guide_content(self):
        fn = self._capture_resource()
        result = await fn(topic="test")
        assert "# Test" in result

    @pytest.mark.asyncio
    async def test_resource_raises_for_unknown(self):
        fn = self._capture_resource()
        from fastmcp.exceptions import ResourceError
        with pytest.raises(ResourceError):
            await fn(topic="unknown")
