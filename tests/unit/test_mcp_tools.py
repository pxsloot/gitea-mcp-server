"""Tests for MCP resource tools."""

import json as json_module
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.context import Context
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.tools.mcp_tools import (
    _make_resource_formatter,
    _maybe_decode_base64,
    _mcp_read_resource_impl,
    _read_resource_tool,
    mcp_list_resources_impl,
    register_mcp_resource_tools,
)
from gitea_mcp_server.tools.resource_display import clean_resource_uri
from gitea_mcp_server.tools.result_pipeline import ExecutionResult
from gitea_mcp_server.tools.result_pipeline import render as _pipeline_render
from tests.helpers.mcp_results import (
    assert_dual_channel,
    extract_text_content,
    get_structured,
    parse_json_content,
)


def _render(
    exec_result: Any, fmt: str = "markdown", page: int = 1, limit: int = 10, fetch_all: bool = False
) -> ToolResult:
    """Render an ExecutionResult through the single result pipeline."""
    return _pipeline_render(exec_result, fmt=fmt, page=page, limit=limit, fetch_all=fetch_all)


class TestCleanResourceUri:
    """Tests for clean_resource_uri."""

    def test_strips_query_param_suffix(self) -> None:
        """Should strip {?param} suffix from URI."""
        assert (
            clean_resource_uri("gitea://repos/{owner}/{repo}/issues{?state}")
            == "gitea://repos/{owner}/{repo}/issues"
        )

    def test_preserves_uri_without_query_params(self) -> None:
        """Should return URI unchanged if no query params suffix."""
        uri = "gitea://repos/{owner}/{repo}"
        assert clean_resource_uri(uri) == uri

    def test_preserves_concrete_uri(self) -> None:
        """Should return concrete URIs unchanged."""
        uri = "gitea://version"
        assert clean_resource_uri(uri) == uri

    def test_strips_multiple_query_params(self) -> None:
        """Should strip multi-param {?a,b} suffix."""
        assert clean_resource_uri("search://{query}{?page,limit}") == "search://{query}"

    def test_preserves_wildcard_path_params(self) -> None:
        """Should preserve {path*} and other non-query params."""
        assert (
            clean_resource_uri("gitea://repos/{owner}/{repo}/contents/{filepath*}")
            == "gitea://repos/{owner}/{repo}/contents/{filepath*}"
        )


class TestMcpListResourcesImpl:
    """Tests for mcp_list_resources_impl function."""

    @pytest.mark.asyncio
    async def test_returns_resources_and_count(self) -> None:
        """Should return dict with resources list and count from resource manager."""
        # Create mock Context with fastmcp.list_resources() and list_resource_templates()
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "Test Resource"
        resource_mock.description = "Test description"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()

        # Mock list_resources to return an async list with one resource
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await mcp_list_resources_impl(ctx)

        assert "resources" in result
        assert "count" in result
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_includes_resource_metadata(self) -> None:
        """Should include URI, name, description, mimeType."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://repo"
        resource_mock.name = "Repo Info"
        resource_mock.description = "Repository information"
        resource_mock.mime_type = "text/markdown"
        resource_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["uri"] == "gitea://repo"
        assert resource["name"] == "Repo Info"
        assert resource["description"] == "Repository information"
        assert resource["mimeType"] == "text/markdown"

    @pytest.mark.asyncio
    async def test_includes_templates(self) -> None:
        """Should include resource templates (parameterized URIs)."""
        ctx = MagicMock(spec=Context)
        template_mock = MagicMock()
        template_mock.uri_template = "gitea://repos/{owner}/{repo}"
        template_mock.name = "Repository"
        template_mock.description = "Repository metadata"
        template_mock.mime_type = "text/markdown"
        template_mock.tags = set()
        # fn not needed since name is provided

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[template_mock])

        result = await mcp_list_resources_impl(ctx)

        assert result["count"] == 1
        resource = result["resources"][0]
        assert resource["uri"] == "gitea://repos/{owner}/{repo}"
        assert resource["name"] == "Repository"
        assert resource["type"] == "template"

    @pytest.mark.asyncio
    async def test_includes_both_resources_and_templates(self) -> None:
        """Should include both concrete resources and templates."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://static"
        resource_mock.name = "Static Resource"
        resource_mock.description = "A concrete resource"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()

        template_mock = MagicMock()
        template_mock.uri_template = "gitea://dynamic/{id}"
        template_mock.name = "Dynamic Template"
        template_mock.description = "A parameterized template"
        template_mock.mime_type = "text/markdown"
        template_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[template_mock])

        result = await mcp_list_resources_impl(ctx)

        assert result["count"] == 2
        uris = [r["uri"] for r in result["resources"]]
        assert "gitea://static" in uris
        assert "gitea://dynamic/{id}" in uris
        # Check types
        types = {r["type"] for r in result["resources"]}
        assert "resource" in types
        assert "template" in types

    @pytest.mark.asyncio
    async def test_handles_empty_list(self) -> None:
        """Should handle empty resource list."""
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await mcp_list_resources_impl(ctx)

        assert result["resources"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_handles_missing_description(self) -> None:
        """Should handle resources with None description."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "Test"
        resource_mock.description = None
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["description"] == ""

    @pytest.mark.asyncio
    async def test_includes_required_scope_from_template_meta(self) -> None:
        """Should include required_scope from template meta."""
        ctx = MagicMock(spec=Context)
        template_mock = MagicMock()
        template_mock.uri_template = "gitea://repos/{owner}/{repo}"
        template_mock.name = "Repository"
        template_mock.description = "Repository metadata"
        template_mock.mime_type = "text/markdown"
        template_mock.tags = set()
        template_mock.meta = {"required_scope": "read:repository"}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[template_mock])

        result = await mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["required_scope"] == "read:repository"

    @pytest.mark.asyncio
    async def test_includes_required_scope_from_resource_meta(self) -> None:
        """Should include required_scope from concrete resource meta."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://version"
        resource_mock.name = "Version"
        resource_mock.description = "Server version"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()
        resource_mock.meta = {"required_scope": None}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["required_scope"] is None

    @pytest.mark.asyncio
    async def test_required_scope_is_none_when_no_meta(self) -> None:
        """Should return None for required_scope when meta is absent."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "Test"
        resource_mock.description = "Test"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()
        resource_mock.meta = None

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["required_scope"] is None

    @pytest.mark.asyncio
    async def test_handles_missing_name_and_mime_type(self) -> None:
        """Should fall back to function name and default mime type."""
        ctx = MagicMock(spec=Context)
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://test"
        resource_mock.name = "my_resource_func"
        resource_mock.description = "Test resource"
        resource_mock.mime_type = None
        resource_mock.tags = set()

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[resource_mock])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = await mcp_list_resources_impl(ctx)

        resource = result["resources"][0]
        assert resource["name"] == "my_resource_func"
        assert resource["mimeType"] == "text/plain"


class TestMcpReadResourceImpl:
    """Tests for _mcp_read_resource_impl function."""

    @pytest.mark.asyncio
    async def test_reads_resource_success(self) -> None:
        """Should read resource content via ctx.read_resource."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        # ctx.read_resource returns a ResourceResult (FastMCP 3.x)
        content_part = ResourceContent("Hello World")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        text, schema, hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert text == "Hello World"
        assert schema is None
        assert hint is None
        assert extra is None
        ctx.read_resource.assert_awaited_once_with("gitea://test")

    @pytest.mark.asyncio
    async def test_extracts_meta_from_content(self) -> None:
        """Should extract schema, format_hint, and extra from content meta."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent(
            '{"key": "val"}',
            meta={
                "response_schema": {"type": "object"},
                "format_hint": "repository",
                "custom_key": "custom_val",
            },
        )
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert raw == '{"key": "val"}'
        assert schema == {"type": "object"}
        assert format_hint == "repository"
        assert extra == {"custom_key": "custom_val"}

    @pytest.mark.asyncio
    async def test_handles_missing_meta_gracefully(self) -> None:
        """Should return None for all meta fields when no meta is present."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent("plain text")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert raw == "plain text"
        assert schema is None
        assert format_hint is None
        assert extra is None

    @pytest.mark.asyncio
    async def test_extracts_meta_known_only_returns_none_extra(self) -> None:
        """Should return None extra when meta has only known pipeline keys."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent(
            "data",
            meta={
                "response_schema": {"type": "object"},
                "format_hint": "repository",
            },
        )
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert raw == "data"
        assert schema == {"type": "object"}
        assert format_hint == "repository"
        assert extra is None

    @pytest.mark.asyncio
    async def test_extracts_meta_unknown_only_returns_all_as_extra(self) -> None:
        """Should treat all meta keys as extra when no known keys present."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent(
            "data",
            meta={"owner": "acme", "repo": "widgets"},
        )
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert raw == "data"
        assert schema is None
        assert format_hint is None
        assert extra == {"owner": "acme", "repo": "widgets"}

    @pytest.mark.asyncio
    async def test_extracts_meta_partial_known_keys(self) -> None:
        """Should handle meta with only response_schema (no format_hint)."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent(
            "data",
            meta={"response_schema": {"type": "object"}},
        )
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert raw == "data"
        assert schema == {"type": "object"}
        assert format_hint is None
        assert extra is None

    @pytest.mark.asyncio
    async def test_raises_for_missing_resource(self) -> None:
        """Should raise ValueError for non-existent resource."""
        from fastmcp.resources import ResourceResult

        ctx = MagicMock(spec=Context)
        empty_result = ResourceResult(contents=[])
        ctx.read_resource = AsyncMock(return_value=empty_result)

        with pytest.raises(ValueError, match="returned no content"):
            await _mcp_read_resource_impl(ctx, "gitea://nonexistent")

    @pytest.mark.asyncio
    async def test_raises_on_exception(self) -> None:
        """Should wrap any exception in ValueError."""
        ctx = MagicMock(spec=Context)
        ctx.read_resource = AsyncMock(side_effect=RuntimeError("Connection failed"))

        with pytest.raises(ValueError, match="Error reading resource"):
            await _mcp_read_resource_impl(ctx, "gitea://test")

    @pytest.mark.asyncio
    async def test_handles_bytes_content(self) -> None:
        """Should decode bytes content to string."""
        from fastmcp.resources import ResourceContent, ResourceResult

        ctx = MagicMock(spec=Context)
        content_part = ResourceContent(b"Hello Bytes")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        text, schema, hint, extra = await _mcp_read_resource_impl(ctx, "gitea://test")

        assert text == "Hello Bytes"
        assert schema is None
        assert hint is None
        assert extra is None
        ctx.read_resource.assert_awaited_once_with("gitea://test")


class TestRegisterMcpResourceTools:
    """Tests for register_mcp_resource_tools function."""

    def test_registers_two_tools(self) -> None:
        """Should register exactly two tools (list_resources, read_resource)."""
        mcp = MagicMock()
        mcp.tool = MagicMock()

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 2

    def test_tool_decorators_applied(self) -> None:
        """Should apply @mcp.tool() decorator to all functions."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        assert mcp.tool.call_count == 2

    def test_list_resources_has_openworld_false(self) -> None:
        """list_resources should have openWorldHint=False."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        call_kwargs = mcp.tool.call_args_list[0][1]
        assert call_kwargs.get("name") == "list_resources"
        annotations = call_kwargs.get("annotations")
        assert annotations is not None
        assert annotations.openWorldHint is False

    def test_read_resource_has_openworld_true(self) -> None:
        """read_resource should have openWorldHint=True (fetches from Gitea API)."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        call_kwargs = mcp.tool.call_args_list[1][1]
        assert call_kwargs.get("name") == "read_resource"
        annotations = call_kwargs.get("annotations")
        assert annotations is not None
        assert annotations.openWorldHint is True

    def test_tool_schema_resource_has_meta_and_tags(self) -> None:
        """gitea://tool/{name}/schema carries ResourceMeta and a small tag set."""
        mcp = MagicMock()
        mcp.tool = MagicMock(return_value=lambda f: f)
        mcp.resource = MagicMock(return_value=lambda f: f)

        register_mcp_resource_tools(mcp)

        call = mcp.resource.call_args
        kwargs = call[1]
        assert kwargs.get("uri") == "gitea://tool/{name}/schema"
        assert kwargs.get("name") == "tool_schema"
        assert kwargs.get("tags") == {"synthetic", "tool-schema", "schema"}
        meta = kwargs.get("meta")
        assert meta is not None, "tool schema resource should carry ResourceMeta"
        assert meta.get("size_hint") == "large"
        assert meta.get("default_detail") == "concise"


class TestMcpReadResourceTool:
    """Tests for the read_resource executor.

    The executor returns raw data (``ExecutionResult``): the resource content
    parsed and shape-classified, with its per-resource schema and resolved
    markdown formatter.  The single result pipeline renders it — these tests
    assert both the executor's raw output and the rendered agent-facing
    surface (raw envelope, json envelope, markdown).
    """

    def _capture_read_resource(self) -> Callable[..., Any]:
        """Register resource tools and return the read_resource function."""
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, Callable[..., Any]] = {}

        def tool_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return deco

        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        fn = captured["read_resource"]
        assert fn is not None
        return fn

    @pytest.mark.asyncio
    async def test_non_json_returns_text_shape(self) -> None:
        """Non-JSON (markdown/text) content: text shape with the raw string."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent("# Hello\n\nThis is **markdown**")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)

        assert isinstance(exec_result, ExecutionResult)
        assert exec_result.data == "# Hello\n\nThis is **markdown**"
        assert exec_result.shape == "text"

    @pytest.mark.asyncio
    async def test_json_dict_returns_object_shape(self) -> None:
        """JSON dict content: object shape with parsed data."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('{"key": "val", "num": 42}')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)

        assert isinstance(exec_result, ExecutionResult)
        assert exec_result.data == {"key": "val", "num": 42}
        assert exec_result.shape == "object"

    @pytest.mark.asyncio
    async def test_json_list_uses_unpaginated_object_shape(self) -> None:
        """JSON list content: object (unpaginated) shape — resources never paginate."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('[{"id": 1}, {"id": 2}]')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)

        assert isinstance(exec_result, ExecutionResult)
        assert exec_result.data == [{"id": 1}, {"id": 2}]
        assert exec_result.shape == "object"

    @pytest.mark.asyncio
    async def test_attaches_schema_and_formatter_from_meta(self) -> None:
        """Content meta (response_schema, format_hint) becomes executor metadata."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        content_part = ResourceContent(
            '{"name": "test"}',
            meta={"response_schema": schema, "format_hint": "repository"},
        )
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)

        assert exec_result.schema == schema
        assert exec_result.markdown_formatter is not None

    @pytest.mark.asyncio
    async def test_renders_markdown_through_pipeline(self) -> None:
        """Rendered markdown: the pipeline renders the parsed data."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('{"key": "val", "num": 42}')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)
        tool_result = _render(exec_result, fmt="markdown")

        assert isinstance(tool_result, ToolResult)
        rendered = extract_text_content(tool_result.content)
        assert "|" in rendered
        assert "Key" in rendered
        assert "val" in rendered

    @pytest.mark.asyncio
    async def test_raw_format_returns_envelope(self) -> None:
        """format=raw renders the envelope — unified with the tool pipeline."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('{"key": "val"}')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)
        tool_result = _render(exec_result, fmt="raw")

        assert_dual_channel(tool_result, fmt="raw")
        parsed = parse_json_content(tool_result)
        assert parsed == {"result": {"key": "val"}}

    @pytest.mark.asyncio
    async def test_json_format_dual_channel_mirror(self) -> None:
        """format=json: content text mirrors structured_content."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('{"key": "val", "num": 42}')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)
        tool_result = _render(exec_result, fmt="json")

        assert_dual_channel(tool_result, fmt="json")
        parsed = parse_json_content(tool_result)
        assert parsed == {"result": {"key": "val", "num": 42}}

    @pytest.mark.asyncio
    async def test_non_json_with_json_format(self) -> None:
        """Non-JSON content with format=json wraps in {\"result\": ...}."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent("plain text")
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)
        tool_result = _render(exec_result, fmt="json")

        parsed = parse_json_content(tool_result)
        assert parsed == {"result": "plain text"}


class TestReadResourceRawUnification:
    """resource format=raw returns the same envelope shape as a tool.

    Before the unification, the resource pipeline short-circuited raw to a
    bare string while the tool pipeline produced ``{"result": ...}``.  Now
    both run through the single result pipeline, so the raw shape is
    identical — deterministic JSON text mirroring structured_content.
    """

    @pytest.mark.asyncio
    async def test_resource_raw_matches_tool_raw_envelope(self) -> None:
        """A resource raw result has the same envelope shape as a tool raw result."""
        from fastmcp.resources import ResourceContent, ResourceResult

        fn = self._capture_read_resource()
        ctx = MagicMock(spec=Context)
        content_part = ResourceContent('{"key": "val"}')
        result = ResourceResult(contents=[content_part])
        ctx.read_resource = AsyncMock(return_value=result)

        exec_result = await fn(uri="gitea://test", ctx=ctx)
        tool_result = _render(exec_result, fmt="raw")

        # Resource raw: {"result": <data>} — same shape as a tool's raw.
        assert_dual_channel(tool_result, fmt="raw")
        parsed = parse_json_content(tool_result)
        assert parsed == {"result": {"key": "val"}}

    def _capture_read_resource(self) -> Callable[..., Any]:
        """Register resource tools and return the read_resource function."""
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, Callable[..., Any]] = {}

        def tool_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return deco

        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        fn = captured["read_resource"]
        assert fn is not None
        return fn


class TestMakeResourceFormatter:
    """Tests for _make_resource_formatter (executor-side formatter resolution)."""

    def test_none_format_hint_returns_none(self) -> None:
        """format_hint=None returns None."""
        fn = _make_resource_formatter(None, None)
        assert fn is None

    def test_unknown_format_hint_returns_none(self) -> None:
        """Unknown format_hint name returns None."""
        fn = _make_resource_formatter("nonexistent_formatter", None)
        assert fn is None

    def test_known_format_hint_returns_callable(self) -> None:
        """Known format_hint returns a callable matching the pipeline contract."""
        fn = _make_resource_formatter("repository", None)
        assert callable(fn)
        result = fn({"name": "test-repo", "full_name": "org/test-repo"})
        assert "test-repo" in result

    def test_formatter_with_extra_passes_it_through(self) -> None:
        """Formatter registered with need_extra=True receives extra dict."""
        fn = _make_resource_formatter("labels", {"owner": "myorg", "repo": "myrepo"})
        assert callable(fn)
        result = fn([{"id": 1, "name": "bug"}])
        assert "myorg/myrepo" in result

    def test_formatter_receives_detail_from_pipeline(self) -> None:
        """The returned callable accepts detail and passes it to the formatter."""
        fn = _make_resource_formatter("repository", None)
        assert callable(fn)
        result = fn({"name": "test-repo", "full_name": "org/test-repo"}, detail="concise")
        assert "test-repo" in result


class TestMcpListResourcesFormat:
    """Tests that list_resources respects the format parameter.

    Uses a mock FastMCP to capture the tool function, then calls it
    directly with each format to verify structured_content and content.
    """

    @pytest.fixture
    def _mock_resource(self) -> MagicMock:
        """Create a clean mock resource that won't produce MagicMock objects in the output."""
        resource_mock = MagicMock()
        resource_mock.uri = "gitea://version"
        resource_mock.name = "Version"
        resource_mock.description = "Server version"
        resource_mock.mime_type = "text/plain"
        resource_mock.tags = set()
        resource_mock.meta = None  # prevent MagicMock leakage into required_scope
        return resource_mock

    def _capture_tool(self, name: str) -> Callable[..., Any]:
        """Register resource tools and return the named function."""
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, Callable[..., Any]] = {}

        def tool_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return deco

        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        fn = captured[name]
        assert fn is not None
        return fn

    @pytest.mark.asyncio
    async def test_raw_format(self, _mock_resource: MagicMock) -> None:
        """format=raw should return ToolResult with structured_content and no content."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[_mock_resource])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await fn(ctx=ctx), fmt="raw")

        assert isinstance(result, ToolResult)
        assert get_structured(result)["total_count"] == 1
        assert get_structured(result)["result"][0]["uri"] == "gitea://version"

    @pytest.mark.asyncio
    async def test_json_format(self, _mock_resource: MagicMock) -> None:
        """format=json should produce the envelope dict in content."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[_mock_resource])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await fn(ctx=ctx), fmt="json")

        assert isinstance(result, ToolResult)
        assert get_structured(result)["total_count"] == 1
        assert len(result.content) == 1
        parsed = parse_json_content(result)
        assert isinstance(parsed["result"], list)
        assert parsed["result"][0]["uri"] == "gitea://version"

    @pytest.mark.asyncio
    async def test_markdown_format(self, _mock_resource: MagicMock) -> None:
        """format=markdown should produce markdown text in content."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(return_value=[_mock_resource])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await fn(ctx=ctx))

        assert isinstance(result, ToolResult)
        assert get_structured(result)["total_count"] == 1
        assert len(result.content) == 1
        content_text = extract_text_content(result.content)
        assert "|" in content_text
        assert "version" in content_text.lower()


class TestMcpListResourcesTagTypeFilter:
    """Tests for tag and type filtering in list_resources tool."""

    def _capture_tool(self, name: str) -> Callable[..., Any]:
        """Register resource tools and return the named function."""
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, Callable[..., Any]] = {}

        def tool_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return deco

        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        fn = captured[name]
        assert fn is not None
        return fn

    @pytest.mark.asyncio
    async def test_tag_filter(self) -> None:
        """list_resources with tag filter returns only matching resources."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()

        r1 = MagicMock()
        r1.uri = "gitea://repos/owner/repo"
        r1.name = "Repo"
        r1.description = "Repository"
        r1.mime_type = "text/markdown"
        r1.tags = {"wrapper", "repository"}
        r1.meta = None

        r2 = MagicMock()
        r2.uri = "gitea://users/user"
        r2.name = "User"
        r2.description = "User"
        r2.mime_type = "text/markdown"
        r2.tags = {"wrapper", "user"}
        r2.meta = None

        ctx.fastmcp.list_resources = AsyncMock(return_value=[r1, r2])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await fn(ctx=ctx, tag="user"))

        assert result.structured_content is not None
        assert get_structured(result)["total_count"] == 1
        assert get_structured(result)["result"][0]["uri"] == "gitea://users/user"

    @pytest.mark.asyncio
    async def test_type_filter(self) -> None:
        """list_resources with type filter returns only matching type."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()

        tpl = MagicMock()
        tpl.uri_template = "gitea://repos/{owner}/{repo}"
        tpl.name = "Repo"
        tpl.description = "Repo template"
        tpl.mime_type = "text/markdown"
        tpl.tags = {"wrapper"}
        tpl.meta = None

        res = MagicMock()
        res.uri = "gitea://version"
        res.name = "Version"
        res.description = "Version"
        res.mime_type = "text/plain"
        res.tags = {"server"}
        res.meta = None

        ctx.fastmcp.list_resources = AsyncMock(return_value=[res])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[tpl])

        result = _render(await fn(ctx=ctx, type="template"))

        assert get_structured(result)["total_count"] == 1
        assert get_structured(result)["result"][0]["type"] == "template"

    @pytest.mark.asyncio
    async def test_type_and_tag_filter_combined(self) -> None:
        """list_resources with both tag and type filter."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()

        r = MagicMock()
        r.uri = "gitea://version"
        r.name = "Version"
        r.description = "Version"
        r.mime_type = "text/plain"
        r.tags = {"wrapper", "server"}
        r.meta = None

        tpl = MagicMock()
        tpl.uri_template = "gitea://repos/{owner}/{repo}"
        tpl.name = "Repo"
        tpl.description = "Repo"
        tpl.mime_type = "text/markdown"
        tpl.tags = {"wrapper", "repository"}
        tpl.meta = None

        ctx.fastmcp.list_resources = AsyncMock(return_value=[r])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[tpl])

        result = _render(await fn(ctx=ctx, tag="wrapper", type="resource"))

        assert get_structured(result)["total_count"] == 1
        assert get_structured(result)["result"][0]["uri"] == "gitea://version"

    @pytest.mark.asyncio
    async def test_tag_filter_no_match_returns_empty(self) -> None:
        """list_resources with a tag that matches nothing returns helpful message."""
        fn = self._capture_tool("list_resources")
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()

        # No resources have the tag 'nonexistent'
        r = MagicMock()
        r.uri = "gitea://version"
        r.name = "Version"
        r.description = "Version"
        r.mime_type = "text/plain"
        r.tags = {"server"}
        r.meta = None

        ctx.fastmcp.list_resources = AsyncMock(return_value=[r])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await fn(ctx=ctx, tag="nonexistent"))

        assert result.structured_content is not None
        sc = get_structured(result)
        assert sc["total_count"] == 0
        assert sc["result"] == []
        # Empty results still carry the full pagination envelope (issue #694).
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        # Text content should indicate no resources found
        assert result.content is not None
        text = extract_text_content(result.content)
        assert "No resources found" in text


class TestExtractResourceContent:
    """Tests for extract_resource_content helper."""

    def test_none_contents_raises(self) -> None:
        """None contents list raises LookupError."""
        from gitea_mcp_server.tools.resource_display import extract_resource_content

        with pytest.raises(LookupError, match="returned no content"):
            extract_resource_content(None, "gitea://test")

    def test_empty_contents_raises(self) -> None:
        """Empty contents list raises LookupError."""
        from gitea_mcp_server.tools.resource_display import extract_resource_content

        with pytest.raises(LookupError, match="returned no content"):
            extract_resource_content([], "gitea://test/resource")

    def test_bytes_content_decoded(self) -> None:
        """Bytes content is decoded from utf-8."""
        from gitea_mcp_server.tools.resource_display import extract_resource_content

        content_obj = type("Obj", (), {"content": b"hello bytes"})()
        result = extract_resource_content([content_obj], "gitea://test")
        assert result == "hello bytes"

    def test_str_content_returned_as_is(self) -> None:
        """String content is returned unchanged."""
        from gitea_mcp_server.tools.resource_display import extract_resource_content

        content_obj = type("Obj", (), {"content": "hello string"})()
        result = extract_resource_content([content_obj], "gitea://test")
        assert result == "hello string"

    def test_non_bytes_non_str_content(self) -> None:
        """Non-bytes, non-string content is converted via str()."""
        from gitea_mcp_server.tools.resource_display import extract_resource_content

        class CustomContent:
            def __str__(self) -> str:
                return "custom content"

        result = extract_resource_content(
            [type("Obj", (), {"content": CustomContent()})()], "gitea://test"
        )
        assert result == "custom content"

    @pytest.mark.asyncio
    async def test_list_resources_impl_exception_handled(self) -> None:
        """Exception in mcp_list_resources_impl returns empty result."""
        from gitea_mcp_server.tools.mcp_tools import mcp_list_resources_impl

        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.list_resources = AsyncMock(side_effect=AttributeError("no attribute"))
        ctx.fastmcp.list_resource_templates = AsyncMock(side_effect=AttributeError("no attribute"))

        result = await mcp_list_resources_impl(ctx)
        assert result == {"resources": [], "count": 0}


class TestToolSchemaResource:
    """Tests for _tool_schema_resource."""

    def _capture_resource_fn(self) -> Callable[..., Any]:
        mcp = MagicMock()
        captured: dict[str, Callable[..., Any]] = {}
        resource_registry: dict[str, Callable[..., Any]] = {}

        def tool_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                captured[fn.__name__] = fn
                return fn

            return deco

        def resource_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                resource_registry[fn.__name__] = fn
                return fn

            return deco

        mcp.tool = tool_decorator
        mcp.resource = resource_decorator
        register_mcp_resource_tools(mcp)
        assert "_tool_schema_resource" in resource_registry
        return resource_registry["_tool_schema_resource"]

    @pytest.mark.asyncio
    async def test_returns_full_tool_schema(self) -> None:
        """tool/{name}/schema returns full tool schema with params, output, tags."""
        fn = self._capture_resource_fn()
        ctx = MagicMock(spec=Context)
        tool = MagicMock()
        tool.name = "gitea_issue_list"
        tool.description = "List issues"
        tool.parameters = {"properties": {"owner": {"type": "string"}}, "required": ["owner"]}
        tool.output_schema = {
            "type": "object",
            "properties": {
                "result": {"type": "array", "items": {"type": "object"}},
            },
        }
        tool.tags = {"issue"}
        tool.version = "1.0"
        tool.annotations = None
        tool.meta = {}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=tool)

        import json

        result = await fn(name="gitea_issue_list", ctx=ctx)
        data = json.loads(result)
        assert data["name"] == "gitea_issue_list"
        assert data["description"] == "List issues"
        assert "parameters" in data
        assert "output_example" in data
        assert "tags" in data
        assert data["tags"] == ["issue"]
        assert data["version"] == "1.0"

    @pytest.mark.asyncio
    async def test_raises_for_missing_tool(self) -> None:
        """tool/{name}/schema raises ValueError for unknown tool."""
        fn = self._capture_resource_fn()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Tool 'unknown_tool' not found"):
            await fn(name="unknown_tool", ctx=ctx)

    @pytest.mark.asyncio
    async def test_handles_missing_output_schema(self) -> None:
        """tool/{name}/schema handles None output_schema gracefully."""
        fn = self._capture_resource_fn()
        ctx = MagicMock(spec=Context)
        tool = MagicMock()
        tool.name = "text_tool"
        tool.description = "Text tool"
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.tags = None
        tool.version = None
        tool.annotations = None
        tool.meta = {}

        ctx.fastmcp = MagicMock()
        ctx.fastmcp.get_tool = AsyncMock(return_value=tool)

        import json

        result = await fn(name="text_tool", ctx=ctx)
        data = json.loads(result)
        assert data["name"] == "text_tool"
        assert "output_example" not in data
        assert "tags" not in data
        assert "version" not in data


class TestMcpListResourcesRawFormat:
    """Tests for list_resources raw format output."""

    def _capture_tool(self) -> Callable[..., Any]:
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, Callable[..., Any]] = {}

        def tool_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return deco

        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        return captured["list_resources"]

    @pytest.mark.asyncio
    async def test_raw_format_has_structured_content(self) -> None:
        """format=raw carries the data in structured_content.

        The text mirrors the envelope (see test_raw_format_dual_channel —
        the single result pipeline puts the envelope in the text).
        """
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        r = MagicMock()
        r.uri = "gitea://version"
        r.name = "Version"
        r.description = "Server version"
        r.mime_type = "text/plain"
        r.tags = set()
        r.meta = None
        ctx.fastmcp.list_resources = AsyncMock(return_value=[r])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await fn(ctx=ctx), fmt="raw")
        assert result.structured_content is not None
        assert get_structured(result)["total_count"] == 1
        assert get_structured(result)["result"][0]["uri"] == "gitea://version"

    @pytest.mark.asyncio
    async def test_raw_format_dual_channel(self) -> None:
        """list_resources format=raw must carry the envelope in the text.

        The single result pipeline renders the text as the serialized
        envelope dict, so ``content`` mirrors ``structured_content`` —
        including the pagination keys (``has_more``/``next_offset``/
        ``total_count``) beside ``result``.  ``format=raw`` is deterministic
        JSON text, not a Python repr and not a pre-envelope shape.
        """
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        r = MagicMock()
        r.uri = "gitea://version"
        r.name = "Version"
        r.description = "Server version"
        r.mime_type = "text/plain"
        r.tags = set()
        r.meta = None
        ctx.fastmcp.list_resources = AsyncMock(return_value=[r])
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(await fn(ctx=ctx), fmt="raw")
        assert_dual_channel(result, fmt="raw")


class TestMcpListResourcesFetchAll:
    """Regression tests for fetch_all parameter in list_resources."""

    def _capture_tool(self) -> Callable[..., Any]:
        mcp = MagicMock()
        mcp.resource = MagicMock(return_value=lambda f: f)
        captured: dict[str, Callable[..., Any]] = {}

        def tool_decorator(**kwargs: Any) -> Callable:
            def deco(fn: Callable) -> Callable:
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return deco

        mcp.tool = tool_decorator
        register_mcp_resource_tools(mcp)
        return captured["list_resources"]

    def _make_resource(self, idx: int) -> MagicMock:
        r = MagicMock()
        r.uri = f"gitea://resource/{idx}"
        r.name = f"Resource {idx}"
        r.description = f"Resource number {idx}"
        r.mime_type = "text/plain"
        r.tags = set()
        r.meta = None
        return r

    @pytest.mark.asyncio
    async def test_fetch_all_with_non_default_page(self) -> None:
        """When fetch_all=True, has_more must be False regardless of page arg.

        Regression: if page/limit are not normalized when fetch_all=True,
        add_pagination_metadata computes has_more = page * limit < total_count,
        which is wrong — all items are already in the result.
        """
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        resources = [self._make_resource(i) for i in range(25)]
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        # fetch_all=True with page=3 — a stale page value that would produce
        # incorrect has_more=True if page/limit weren't normalized.
        result = _render(
            await fn(ctx=ctx, page=3, limit=5, fetch_all=True),
            fmt="raw",
            page=3,
            limit=5,
            fetch_all=True,
        )
        sc = result.structured_content
        assert sc is not None
        assert len(sc["result"]) == 25
        assert sc["has_more"] is False, (
            "has_more should be False when fetch_all=True — all items already returned"
        )
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    @pytest.mark.asyncio
    async def test_fetch_all_with_page_1(self) -> None:
        """fetch_all + page=1 returns all items with correct metadata."""
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        resources = [self._make_resource(i) for i in range(7)]
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(
            await fn(ctx=ctx, page=1, limit=3, fetch_all=True),
            fmt="raw",
            page=1,
            limit=3,
            fetch_all=True,
        )
        sc = get_structured(result)
        assert len(sc["result"]) == 7
        assert sc["has_more"] is False
        assert sc["total_count"] == 7

    @pytest.mark.asyncio
    async def test_page_out_of_range_emits_empty_envelope(self) -> None:
        """An out-of-range page emits the empty envelope with a message."""
        fn = self._capture_tool()
        ctx = MagicMock(spec=Context)
        ctx.fastmcp = MagicMock()
        resources = [self._make_resource(i) for i in range(5)]
        ctx.fastmcp.list_resources = AsyncMock(return_value=resources)
        ctx.fastmcp.list_resource_templates = AsyncMock(return_value=[])

        result = _render(
            await fn(ctx=ctx, page=10, limit=10),
            fmt="raw",
            page=10,
            limit=10,
        )
        sc = get_structured(result)
        assert sc["result"] == []
        assert "Page 10 is out of range" in sc["message"]
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 5


# ---------------------------------------------------------------------------
# Tests: _maybe_decode_base64
# ---------------------------------------------------------------------------


class TestMaybeDecodeBase64:
    """Tests for ``_maybe_decode_base64`` in ``mcp_tools.py``.

    Verifies runtime detection and decoding of Gitea ContentsResponse in
    resource content before the display pipeline processes it.
    """

    @pytest.mark.asyncio
    async def test_decodes_base64_content(self) -> None:
        """A valid base64-encoded ContentsResponse is decoded to plain text."""
        import base64

        plaintext = "file content here\nline two"
        encoded = base64.b64encode(plaintext.encode()).decode()
        raw = json_module.dumps({"content": encoded, "encoding": "base64"})
        result = await _maybe_decode_base64(raw)
        assert result == plaintext

    @pytest.mark.asyncio
    async def test_passes_through_plain_text(self) -> None:
        """Non-JSON content passes through unchanged."""
        raw = "plain text response"
        result = await _maybe_decode_base64(raw)
        assert result == "plain text response"

    @pytest.mark.asyncio
    async def test_passes_through_json_without_base64_encoding(self) -> None:
        """JSON dict without encoding field passes through unchanged."""
        raw = json_module.dumps({"name": "file.txt", "type": "file"})
        result = await _maybe_decode_base64(raw)
        assert result == raw

    @pytest.mark.asyncio
    async def test_decodes_with_other_fields(self) -> None:
        """A ContentsResponse with extra metadata fields still decodes."""
        import base64

        plaintext = "content with metadata"
        encoded = base64.b64encode(plaintext.encode()).decode()
        raw = json_module.dumps(
            {"content": encoded, "encoding": "base64", "name": "f.py", "size": 42}
        )
        result = await _maybe_decode_base64(raw)
        assert result == plaintext

    @pytest.mark.asyncio
    async def test_passes_through_non_dict_json(self) -> None:
        """JSON array content passes through unchanged."""
        raw = json_module.dumps(["item1", "item2"])
        result = await _maybe_decode_base64(raw)
        assert result == raw

    @pytest.mark.asyncio
    async def test_passes_through_invalid_json(self) -> None:
        """Invalid JSON passes through unchanged."""
        raw = "not valid { json"
        result = await _maybe_decode_base64(raw)
        assert result == raw

    @pytest.mark.asyncio
    async def test_handles_empty_base64_content(self) -> None:
        """Empty content field with base64 encoding returns empty string."""
        raw = json_module.dumps({"content": "", "encoding": "base64"})
        result = await _maybe_decode_base64(raw)
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: _read_resource_tool — format=raw preserves base64 content
# ---------------------------------------------------------------------------


class TestReadResourceToolBase64Decode:
    """The executor always decodes base64 ContentsResponse (like autogen).

    ``format=raw`` no longer preserves the base64 JSON — the executor
    produces the data (decoded text), and raw renders the envelope.
    """

    @pytest.mark.asyncio
    async def test_always_decodes_base64_content(self) -> None:
        """Base64 ContentsResponse is decoded in the executor, for every format."""
        import base64

        plaintext = "Hello World"
        encoded = base64.b64encode(plaintext.encode()).decode()
        raw_json = json_module.dumps({"content": encoded, "encoding": "base64"})

        with (
            patch(
                "gitea_mcp_server.tools.mcp_tools._mcp_read_resource_impl",
                new_callable=AsyncMock,
                return_value=(raw_json, None, None, None),
            ),
            patch(
                "gitea_mcp_server.tools.mcp_tools._maybe_decode_base64",
                wraps=_maybe_decode_base64,
            ) as mock_decode,
        ):
            result = await _read_resource_tool(uri="gitea://repos/org/repo/contents/file.py")
            mock_decode.assert_called_once()
            assert isinstance(result, ExecutionResult)
            assert result.data == plaintext
            assert result.shape == "text"
