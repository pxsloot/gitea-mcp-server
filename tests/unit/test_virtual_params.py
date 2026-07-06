"""Unit tests for gitea_mcp_server/tools/virtual_params.py.

Tests cover the three lifecycle functions (inject_into, extract_from, apply_to)
and integration with _ToolWrappingTransform._wrap().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.tools.virtual_params import (
    VirtualParam,
    apply_to,
    extract_from,
    inject_into,
)

# A minimal VirtualParam entry used by lifecycle tests that patch _VIRTUAL_PARAMS.
_FORMAT_VP = VirtualParam(
    schema={"type": "string", "enum": ["json", "markdown", "raw"]},
    default="markdown",
    description="Response format control.",
)


# ---------------------------------------------------------------------------
# inject_into
# ---------------------------------------------------------------------------


class TestInjectInto:
    """Tests for inject_into — schema augmentation (mechanism, not format)."""

    def test_adds_patched_entry_when_missing(self):
        """Adds a patched virtual param when not already in properties."""
        params: dict = {"properties": {}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"test_param": _FORMAT_VP},
        ):
            inject_into(params)
        props = params["properties"]
        assert "test_param" in props
        assert props["test_param"]["type"] == "string"
        assert props["test_param"]["default"] == "markdown"

    def test_does_not_overwrite_existing_param(self):
        """Skips virtual param if tool already has a parameter with that name."""
        params: dict = {"properties": {"test_param": {"type": "integer"}}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"test_param": _FORMAT_VP},
        ):
            inject_into(params)
        assert params["properties"]["test_param"] == {"type": "integer"}

    def test_no_op_when_empty_registry(self):
        """Does nothing when _VIRTUAL_PARAMS is empty."""
        params: dict = {"properties": {}}
        inject_into(params)
        assert params["properties"] == {}

    def test_handles_empty_parameters(self):
        """Works with an empty parameters dict, creating properties."""
        params: dict = {}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"test_param": _FORMAT_VP},
        ):
            inject_into(params)
        assert "test_param" in params["properties"]


# ---------------------------------------------------------------------------
# extract_from
# ---------------------------------------------------------------------------


class TestExtractFrom:
    """Tests for extract_from — pre-call parameter extraction (mechanism)."""

    def test_pops_patched_param_and_returns_value(self):
        """Pops 'format' from kwargs and returns {name: value}."""
        kwargs = {"owner": "test", "format": "markdown"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"format": _FORMAT_VP},
        ):
            extracted = extract_from(kwargs)
        assert extracted == {"format": "markdown"}
        assert "format" not in kwargs

    def test_returns_empty_dict_no_virtual_params(self):
        """Returns {} when no virtual params are present."""
        kwargs = {"owner": "test", "repo": "r", "page": 1}
        extracted = extract_from(kwargs)
        assert extracted == {}

    def test_removes_only_known_virtual_params(self):
        """Pops every known virtual param from kwargs."""
        kwargs = {"owner": "test", "format": "json"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"format": _FORMAT_VP},
        ):
            extracted = extract_from(kwargs)
        assert "format" not in kwargs
        assert len(kwargs) == 1
        assert "owner" in kwargs


# ---------------------------------------------------------------------------
# apply_to
# ---------------------------------------------------------------------------


class TestApplyTo:
    """Tests for apply_to — post-call result transformation."""

    def test_runs_post_hook_with_value(self):
        """Calls the post_hook with (result, value)."""
        result = ToolResult(content=[TextContent(type="text", text="hello")])
        transformed = ToolResult(
            content=[TextContent(type="text", text="transformed")]
        )

        hook = MagicMock(return_value=transformed)
        extracted = {"format": "markdown"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": VirtualParam(
                    schema={}, default="json", description="", post_hook=hook
                ),
            },
        ):
            output = apply_to(result, extracted)

        hook.assert_called_once_with(result, "markdown")
        assert output is transformed

    def test_returns_result_when_no_extracted_params(self):
        """Returns result unchanged when extracted is empty."""
        result = ToolResult(content=[TextContent(type="text", text="hello")])
        assert apply_to(result, {}) is result

    def test_handles_none_post_hook(self):
        """VirtualParam with post_hook=None is a no-op."""
        result = ToolResult(content=[TextContent(type="text", text="hello")])
        extracted = {"format": "json"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": VirtualParam(
                    schema={}, default="json", description="", post_hook=None
                ),
            },
        ):
            assert apply_to(result, extracted) is result


# ---------------------------------------------------------------------------
# Integration with _wrap()
# ---------------------------------------------------------------------------


class TestWrapIntegration:
    """Tests that _ToolWrappingTransform._wrap() injects/extracts format."""

    def _make_tool(self) -> Tool:
        """Minimal Tool with _customization_applied flag."""
        return Tool(
            name="issue_list_issues",
            description="List issues in a repository.",
            parameters={"properties": {"owner": {"type": "string"}}},
            meta={
                "_customization_applied": True,
                "_customization": {
                    "has_labels": False,
                    "is_text_response": False,
                    "route_path": "/repos/{owner}/{repo}/issues",
                    "route_method": "GET",
                },
            },
        )

    @pytest.mark.asyncio
    async def test_injects_format_into_parameters(self):
        """_wrap() adds the format parameter to tool schema (promoted)."""
        from gitea_mcp_server.label_manager import LabelManager
        from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform

        transform = _ToolWrappingTransform(
            label_manager=LabelManager(),
            openapi_spec={},
        )
        tool = self._make_tool()
        [wrapped] = await transform.list_tools([tool])

        assert "format" in wrapped.parameters.get("properties", {})
        fmt_schema = wrapped.parameters["properties"]["format"]
        assert fmt_schema["type"] == "string"
        assert fmt_schema["default"] == "markdown"
        assert "markdown" in fmt_schema["enum"]

    @pytest.mark.asyncio
    async def test_format_extracted_before_execution(self):
        """Format is stripped from kwargs before the HTTP execution path."""
        from gitea_mcp_server.label_manager import LabelManager
        from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform

        transform = _ToolWrappingTransform(
            label_manager=LabelManager(),
            openapi_spec={},
        )
        tool = self._make_tool()

        # Wrap the tool
        [wrapped] = await transform.list_tools([tool])

        # Mock the underlying execution
        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="result")],
                structured_content={"result": [{"id": 1}]},
            )

            # Call with format=markdown
            await wrapped.run({"owner": "test", "format": "markdown"})

            # Verify format was stripped before reaching _run_with_error_handling
            call_kwargs = mock_run.call_args[0][0]
            assert "format" not in call_kwargs
            assert call_kwargs == {"owner": "test"}

    @pytest.mark.asyncio
    async def test_default_markdown_no_format_supplied(self):
        """Default markdown when format is not supplied."""
        from gitea_mcp_server.label_manager import LabelManager
        from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform

        transform = _ToolWrappingTransform(
            label_manager=LabelManager(),
            openapi_spec={},
        )
        tool = self._make_tool()

        [wrapped] = await transform.list_tools([tool])

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            expected_result = ToolResult(
                content=[TextContent(type="text", text="markdown output")],
                structured_content={"result": [{"id": 1}]},
            )
            mock_run.return_value = expected_result

            result = await wrapped.run({"owner": "test"})
            assert result.structured_content == {"result": [{"id": 1}]}
