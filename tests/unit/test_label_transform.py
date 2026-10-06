"""Unit tests for LabelTransform - FastMCP Transform wrapping label conversion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import ToolAnnotations

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.models import ToolCustomization
from gitea_mcp_server.tools.label_transform import (
    LabelTransform,
    _convert_labels_inline,
)
from tests.helpers.mock_tool import make_async_mock
from tests.helpers.registration import autogen_meta

if TYPE_CHECKING:
    from fastmcp.utilities.versions import VersionSpec


# ---------------------------------------------------------------------------
# LabelTransform
# ---------------------------------------------------------------------------


def _make_call_next(
    tool: Tool | None,
) -> GetToolNext:
    """Create a call_next that returns the given tool (or None for unknown).

    This eliminates the repetitive ``async def call_next(name, *, version=None):``
    closure that every ``get_tool`` test would otherwise need.  The ``name``
    parameter is accepted (required by the ``GetToolNext`` protocol) but ignored
    — the factory always returns the same tool regardless of which name is
    requested.
    """

    async def call_next(name: str, *, version: VersionSpec | None = None) -> Tool | None:
        return tool

    return call_next


class TestLabelTransform:
    """Tests for LabelTransform as a FastMCP Transform."""

    @pytest.fixture
    def label_service(self) -> AsyncMock:
        return make_async_mock(LabelService)

    @pytest.fixture
    def gitea_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def transform(self, label_service: AsyncMock, gitea_client: AsyncMock) -> LabelTransform:
        return LabelTransform(
            label_service=label_service,
            gitea_client=gitea_client,
        )

    def make_tool(
        self,
        name: str = "test_tool",
        has_labels: bool = False,
    ) -> Tool:
        """Create a Tool with minimal metadata for testing."""
        return Tool(
            name=name,
            parameters={"properties": {}, "required": []},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta=autogen_meta(
                ToolCustomization(
                    has_labels=has_labels,
                    route_path="/test",
                    route_method="POST",
                )
            ),
            annotations=ToolAnnotations(title=name),
        )

    @pytest.mark.asyncio
    async def test_is_transform_subclass(self) -> None:
        """LabelTransform should be a Transform subclass."""
        assert issubclass(LabelTransform, Transform)

    @pytest.mark.asyncio
    async def test_list_tools_passes_through(self, transform: LabelTransform) -> None:
        """list_tools should return tools unchanged."""
        tools = [Tool(name="a", parameters={}), Tool(name="b", parameters={})]
        result = await transform.list_tools(tools)
        assert result is tools  # same list reference

    @pytest.mark.asyncio
    async def test_get_tool_returns_none_for_unknown(self, transform: LabelTransform) -> None:
        """get_tool returns None when call_next returns None."""
        result = await transform.get_tool("nonexistent", _make_call_next(None))
        assert result is None

    @pytest.mark.asyncio
    async def test_get_tool_passes_through_without_labels(self, transform: LabelTransform) -> None:
        """get_tool returns the tool unchanged when has_labels is False."""
        tool = self.make_tool("no_labels", has_labels=False)
        result = await transform.get_tool("no_labels", _make_call_next(tool))
        assert result is tool  # same object, not wrapped

    @pytest.mark.asyncio
    async def test_get_tool_wraps_labels_tool(self, transform: LabelTransform) -> None:
        """get_tool returns a wrapped tool when has_labels is True."""
        tool = self.make_tool("labels_tool", has_labels=True)
        result = await transform.get_tool("labels_tool", _make_call_next(tool))
        assert result is not None
        assert result is not tool  # wrapped - new object
        assert result.name == "labels_tool"
        assert result.meta == tool.meta  # metadata preserved

    @pytest.mark.asyncio
    async def test_label_conversion_runs_before_execution(
        self, transform: LabelTransform, label_service: AsyncMock
    ) -> None:
        """The wrapped tool should call validate_and_convert before the HTTP call."""
        label_service.validate_and_convert.return_value = [1, 42]

        # Use Tool.from_tool to create a spy on the original run(). This is the
        # FastMCP way to create a modified tool - LabelTransform will capture
        # the spy's run() as original_run.
        tool = self.make_tool("labels_tool", has_labels=True)
        executed = False

        async def spy_transform_fn(**kwargs: Any) -> ToolResult:
            nonlocal executed
            executed = True
            return ToolResult(structured_content={"result": "ok"})

        spied_tool = Tool.from_tool(tool, transform_fn=spy_transform_fn)

        wrapped = await transform.get_tool("labels_tool", _make_call_next(spied_tool))
        assert wrapped is not None

        await wrapped.run(
            arguments={
                "owner": "test-owner",
                "repo": "test-repo",
                "labels": ["bug", 42],
            }
        )

        label_service.validate_and_convert.assert_awaited_once_with(
            ["bug", 42],
            "test-owner",
            "test-repo",
            transform._gitea_client,
        )
        assert executed  # HTTP call happened

    @pytest.mark.asyncio
    async def test_unknown_labels_raise_value_error(
        self, transform: LabelTransform, label_service: AsyncMock
    ) -> None:
        """Unknown labels should produce a ValueError (agent-friendly)."""
        label_service.validate_and_convert.side_effect = ValidationError(
            message="Unknown label name(s): ['nonexistent']",
            field="labels",
        )

        tool = self.make_tool("labels_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        wrapped = await transform.get_tool("labels_tool", _make_call_next(spied_tool))
        assert wrapped is not None

        with pytest.raises(ValueError, match="nonexistent"):
            await wrapped.run(
                arguments={
                    "owner": "test-owner",
                    "repo": "test-repo",
                    "labels": ["nonexistent"],
                }
            )
        run_spy.assert_not_awaited()  # HTTP call never happened

    @pytest.mark.asyncio
    async def test_no_gitea_client_skips_conversion(self) -> None:
        """When gitea_client is None, no validation should happen."""
        label_service = AsyncMock(spec=LabelService)
        transform = LabelTransform(
            label_service=label_service,
            gitea_client=None,
        )

        tool = self.make_tool("labels_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        wrapped = await transform.get_tool("labels_tool", _make_call_next(spied_tool))
        assert wrapped is not None

        await wrapped.run(
            arguments={
                "owner": "test-owner",
                "repo": "test-repo",
                "labels": ["bug"],
            }
        )

        label_service.validate_and_convert.assert_not_called()
        run_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_labels_in_args_skips_conversion(
        self, transform: LabelTransform, label_service: AsyncMock
    ) -> None:
        """When labels key is absent from args, no conversion."""
        tool = self.make_tool("labels_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        wrapped = await transform.get_tool("labels_tool", _make_call_next(spied_tool))
        assert wrapped is not None

        await wrapped.run(arguments={"owner": "test-owner", "repo": "test-repo"})

        label_service.validate_and_convert.assert_not_called()
        run_spy.assert_awaited_once()


# ---------------------------------------------------------------------------
# _convert_labels_inline
# ---------------------------------------------------------------------------


class TestConvertLabelsInline:
    """Tests for _convert_labels_inline helper used inside LabelTransform."""

    @pytest.fixture
    def label_service(self) -> AsyncMock:
        return make_async_mock(LabelService)

    @pytest.fixture
    def gitea_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_skips_when_labels_empty(
        self, label_service: AsyncMock, gitea_client: AsyncMock
    ) -> None:
        """Empty labels list -> no conversion."""
        kwargs: dict[str, Any] = {"labels": []}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_labels_absent(
        self, label_service: AsyncMock, gitea_client: AsyncMock
    ) -> None:
        """No labels key -> no conversion."""
        kwargs = {"owner": "o", "repo": "r"}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_owner_missing(
        self, label_service: AsyncMock, gitea_client: AsyncMock
    ) -> None:
        """No owner/org -> no conversion."""
        kwargs = {"repo": "r", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_repo_missing(
        self, label_service: AsyncMock, gitea_client: AsyncMock
    ) -> None:
        """No repo -> no conversion."""
        kwargs = {"owner": "o", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_client(self, label_service: AsyncMock) -> None:
        """No gitea_client -> no conversion."""
        kwargs = {"owner": "o", "repo": "r", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client=None)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_org_fallback(
        self, label_service: AsyncMock, gitea_client: AsyncMock
    ) -> None:
        """org parameter is used as fallback for owner."""
        label_service.validate_and_convert.return_value = [1]
        kwargs = {"org": "my-org", "repo": "r", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_awaited_once_with(
            ["bug"],
            "my-org",
            "r",
            gitea_client,
        )

    @pytest.mark.asyncio
    async def test_converts_labels_in_place(
        self, label_service: AsyncMock, gitea_client: AsyncMock
    ) -> None:
        """Labels are converted and written back to kwargs."""
        label_service.validate_and_convert.return_value = [1, 2]
        kwargs = {"owner": "o", "repo": "r", "labels": ["bug", "feature"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        assert kwargs["labels"] == [1, 2]
