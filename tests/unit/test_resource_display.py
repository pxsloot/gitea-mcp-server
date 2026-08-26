"""Tests for tools/resource_display.py (format_resource_content).

Covers:
    - context_meta_keys display context forwarding
    - format_resource_content extra parameter passthrough
    - Format hint handling (issues, pulls, generic)
    - Resource handler meta extraction
    - Labels handler meta forwarding
"""

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP


class TestContextMetaKeysPipeline:
    """End-to-end tests for the context_meta_keys display context forwarding.

    Tests the full pipeline:
    1. make_api_resource with context_meta_keys=["type"] registers a handler
       that forwards matching query params into ResourceContent.meta
    2. _mcp_read_resource_impl extra extraction from ResourceContent.meta
    3. format_resource_content passes extra to domain formatters
    """

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        """GiteaClient that returns JSON data."""
        client = AsyncMock()
        client.config.token = "test-token"
        return client

    @pytest.fixture
    def issues_resource(self, mock_client: AsyncMock) -> Callable[..., Any] | None:
        """Register and return the issues resource handler with context_meta_keys."""
        from gitea_mcp_server.resources.factory import ResourceParamConfig, make_api_resource

        mcp = MagicMock(spec=FastMCP)
        registered: dict[str, Callable[..., Any]] = {}

        def resource_decorator(uri: str, **kwargs: Any) -> Callable:
            def deco(func: Callable) -> Callable:
                registered[uri] = func
                return func

            return deco

        mcp.resource = resource_decorator

        make_api_resource(
            mcp,
            mock_client,
            openapi_spec=None,
            uri="gitea://repos/{owner}/{repo}/issues{?state,type}",
            api_path="/repos/{owner}/{repo}/issues",
            method="GET",
            format_hint="issues",
            resource_type="issues",
            scope="read:repository",
            tags={"issues"},
            param_config=ResourceParamConfig(
                query_params=["state", "type"],
                query_param_validators={"state": ["open", "closed"], "type": ["issues", "pulls"]},
                context_meta_keys=["type"],
            ),
        )
        return registered.get("gitea://repos/{owner}/{repo}/issues{?state,type}")

    @pytest.mark.asyncio
    async def test_handler_meta_includes_forwarded_param(
        self, issues_resource: Callable[..., Any], mock_client: AsyncMock
    ) -> None:
        """Handler forwards context_meta_keys params (query or path) into ResourceContent.meta."""
        from fastmcp.resources import ResourceResult

        mock_client.request = AsyncMock(return_value=[])
        result = await issues_resource(owner="test", repo="test", type="pulls")

        assert isinstance(result, ResourceResult)
        assert result.contents
        meta = result.contents[0].meta
        assert meta is not None
        # format_hint should be present (response_schema absent because openapi_spec=None)
        assert meta.get("format_hint") == "issues"
        # The context key should be forwarded as well
        assert meta.get("type") == "pulls"

    @pytest.mark.asyncio
    async def test_handler_meta_omits_unmatched_context_param(
        self, issues_resource: Callable[..., Any], mock_client: AsyncMock
    ) -> None:
        """Handler does NOT forward params not listed in context_meta_keys."""
        from fastmcp.resources import ResourceResult

        mock_client.request = AsyncMock(return_value=[])
        result = await issues_resource(owner="test", repo="test", state="open")

        assert isinstance(result, ResourceResult)
        assert result.contents
        meta = result.contents[0].meta
        assert meta is not None
        # 'state' is not in context_meta_keys, so it should NOT be in meta
        assert "state" not in meta
        # But response_schema and format_hint should still be there
        assert "format_hint" in meta

    @pytest.mark.asyncio
    async def test_handler_meta_no_context_meta_keys(self, mock_client: AsyncMock) -> None:
        """Handler does NOT forward any extra meta when context_meta_keys is absent."""
        from fastmcp.resources import ResourceResult

        from gitea_mcp_server.resources.factory import ResourceParamConfig, make_api_resource

        mcp = MagicMock(spec=FastMCP)
        registered: dict[str, Callable[..., Any]] = {}

        def resource_decorator(uri: str, **kwargs: Any) -> Callable:
            def deco(func: Callable) -> Callable:
                registered[uri] = func
                return func

            return deco

        mcp.resource = resource_decorator

        # Register WITHOUT context_meta_keys
        make_api_resource(
            mcp,
            mock_client,
            openapi_spec=None,
            uri="gitea://repos/{owner}/{repo}/test",
            api_path="/repos/{owner}/{repo}/test",
            method="GET",
            format_hint="repository",
            scope="read:repository",
            tags={"test"},
            param_config=ResourceParamConfig(
                query_params=["state"],
            ),
        )
        handler = registered.get("gitea://repos/{owner}/{repo}/test")
        assert handler is not None

        mock_client.request = AsyncMock(return_value={})
        assert handler is not None
        result = await handler(owner="test", repo="test", state="open")

        assert isinstance(result, ResourceResult)
        assert result.contents
        meta = result.contents[0].meta
        assert meta is not None
        # Only standard keys (format_hint), no extra context.
        # response_schema is absent because openapi_spec=None.
        assert "state" not in meta
        assert meta.get("format_hint") == "repository"

    def test_format_resource_content_with_extra_pulls(self) -> None:
        """Display pipeline passes extra to formatter - produces 'Pull Requests' title."""
        from gitea_mcp_server.tools.resource_display import format_resource_content

        data = json.dumps([{"number": 1, "title": "Bug", "state": "open"}])
        result = format_resource_content(
            data,
            "markdown",
            format_hint="issues",
            extra={"type": "pulls"},
        )
        assert "Pull Requests - 1 items" in result

    def test_format_resource_content_with_extra_issues(self) -> None:
        """Display pipeline passes extra to formatter - produces 'Issues' title."""
        from gitea_mcp_server.tools.resource_display import format_resource_content

        data = json.dumps([{"number": 1, "title": "Bug", "state": "open"}])
        result = format_resource_content(
            data,
            "markdown",
            format_hint="issues",
            extra={"type": "issues"},
        )
        assert "Issues - 1 items" in result

    def test_format_resource_content_without_extra_fallback(self) -> None:
        """Display pipeline falls back to scanning when extra is absent."""
        from gitea_mcp_server.tools.resource_display import format_resource_content

        # Data has no pull_request field -> title is "Issues"
        data = json.dumps([{"number": 1, "title": "Bug", "state": "open"}])
        result = format_resource_content(
            data,
            "markdown",
            format_hint="issues",
        )
        assert "Issues - 1 items" in result

    def test_format_resource_content_without_format_hint(self) -> None:
        """Display pipeline ignores extra when no format_hint is provided."""
        from gitea_mcp_server.tools.resource_display import format_resource_content

        data = json.dumps({"key": "value"})
        result = format_resource_content(
            data,
            "markdown",
            extra={"type": "pulls"},
        )
        # Generic markdown uses capitalized "Key" as header
        assert "| Key | value |" in result

    @pytest.mark.asyncio
    async def test_labels_handler_meta_forwards_owner_repo(self, mock_client: AsyncMock) -> None:
        """Handler with context_meta_keys=["owner","repo"] forwards path params to meta."""
        from fastmcp.resources import ResourceResult

        from gitea_mcp_server.resources.factory import ResourceParamConfig, make_api_resource
        from gitea_mcp_server.tools.display import _format_labels_markdown

        mcp = MagicMock(spec=FastMCP)
        registered: dict[str, Callable[..., Any]] = {}

        def resource_decorator(uri: str, **kwargs: Any) -> Callable:
            def deco(func: Callable) -> Callable:
                registered[uri] = func
                return func

            return deco

        mcp.resource = resource_decorator

        make_api_resource(
            mcp,
            mock_client,
            openapi_spec=None,
            uri="gitea://repos/{owner}/{repo}/labels",
            api_path="/repos/{owner}/{repo}/labels",
            method="GET",
            format_hint="labels",
            scope="read:issue",
            tags={"labels"},
            error_message="Labels not found for repository '{owner}/{repo}'.",
            param_config=ResourceParamConfig(
                context_meta_keys=["owner", "repo"],
            ),
        )
        handler = registered.get("gitea://repos/{owner}/{repo}/labels")
        assert handler is not None

        # Simulate Gitea returning label data.
        labels = [
            {
                "id": 1,
                "name": "bug",
                "color": "ff0000",
                "description": "Bug reports",
                "exclusive": False,
            },
        ]
        mock_client.request = AsyncMock(return_value=labels)
        result = await handler(owner="acmecorp", repo="widgets")

        assert isinstance(result, ResourceResult)
        assert result.contents
        meta = result.contents[0].meta
        assert meta is not None

        # Path param values should be forwarded via context_meta_keys
        # into ResourceContent.meta for the display pipeline.
        assert meta.get("owner") == "acmecorp"
        assert meta.get("repo") == "widgets"

        # Verify the display pipeline can extract and use those values:
        # the formatter receives the extra dict (simulating what
        # _mcp_read_resource_impl extracts from meta), and renders
        # the owner/repo into the heading.
        formatted = _format_labels_markdown(
            labels,
            extra={"owner": meta["owner"], "repo": meta["repo"]},
        )
        assert "# Labels for acmecorp/widgets" in formatted
        assert "bug" in formatted

    def test_extract_extra_meta_known_and_extra(self) -> None:
        """_extract_extra_meta returns extra keys, stripping known pipeline keys."""
        from gitea_mcp_server.tools.mcp_tools import _extract_extra_meta

        meta = {
            "response_schema": {"type": "object"},
            "format_hint": "labels",
            "owner": "acme",
            "repo": "widgets",
        }
        extra = _extract_extra_meta(meta)
        assert extra == {"owner": "acme", "repo": "widgets"}

    def test_extract_extra_meta_known_only(self) -> None:
        """_extract_extra_meta returns None when only known keys are present."""
        from gitea_mcp_server.tools.mcp_tools import _extract_extra_meta

        meta_only_known = {"response_schema": {}, "format_hint": "repository"}
        extra = _extract_extra_meta(meta_only_known)
        assert extra is None

    def test_extract_extra_meta_empty(self) -> None:
        """_extract_extra_meta returns None for empty meta dict."""
        from gitea_mcp_server.tools.mcp_tools import _extract_extra_meta

        extra = _extract_extra_meta({})
        assert extra is None


class TestFormatResourceResult:
    """Tests for format_resource_result — the dual-channel display pipeline."""

    def test_json_format_dual_channel_mirror(self) -> None:
        """fmt=json on JSON content: content text mirrors structured_content."""
        from gitea_mcp_server.tools.resource_display import format_resource_result
        from tests.helpers.mcp_results import assert_dual_channel, parse_json_content

        result = format_resource_result('{"key": "val", "num": 42}', "json")
        assert_dual_channel(result, fmt="json")
        assert parse_json_content(result) == {"result": {"key": "val", "num": 42}}

    def test_raw_format_dual_channel(self) -> None:
        """fmt=raw: content is the raw string, structured carries it in the envelope."""
        from gitea_mcp_server.tools.resource_display import format_resource_result
        from tests.helpers.mcp_results import extract_text_content, get_structured

        result = format_resource_result('{"key": "val"}', "raw")
        assert extract_text_content(result.content) == '{"key": "val"}'
        assert get_structured(result) == {"result": '{"key": "val"}'}

    def test_markdown_structured_carries_parsed_data(self) -> None:
        """fmt=markdown: content is a rendering, structured carries the parsed data."""
        from gitea_mcp_server.tools.resource_display import format_resource_result
        from tests.helpers.mcp_results import extract_text_content, get_structured

        result = format_resource_result('{"key": "val"}', "markdown")
        rendered = extract_text_content(result.content)
        assert "Key" in rendered
        assert "val" in rendered
        assert get_structured(result) == {"result": {"key": "val"}}

    def test_non_json_json_format(self) -> None:
        """Non-JSON content with fmt=json: content and structured both carry raw."""
        from gitea_mcp_server.tools.resource_display import format_resource_result
        from tests.helpers.mcp_results import assert_dual_channel

        result = format_resource_result("plain text", "json")
        assert_dual_channel(result, fmt="json")

    def test_concise_collapses_structured_data(self) -> None:
        """detail=concise with schema: structured carries the collapsed data."""
        from gitea_mcp_server.tools.resource_display import format_resource_result
        from tests.helpers.mcp_results import get_structured

        schema = {
            "type": "object",
            "properties": {
                "owner": {"$ref": "#/components/schemas/User"},
                "name": {"type": "string"},
            },
        }
        raw = json.dumps({"owner": {"id": 1, "login": "alice"}, "name": "repo"})
        result = format_resource_result(raw, "json", detail="concise", schema=schema)
        structured = get_structured(result)
        assert structured["result"]["name"] == "repo"
        # Nested object collapsed to a $ref label at depth >= 1.
        assert structured["result"]["owner"] == "$ref:User"


class TestFormatResourceContentEmptyFallback:
    """Tests for format_resource_content empty-content fallback paths."""

    @pytest.mark.parametrize(
        "content_value",
        [
            None,
            [],
            [MagicMock()],
        ],
        ids=[
            "content=None",
            "content=[]",
            "content=no-TextContent",
        ],
    )
    def test_empty_fallback_returns_empty_string(self, content_value: Any) -> None:
        """When apply_format result.content is None, [], or non-TextContent, return ''."""
        from unittest.mock import MagicMock, patch

        from gitea_mcp_server.tools.resource_display import format_resource_content

        mock_result = MagicMock()
        mock_result.content = content_value

        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format", return_value=mock_result
        ):
            result = format_resource_content("{}", "markdown")
            assert result == ""
