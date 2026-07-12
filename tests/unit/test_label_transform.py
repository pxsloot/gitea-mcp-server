"""Unit tests for LabelTransform — FastMCP Transform wrapping label conversion."""

from typing import TYPE_CHECKING, Any, Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import ToolAnnotations

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.tools.label_transform import (
    LabelTransform,
    _convert_labels_inline,
)

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )


# The session-scoped ``_init_otel_exporter`` and ``trace_exporter`` fixture
# are defined in ``tests/conftest.py`` (shared across all test modules).


# ---------------------------------------------------------------------------
# LabelTransform
# ---------------------------------------------------------------------------


class TestLabelTransform:
    """Tests for LabelTransform as a FastMCP Transform."""

    @pytest.fixture
    def label_service(self):
        return AsyncMock(spec=LabelService)

    @pytest.fixture
    def gitea_client(self):
        return AsyncMock()

    @pytest.fixture
    def transform(self, label_service, gitea_client):
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
            meta={
                "_customization_applied": True,
                "_customization": {
                    "has_labels": has_labels,
                    "route_path": "/test",
                    "route_method": "POST",
                },
            },
            annotations=ToolAnnotations(title=name),
        )

    async def _call_next(self, name, *, version=None):
        """Simulate the inner transform/provider returning tools by name."""
        return self._tool_registry.get(name)

    @pytest.mark.asyncio
    async def test_is_transform_subclass(self):
        """LabelTransform should be a Transform subclass."""
        assert issubclass(LabelTransform, Transform)

    @pytest.mark.asyncio
    async def test_list_tools_passes_through(self, transform):
        """list_tools should return tools unchanged."""
        tools = [Tool(name="a", parameters={}), Tool(name="b", parameters={})]
        result = await transform.list_tools(tools)
        assert result is tools  # same list reference

    @pytest.mark.asyncio
    async def test_get_tool_returns_none_for_unknown(self, transform):
        """get_tool returns None when call_next returns None."""

        async def call_next(name, *, version=None):
            return None

        result = await transform.get_tool("nonexistent", call_next)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_tool_passes_through_without_labels(self, transform):
        """get_tool returns the tool unchanged when has_labels is False."""
        tool = self.make_tool("no_labels", has_labels=False)

        async def call_next(name, *, version=None):
            return tool

        result = await transform.get_tool("no_labels", call_next)
        assert result is tool  # same object, not wrapped

    @pytest.mark.asyncio
    async def test_get_tool_wraps_labels_tool(self, transform):
        """get_tool returns a wrapped tool when has_labels is True."""
        tool = self.make_tool("labels_tool", has_labels=True)

        async def call_next(name, *, version=None):
            return tool

        result = await transform.get_tool("labels_tool", call_next)
        assert result is not tool  # wrapped — new object
        assert result.name == "labels_tool"
        assert result.meta == tool.meta  # metadata preserved

    @pytest.mark.asyncio
    async def test_label_conversion_runs_before_execution(self, transform, label_service):
        """The wrapped tool should call validate_and_convert before the HTTP call."""
        label_service.validate_and_convert.return_value = [1, 42]

        # Use Tool.from_tool to create a spy on the original run(). This is the
        # FastMCP way to create a modified tool — LabelTransform will capture
        # the spy's run() as original_run.
        tool = self.make_tool("labels_tool", has_labels=True)
        executed = False

        async def spy_transform_fn(**kwargs):
            nonlocal executed
            executed = True
            return ToolResult(structured_content={"result": "ok"})

        spied_tool = Tool.from_tool(tool, transform_fn=spy_transform_fn)

        async def call_next(name, *, version=None):
            return spied_tool

        wrapped = await transform.get_tool("labels_tool", call_next)

        await wrapped.run(arguments={
            "owner": "test-owner",
            "repo": "test-repo",
            "labels": ["bug", 42],
        })

        label_service.validate_and_convert.assert_awaited_once_with(
            ["bug", 42], "test-owner", "test-repo", transform._gitea_client,
        )
        assert executed  # HTTP call happened

    @pytest.mark.asyncio
    async def test_unknown_labels_raise_value_error(self, transform, label_service):
        """Unknown labels should produce a ValueError (agent-friendly)."""
        label_service.validate_and_convert.side_effect = ValidationError(
            message="Unknown label name(s): ['nonexistent']", field="labels",
        )

        tool = self.make_tool("labels_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        async def call_next(name, *, version=None):
            return spied_tool

        wrapped = await transform.get_tool("labels_tool", call_next)

        with pytest.raises(ValueError, match="nonexistent"):
            await wrapped.run(arguments={
                "owner": "test-owner",
                "repo": "test-repo",
                "labels": ["nonexistent"],
            })
        run_spy.assert_not_awaited()  # HTTP call never happened

    @pytest.mark.asyncio
    async def test_no_gitea_client_skips_conversion(self):
        """When gitea_client is None, no validation should happen."""
        label_service = AsyncMock(spec=LabelService)
        transform = LabelTransform(
            label_service=label_service,
            gitea_client=None,
        )

        tool = self.make_tool("labels_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        async def call_next(name, *, version=None):
            return spied_tool

        wrapped = await transform.get_tool("labels_tool", call_next)

        await wrapped.run(arguments={
            "owner": "test-owner",
            "repo": "test-repo",
            "labels": ["bug"],
        })

        label_service.validate_and_convert.assert_not_called()
        run_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_labels_in_args_skips_conversion(self, transform, label_service):
        """When labels key is absent from args, no conversion."""
        tool = self.make_tool("labels_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        async def call_next(name, *, version=None):
            return spied_tool

        wrapped = await transform.get_tool("labels_tool", call_next)

        await wrapped.run(arguments={"owner": "test-owner", "repo": "test-repo"})

        label_service.validate_and_convert.assert_not_called()
        run_spy.assert_awaited_once()


# ---------------------------------------------------------------------------
# _convert_labels_inline
# ---------------------------------------------------------------------------


class TestConvertLabelsInline:
    """Tests for _convert_labels_inline helper used inside LabelTransform."""

    @pytest.fixture
    def label_service(self):
        return AsyncMock(spec=LabelService)

    @pytest.fixture
    def gitea_client(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_skips_when_labels_empty(self, label_service, gitea_client):
        """Empty labels list -> no conversion."""
        kwargs = {"labels": []}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_labels_absent(self, label_service, gitea_client):
        """No labels key -> no conversion."""
        kwargs = {"owner": "o", "repo": "r"}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_owner_missing(self, label_service, gitea_client):
        """No owner/org -> no conversion."""
        kwargs = {"repo": "r", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_repo_missing(self, label_service, gitea_client):
        """No repo -> no conversion."""
        kwargs = {"owner": "o", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_client(self, label_service):
        """No gitea_client -> no conversion."""
        kwargs = {"owner": "o", "repo": "r", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client=None)
        label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_org_fallback(self, label_service, gitea_client):
        """org parameter is used as fallback for owner."""
        label_service.validate_and_convert.return_value = [1]
        kwargs = {"org": "my-org", "repo": "r", "labels": ["bug"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        label_service.validate_and_convert.assert_awaited_once_with(
            ["bug"], "my-org", "r", gitea_client,
        )

    @pytest.mark.asyncio
    async def test_converts_labels_in_place(self, label_service, gitea_client):
        """Labels are converted and written back to kwargs."""
        label_service.validate_and_convert.return_value = [1, 2]
        kwargs = {"owner": "o", "repo": "r", "labels": ["bug", "feature"]}
        await _convert_labels_inline(kwargs, label_service, gitea_client)
        assert kwargs["labels"] == [1, 2]


# ---------------------------------------------------------------------------
# LabelTransform — OpenTelemetry spans
# ---------------------------------------------------------------------------


class TestLabelTransformTelemetry:
    """Tests for OTEL spans emitted from LabelTransform._wrap_tool."""

    @pytest.fixture
    def label_service(self):
        return AsyncMock(spec=LabelService)

    @pytest.fixture
    def gitea_client(self):
        return AsyncMock()

    @pytest.fixture
    def transform(self, label_service, gitea_client):
        return LabelTransform(
            label_service=label_service,
            gitea_client=gitea_client,
        )

    def make_tool(
        self,
        name: str = "test_tool",
        has_labels: bool = False,
    ) -> Tool:
        return Tool(
            name=name,
            parameters={"properties": {}, "required": []},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta={
                "_customization_applied": True,
                "_customization": {
                    "has_labels": has_labels,
                    "route_path": "/test",
                    "route_method": "POST",
                },
            },
            annotations=ToolAnnotations(title=name),
        )

    @pytest.mark.asyncio
    async def test_emits_convert_labels_span(self, transform, label_service, trace_exporter):
        """Wrapping a label tool emits a ``{tool}.convert_labels`` span."""
        label_service.validate_and_convert.return_value = [1, 42]

        tool = self.make_tool("labels_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        async def call_next(name, *, version=None):
            return spied_tool

        wrapped = await transform.get_tool("labels_tool", call_next)
        await wrapped.run(arguments={
            "owner": "test-owner",
            "repo": "test-repo",
            "labels": ["bug", 42],
        })

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        assert "labels_tool.convert_labels" in span_names, (
            f"Expected 'labels_tool.convert_labels' in span names: {span_names}"
        )

    @pytest.mark.asyncio
    async def test_no_convert_labels_span_when_no_labels(
        self, transform, trace_exporter
    ):
        """When has_labels is False, no convert_labels span is emitted."""
        tool = self.make_tool("no_labels", has_labels=False)
        async def call_next(name, *, version=None):
            return tool

        wrapped = await transform.get_tool("no_labels", call_next)
        assert wrapped is tool  # not wrapped — passes through

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        assert "no_labels.convert_labels" not in span_names, (
            f"Expected no 'convert_labels' span, got: {span_names}"
        )

    @pytest.mark.asyncio
    async def test_convert_labels_span_has_tool_name_attribute(
        self, transform, label_service, trace_exporter
    ):
        """The convert_labels span carries a ``tool.name`` attribute."""
        label_service.validate_and_convert.return_value = [1]

        tool = self.make_tool("attr_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        async def call_next(name, *, version=None):
            return spied_tool

        wrapped = await transform.get_tool("attr_tool", call_next)
        await wrapped.run(arguments={
            "owner": "o", "repo": "r", "labels": ["bug"],
        })

        spans = trace_exporter.get_finished_spans()
        for span in spans:
            if span.name == "attr_tool.convert_labels":
                assert span.attributes.get("tool.name") == "attr_tool"
                assert span.attributes.get("labels.has_labels") is True
                break
        else:
            pytest.fail("No 'attr_tool.convert_labels' span found")

    @pytest.mark.asyncio
    async def test_convert_labels_span_sets_error_on_failure(
        self, transform, label_service, trace_exporter
    ):
        """When label conversion fails, the span records an error attribute."""
        label_service.validate_and_convert.side_effect = ValidationError(
            message="Unknown label: bad", field="labels",
        )

        tool = self.make_tool("fail_tool", has_labels=True)
        run_spy = AsyncMock(return_value=ToolResult(structured_content={"result": "ok"}))
        spied_tool = Tool.from_tool(tool, transform_fn=lambda **kw: run_spy(kw))

        async def call_next(name, *, version=None):
            return spied_tool

        wrapped = await transform.get_tool("fail_tool", call_next)

        with pytest.raises(ValueError, match="Unknown label"):
            await wrapped.run(arguments={
                "owner": "o", "repo": "r", "labels": ["bad"],
            })

        spans = trace_exporter.get_finished_spans()
        for span in spans:
            if span.name == "fail_tool.convert_labels":
                assert span.attributes.get("error") is True
                assert "Unknown label" in (span.attributes.get("error.message") or "")
                break
        else:
            pytest.fail("No 'fail_tool.convert_labels' span found")
