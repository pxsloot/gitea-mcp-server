"""Tests for unified search tool across tools, docs, and resources."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.context import Context
from fastmcp.tools.base import Tool
from mcp.types import TextContent

from gitea_mcp_server.docs_tools import DocManager
from gitea_mcp_server.tools.search import TolerantSearchTransform
from gitea_mcp_server.unified_search import register_unified_search


def _make_resource(uri: str, name: str, description: str, mime_type: str = "text/plain", tags: set[str] | None = None) -> SimpleNamespace:
    """Create a simple resource-like object. Avoids MagicMock name conflict."""
    return SimpleNamespace(
        uri=uri,
        name=name,
        description=description,
        mime_type=mime_type,
        tags=tags or set(),
        meta=None,
    )


def _make_search_transform(catalog: list[Tool]) -> TolerantSearchTransform:
    """Create a TolerantSearchTransform with a known tool catalog."""
    transform = TolerantSearchTransform()
    transform.get_tool_catalog = AsyncMock(return_value=catalog)  # type: ignore[method-assign]
    return transform


def _make_tool(name: str, description: str = "", tags: list[str] | None = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        tags=set(tags or []),
        parameters={},
    )


def _setup_mcp() -> tuple[MagicMock, Any]:
    """Set up a mock MCP and capture the registered search function.

    Returns:
        Tuple of (mcp mock, registered search function)
    """
    mcp = MagicMock()
    decorator = MagicMock()
    mcp.tool = MagicMock(return_value=decorator)
    return mcp, decorator


class TestUnifiedSearchAnnotations:
    """Tests annotation metadata on the unified search tool."""

    def test_search_tool_has_description(self):
        """search tool must have a non-empty description."""
        from gitea_mcp_server.unified_search import register_unified_search

        doc_manager = MagicMock()
        search_transform = MagicMock()

        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        register_unified_search(mcp, doc_manager, search_transform)

        call_kwargs = mcp.tool.call_args[1]
        assert call_kwargs.get("name") == "search"
        desc = call_kwargs.get("description")
        assert desc, f"search.description should be non-empty, got: {desc!r}"

    def test_search_has_openworld_false(self):
        """search tool should have openWorldHint=False."""
        from gitea_mcp_server.unified_search import register_unified_search

        doc_manager = MagicMock()
        search_transform = MagicMock()

        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        register_unified_search(mcp, doc_manager, search_transform)

        call_kwargs = mcp.tool.call_args[1]
        assert call_kwargs.get("name") == "search"
        annotations = call_kwargs.get("annotations")
        assert annotations is not None
        assert annotations.openWorldHint is False


class TestUnifiedSearch:
    """Tests for the unified search tool."""

    @pytest.mark.asyncio
    async def test_returns_results_with_type_discriminator(self) -> None:
        """All results should include a 'type' field: tool, doc, or resource."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[
            _make_resource("gitea://version", "Version", "Gitea server version"),
        ])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = [
            {"name": "token-scopes", "title": "Token Scopes", "description": "How tokens work", "tags": ["auth"]},
        ]

        search_transform = _make_search_transform([
            _make_tool("gitea_issue_list_issues", "List issues in a repository", ["issue", "list"]),
            _make_tool("gitea_repo_list_pull_requests", "List pull requests", ["pr", "list"]),
        ])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="issue", format="raw", ctx=ctx)

        results = result.structured_content["result"]
        assert len(results) > 0

        for item in results:
            assert "type" in item, f"Missing type in: {item}"
            assert item["type"] in ("tool", "doc", "resource"), f"Invalid type: {item['type']}"

    @pytest.mark.asyncio
    async def test_tool_results_have_name_and_access_uri(self) -> None:
        """Tool results should have name and access_uri set to tool name."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = []

        search_transform = _make_search_transform([
            _make_tool("gitea_issue_list_issues", "List issues", ["issue"]),
        ])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="issue", format="raw", ctx=ctx)
        items = result.structured_content["result"]
        tool_items = [i for i in items if i["type"] == "tool"]

        assert len(tool_items) == 1
        assert tool_items[0]["name"] == "gitea_issue_list_issues"
        assert tool_items[0]["access_uri"] == "gitea_issue_list_issues"

    @pytest.mark.asyncio
    async def test_resource_results_have_uri(self) -> None:
        """Resource results should include the original URI."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[
            _make_resource("gitea://repos/owner/repo", "Repository", "Repo metadata", tags={"wrapper", "repository"}),
        ])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = []

        search_transform = _make_search_transform([])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="repo", format="raw", ctx=ctx)
        items = result.structured_content["result"]
        resource_items = [i for i in items if i["type"] == "resource"]

        assert len(resource_items) == 1
        assert resource_items[0]["uri"] == "gitea://repos/owner/repo"
        assert resource_items[0]["access_uri"] == "gitea://repos/owner/repo"

    @pytest.mark.asyncio
    async def test_doc_results_have_access_uri(self) -> None:
        """Doc results should have a docs resource URI as access_uri."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = [
            {"name": "token-scopes", "title": "Token Scopes", "description": "How tokens work", "tags": ["auth"]},
        ]

        search_transform = _make_search_transform([])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="token", format="raw", ctx=ctx)
        items = result.structured_content["result"]
        doc_items = [i for i in items if i["type"] == "doc"]

        assert len(doc_items) == 1
        assert doc_items[0]["access_uri"] == "gitea://docs/guide/token-scopes"

    @pytest.mark.asyncio
    async def test_format_markdown_returns_text_content(self) -> None:
        """format=markdown should return TextContent in content list."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[
            _make_resource("gitea://version", "Version", "Gitea version"),
        ])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = []

        search_transform = _make_search_transform([
            _make_tool("gitea_issue_list_issues", "List issues"),
        ])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="issue", format="markdown", ctx=ctx)

        assert len(result.content) > 0
        assert isinstance(result.content[0], TextContent)
        assert isinstance(result.content[0].text, str)

        assert "result" in result.structured_content

    @pytest.mark.asyncio
    async def test_format_json_returns_json_string(self) -> None:
        """format=json should return a JSON string in TextContent."""
        import json

        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = []

        search_transform = _make_search_transform([
            _make_tool("gitea_issue_list_issues", "List issues"),
        ])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="issue", format="json", ctx=ctx)

        assert len(result.content) > 0
        assert isinstance(result.content[0], TextContent)
        parsed = json.loads(result.content[0].text)
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_no_results_returns_empty_list(self) -> None:
        """When nothing matches, should return empty result list."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = []

        search_transform = _make_search_transform([
            _make_tool("gitea_issue_list_issues", "List issues"),
        ])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="xyznonexistent123", format="raw", ctx=ctx)
        items = result.structured_content["result"]
        assert items == []

    @pytest.mark.asyncio
    async def test_ctx_none_raises_value_error(self) -> None:
        """When ctx is None, should raise ValueError (lines 87-88)."""
        from gitea_mcp_server.unified_search import register_unified_search

        doc_manager = MagicMock(spec=DocManager)
        search_transform = _make_search_transform([])
        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        with pytest.raises(ValueError, match="Context is required"):
            await registered_fn(query="test", format="raw", ctx=None)

    @pytest.mark.asyncio
    async def test_all_texts_empty_returns_empty_result(self) -> None:
        """When tools/resources/docs all produce no text, return empty result (line 142)."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = []

        # Empty tool catalog means no tool entries → all_texts will be empty
        search_transform = _make_search_transform([])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="anything", format="raw", ctx=ctx)
        items = result.structured_content["result"]
        assert items == []

    @pytest.mark.asyncio
    async def test_pagination_metadata_present(self) -> None:
        """Unified search should include has_more/next_offset/total_count."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        doc_manager = MagicMock(spec=DocManager)
        doc_manager.search.return_value = []

        search_transform = _make_search_transform([
            _make_tool("gitea_issue_list_issues", "List issues", ["issue"]),
            _make_tool("gitea_issue_get_issue", "Get issue", ["issue"]),
        ])

        mcp, decorator = _setup_mcp()
        register_unified_search(mcp, doc_manager, search_transform)

        registered_fn = decorator.call_args[0][0]
        result = await registered_fn(query="issue", format="raw", ctx=ctx)

        assert result.structured_content is not None
        assert "has_more" in result.structured_content
        assert "next_offset" in result.structured_content
        assert "total_count" in result.structured_content
        assert result.structured_content["total_count"] >= 1


__all__ = []
