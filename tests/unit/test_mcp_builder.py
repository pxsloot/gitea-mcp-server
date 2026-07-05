"""Unit tests for server_setup/mcp_builder.py (_customize_metadata, _ToolWrappingTransform)."""

from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.constants import LABEL_GUIDANCE, TITLE_TRUNCATE_LIMIT
from gitea_mcp_server.server_setup.mcp_builder import (
    _customize_metadata,
    _get_deprecated_routes,
    _ToolWrappingTransform,
)

# ---------------------------------------------------------------------------
# _customize_metadata
# ---------------------------------------------------------------------------


class TestCustomizeMetadata:
    """Tests for _customize_metadata — in-place metadata on OpenAPITools."""

    def test_skips_non_openapi_tool(self):
        """Non-OpenAPITool components are skipped."""
        route = MagicMock(path="/test", summary="Test", operation_id="test_op", method="GET")
        resource = MagicMock(spec=object)

        _customize_metadata(route, resource, openapi_spec={})

    def test_sets_title_and_annotations(self):
        """Title and ToolAnnotations are set from route summary."""
        route = MagicMock(
            path="/test", summary="List items", operation_id="list_items", method="GET"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_items"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.annotations is not None
        assert tool.annotations.title == "List items"
        assert tool.annotations.readOnlyHint is True

    def test_title_from_operation_id(self):
        """Title is generated from operationId when summary is None."""
        route = MagicMock(path="/test", summary=None, operation_id="get_user_by_id", method="GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_user_by_id"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get user by ID"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.annotations.title == "Get User By Id"

    def test_long_operation_id_truncated(self):
        """Operation IDs longer than TITLE_TRUNCATE_LIMIT are truncated."""
        long_op_id = (
            "this_is_a_very_long_operation_id_that_exceeds_fifty_characters_and_needs_truncation"
        )
        route = MagicMock(path="/test", summary=None, operation_id=long_op_id, method="GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test operation"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert len(tool.annotations.title) <= TITLE_TRUNCATE_LIMIT
        assert tool.annotations.title.endswith("...")

    def test_adds_annotations_from_dict(self):
        """Annotations dict is converted to ToolAnnotations."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="List issues",
            operation_id="list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = {"title": "Old Title"}
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "List issues"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "List issues"
        assert "issue" in tool.tags

    def test_preserves_existing_toolannotations(self):
        """Existing ToolAnnotations are preserved and updated."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/pulls/{index}",
            summary="Get pull request",
            operation_id="get_pull",
            method="GET",
        )
        existing = ToolAnnotations(title="Old Title", readOnlyHint=True)
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_pull"
        tool.annotations = existing
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get pull request"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "Get pull request"
        assert tool.annotations.readOnlyHint is True
        assert "pull_request" in tool.tags

    def test_category_detection_various_paths(self):
        """Category tag is inferred correctly from various route paths."""
        test_cases = [
            ("/repos/{owner}/{repo}/issues", "issue"),
            ("/repos/{owner}/{repo}/pulls/{index}", "pull_request"),
            ("/user/keys", "user"),
            ("/orgs/{org}", "organization"),
            ("/admin/users", "admin"),
            ("/repos/{owner}/{repo}/branches", "repository"),
            ("/version", "misc"),
        ]

        for path, expected_category in test_cases:
            route = MagicMock(path=path, summary=None, operation_id="test_op", method="GET")
            tool = MagicMock(spec=OpenAPITool)
            tool.name = "test"
            tool.annotations = None
            tool.tags = set()
            tool.parameters = {"properties": {}}
            tool.output_schema = None
            tool.description = "Test"
            tool.meta = {}

            _customize_metadata(route, tool, openapi_spec={})

            assert tool.annotations is not None
            assert expected_category in tool.tags, (
                f"Failed for {path}: category {expected_category} not in tags"
            )

    def test_destructive_hint_from_method(self):
        """DELETE method sets destructiveHint = True."""
        route = MagicMock(
            path="/repos/{owner}/{repo}",
            summary="Delete repo",
            operation_id="delete_repo",
            method="DELETE",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "delete_repo"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.annotations.destructiveHint is True

    def test_sets_description(self):
        """Description is preserved and updated."""
        route = MagicMock(path="/user", summary="Get user", operation_id="get_user", method="GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_user"
        tool.annotations = None
        tool.tags = set()
        tool.description = "Original description"
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.description == "Original description"

    def test_uses_component_description_not_doc(self):
        """Verify that component.description is used, not __doc__."""
        route = MagicMock(path="/test", summary="Test", operation_id="test_op", method="GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test_op"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Description from attribute"
        tool.__doc__ = "Docstring should be ignored"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert "Description from attribute" in tool.description
        assert "Docstring should be ignored" not in tool.description

    def test_applies_label_guidance(self):
        """Verify LABEL_GUIDANCE is appended for tools with labels parameter."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="Create issue",
            operation_id="create_issue",
            method="POST",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_create_issue"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {
            "properties": {"labels": {"type": "array", "items": {"type": "integer"}}}
        }
        tool.output_schema = None
        tool.description = "Create an issue"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert LABEL_GUIDANCE.strip() in tool.description

    def test_applies_label_guidance_nullable(self):
        """Verify label guidance works with nullable array type ['array', 'null']."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="Create issue",
            operation_id="create_issue",
            method="POST",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_create_issue"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {
            "properties": {"labels": {"type": ["array", "null"], "items": {"type": "integer"}}}
        }
        tool.output_schema = None
        tool.description = "Create an issue"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert LABEL_GUIDANCE.strip() in tool.description

    def test_does_not_apply_label_guidance(self):
        """Verify LABEL_GUIDANCE is not added if tool has no labels parameter."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="List issues",
            operation_id="list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "List issues"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert LABEL_GUIDANCE.strip() not in tool.description

    def test_sets_meta_flags(self):
        """Meta dict contains _META_CUSTOMIZED and _customization."""
        route = MagicMock(
            path="/repos/{owner}/{repo}", summary="Get repo", operation_id="get_repo", method="GET"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_repo"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.meta.get("_customization_applied") is True
        assert "_customization" in tool.meta
        assert tool.meta["_customization"]["route_path"] == "/repos/{owner}/{repo}"
        assert tool.meta["_customization"]["route_method"] == "GET"
        assert tool.meta["_customization"]["has_labels"] is False
        assert tool.meta["_customization"]["is_text_response"] is False

    def test_registers_invalidation_patterns_for_write(self):
        """Write methods register cache invalidation patterns."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="Create issue",
            operation_id="create_issue",
            method="POST",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "create_issue"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.register_tool_invalidation"
        ) as mock_register:
            _customize_metadata(route, tool, openapi_spec={})

            mock_register.assert_called_once()

    def test_read_method_does_not_register_invalidation(self):
        """GET methods do not register cache invalidation."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="List issues",
            operation_id="list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.register_tool_invalidation"
        ) as mock_register:
            _customize_metadata(route, tool, openapi_spec={})

            mock_register.assert_not_called()

    def test_output_schema_not_none_sets_wrap_flag(self):
        """When output_schema is not None, x-fastmcp-wrap-result is set."""
        route = MagicMock(
            path="/repos/{owner}/{repo}",
            summary="Get repo",
            operation_id="get_repo",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_repo"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        output_schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.derive_output_schema",
            return_value=output_schema,
        ):
            _customize_metadata(route, tool, openapi_spec={})
            assert tool.output_schema["x-fastmcp-wrap-result"] is True

    def test_array_output_schema_adds_pagination_fields(self):
        """Array output_schema gets pagination fields (has_more, next_offset, total_count)."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="List issues",
            operation_id="list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        # Array output schema: type=array with items schema
        output_schema = {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
        }

        with (
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.derive_output_schema",
                return_value=output_schema,
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                return_value=True,
            ),
        ):
            _customize_metadata(route, tool, openapi_spec={})
            props = output_schema.setdefault("properties", {})
            assert "has_more" in props
            assert "next_offset" in props
            assert "total_count" in props
            assert props["has_more"]["type"] == "boolean"
            assert props["next_offset"]["type"] == "integer"
            assert props["total_count"]["type"] == "integer"

    def test_text_plain_fallback_schema(self):
        """Text/plain endpoints get string output_schema when derive_output_schema returns None."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/pulls/{index}.{diffType}",
            summary="Download pull request diff",
            operation_id="repo_download_pull_diff_or_patch",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_download_pull_diff_or_patch"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        with (
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.derive_output_schema",
                return_value=None,
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_text_response",
                return_value=True,
            ),
        ):
            _customize_metadata(route, tool, openapi_spec={})

            assert tool.output_schema is not None
            assert tool.output_schema["type"] == "object"
            assert tool.output_schema["properties"]["result"]["type"] == "string"
            # x-fastmcp-wrap-result should be set since output_schema is now not None
            assert tool.output_schema.get("x-fastmcp-wrap-result") is True

    def test_json_endpoint_retains_derived_schema(self):
        """JSON endpoints keep their derived output_schema even when is_text_response is False."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="List issues",
            operation_id="list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        derived_schema = {
            "type": "object",
            "properties": {"result": {"type": "array", "items": {"type": "object"}}},
        }

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.derive_output_schema",
            return_value=derived_schema,
        ):
            _customize_metadata(route, tool, openapi_spec={})

            assert tool.output_schema is not None
            # Should be the derived schema, not the text/plain fallback
            assert tool.output_schema["properties"]["result"]["type"] == "array"


# ---------------------------------------------------------------------------
# _get_deprecated_routes
# ---------------------------------------------------------------------------


class TestGetDeprecatedRoutes:
    """Tests for _get_deprecated_routes — filtering deprecated operations from OpenAPI spec."""

    def test_empty_paths(self):
        """Empty paths dict returns empty set."""
        spec = {"openapi": "3.1.1", "paths": {}, "info": {"title": "T", "version": "1"}}
        result = _get_deprecated_routes(spec)
        assert result == set()

    def test_missing_paths(self):
        """Spec with no paths key returns empty set."""
        spec = {"openapi": "3.1.1", "info": {"title": "T", "version": "1"}}
        result = _get_deprecated_routes(spec)
        assert result == set()

    def test_non_dict_paths(self):
        """Non-dict paths value returns empty set."""
        spec = {"openapi": "3.1.1", "paths": "not_a_dict"}
        result = _get_deprecated_routes(spec)
        assert result == set()

    def test_no_deprecated_returns_empty(self):
        """No deprecated:true operations returns empty set."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser"},
                    "post": {"operationId": "createUser"},
                },
            },
        }
        result = _get_deprecated_routes(spec)
        assert result == set()

    def test_single_deprecated_get(self):
        """Single deprecated GET is found."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser", "deprecated": True},
                    "post": {"operationId": "createUser"},
                },
            },
        }
        result = _get_deprecated_routes(spec)
        assert result == {("/user", "GET")}

    def test_multiple_deprecated_operations(self):
        """Multiple deprecated methods on same path are found."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {"operationId": "getRepo"},
                    "put": {"operationId": "updateRepo", "deprecated": True},
                    "delete": {"operationId": "deleteRepo", "deprecated": True},
                },
            },
        }
        result = _get_deprecated_routes(spec)
        assert result == {("/repos/{owner}/{repo}", "PUT"), ("/repos/{owner}/{repo}", "DELETE")}

    def test_multiple_paths_mixed(self):
        """Deprecated across multiple paths, non-deprecated excluded."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/v1/old": {
                    "get": {"operationId": "oldGet", "deprecated": True},
                    "post": {"operationId": "oldPost", "deprecated": True},
                },
                "/v2/active": {
                    "get": {"operationId": "activeGet"},
                    "post": {"operationId": "activePost"},
                },
                "/v2/also_old": {
                    "patch": {"operationId": "oldPatch", "deprecated": True},
                },
            },
        }
        result = _get_deprecated_routes(spec)
        assert result == {
            ("/v1/old", "GET"),
            ("/v1/old", "POST"),
            ("/v2/also_old", "PATCH"),
        }

    def test_deprecated_false_not_included(self):
        """deprecated: false is treated as not deprecated."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser", "deprecated": False},
                },
            },
        }
        result = _get_deprecated_routes(spec)
        assert result == set()

    def test_non_http_method_keys_ignored(self):
        """Parameters key at path level is not treated as an operation."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "parameters": [{"name": "owner", "in": "path"}],
                    "get": {"operationId": "getRepo", "deprecated": True},
                },
            },
        }
        result = _get_deprecated_routes(spec)
        assert result == {("/repos/{owner}/{repo}", "GET")}

    def test_non_dict_path_item_skipped(self):
        """Non-dict path item is skipped defensively."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/broken": "not_a_dict",
                "/good": {
                    "get": {"operationId": "goodGet", "deprecated": True},
                },
            },
        }
        result = _get_deprecated_routes(spec)
        assert result == {("/good", "GET")}

    def test_http_methods_comprehensive(self):
        """All HTTP methods are properly detected."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/resource": {
                    method: {"operationId": f"{method}Resource", "deprecated": True}
                    for method in ("get", "post", "put", "delete", "patch", "options", "head", "trace")
                },
            },
        }
        result = _get_deprecated_routes(spec)
        expected = {("/resource", method.upper()) for method in ("get", "post", "put", "delete", "patch", "options", "head", "trace")}
        assert result == expected
        assert len(result) == 8


# ---------------------------------------------------------------------------
# _ToolWrappingTransform — OpenTelemetry spans
# ---------------------------------------------------------------------------

# Shared InMemorySpanExporter for all telemetry tests in this module.
# OpenTelemetry 1.43+ enforces a set-once guard on the global
# TracerProvider, so we set it once at module load time.
_TRACE_EXPORTER: Any = None


def _init_shared_exporter() -> None:
    """Set the global TracerProvider with an InMemorySpanExporter (once)."""
    global _TRACE_EXPORTER  # noqa: PLW0603
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    _TRACE_EXPORTER = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_TRACE_EXPORTER))
    trace.set_tracer_provider(provider)


_init_shared_exporter()


@pytest.fixture
def trace_exporter() -> Generator[Any, None, None]:
    """Yield the shared InMemorySpanExporter, cleared between tests."""
    _TRACE_EXPORTER.clear()
    yield _TRACE_EXPORTER


class TestToolWrappingTransformTelemetry:
    """Tests for custom OTEL spans emitted from _ToolWrappingTransform._run_transform_pipeline."""

    def make_transform(self, openapi_spec=None):
        from gitea_mcp_server.label_manager import LabelManager

        return _ToolWrappingTransform(
            label_manager=LabelManager(),
            openapi_spec=openapi_spec or {},
        )

    def make_tool(self, name: str = "test_tool") -> Tool:
        return Tool(
            name=name,
            tags={"test"},
            description="Test tool",
            parameters={"properties": {}, "required": []},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta={
                "_customization_applied": True,
                "_customization": {
                    "has_labels": False,
                    "is_text_response": False,
                    "route_path": "/test",
                    "route_method": "GET",
                },
            },
            annotations=ToolAnnotations(title="Test"),
        )

    @pytest.mark.asyncio
    async def test_pipeline_emits_validate_span(self, trace_exporter):
        """Pipeline emits a ``{tool}.validate`` span with arg_count attribute."""
        transform = self.make_transform()
        tool = self.make_tool("test_tool")

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._convert_labels",
                new_callable=AsyncMock,
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                return_value=False,
            ),
        ):
            from fastmcp.tools.base import ToolResult

            mock_run.return_value = ToolResult(structured_content={"result": "ok"})

            result = await transform.list_tools([tool])
            wrapped = result[0]
            await wrapped.run(arguments={"key": "value"})

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        assert "test_tool.validate" in span_names, (
            f"Expected 'test_tool.validate' in span names: {span_names}"
        )
        assert "test_tool.execute" in span_names, (
            f"Expected 'test_tool.execute' in span names: {span_names}"
        )

    @pytest.mark.asyncio
    async def test_pipeline_emits_convert_labels_span(self, trace_exporter):
        """Pipeline emits a ``{tool}.convert_labels`` span when labels are present."""
        transform = self.make_transform()
        tool = self.make_tool("labels_tool")
        tool.meta["_customization"]["has_labels"] = True

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._convert_labels",
                new_callable=AsyncMock,
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                return_value=False,
            ),
        ):
            from fastmcp.tools.base import ToolResult

            mock_run.return_value = ToolResult(structured_content={"result": "ok"})

            result = await transform.list_tools([tool])
            wrapped = result[0]
            await wrapped.run(arguments={})

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        assert "labels_tool.convert_labels" in span_names, (
            f"Expected 'labels_tool.convert_labels' in span names: {span_names}"
        )

    @pytest.mark.asyncio
    async def test_spans_carry_tool_name_attribute(self, trace_exporter):
        """Validate and execute spans carry ``tool.name`` attribute."""
        transform = self.make_transform()
        tool = self.make_tool("attr_tool")

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._convert_labels",
                new_callable=AsyncMock,
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                return_value=False,
            ),
        ):
            from fastmcp.tools.base import ToolResult

            mock_run.return_value = ToolResult(structured_content={"result": "ok"})

            result = await transform.list_tools([tool])
            wrapped = result[0]
            await wrapped.run(arguments={})

        spans = trace_exporter.get_finished_spans()
        for span in spans:
            if span.name == "attr_tool.validate":
                assert span.attributes.get("tool.name") == "attr_tool"
            if span.name == "attr_tool.execute":
                assert span.attributes.get("http.route") == "/test"
                assert span.attributes.get("http.method") == "GET"


# ---------------------------------------------------------------------------
# create_openapi_provider
# ---------------------------------------------------------------------------


class TestCreateOpenapiProvider:
    """Tests for create_openapi_provider — provider creation and deprecated route filtering."""

    def test_deprecated_routes_are_filtered_out(self, caplog):
        """Deprecated routes are excluded via route_map_fn."""
        import logging

        caplog.set_level(logging.DEBUG)

        from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider

        # Spec with a deprecated route
        openapi_spec = {
            "openapi": "3.1.1",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser"},
                },
                "/old/endpoint": {
                    "post": {"operationId": "oldEndpoint", "deprecated": True},
                },
            },
            "components": {"schemas": {}},
        }

        from gitea_mcp_server.label_manager import LabelManager

        client = MagicMock()
        label_manager = LabelManager()
        provider = create_openapi_provider(
            openapi_spec=openapi_spec,
            client=client,
            label_manager=label_manager,
        )

        assert provider is not None
        assert "Excluding deprecated endpoint" in caplog.text


# ---------------------------------------------------------------------------
# _ToolWrappingTransform
# ---------------------------------------------------------------------------


class TestToolWrappingTransform:
    """Tests for _ToolWrappingTransform."""

    def make_transform(self, openapi_spec=None):
        from gitea_mcp_server.label_manager import LabelManager

        return _ToolWrappingTransform(
            label_manager=LabelManager(),
            openapi_spec=openapi_spec or {},
        )

    def make_tool(self, customized=True):
        meta: dict[str, Any] = {}
        if customized:
            meta = {
                "_customization_applied": True,
                "_customization": {
                    "has_labels": False,
                    "is_text_response": False,
                    "route_path": "/test",
                    "route_method": "GET",
                },
            }
        return Tool(
            name="test_tool",
            tags={"test"},
            description="Test tool",
            parameters={"properties": {}, "required": []},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta=meta,
            annotations=ToolAnnotations(title="Test"),
        )

    @pytest.mark.asyncio
    async def test_list_tools_passthrough_uncustomized(self):
        """Uncustomized tools pass through without wrapping."""
        transform = self.make_transform()
        tool = self.make_tool(customized=False)
        result = await transform.list_tools([tool])
        assert len(result) == 1
        assert result[0] is tool

    @pytest.mark.asyncio
    async def test_list_tools_wraps_customized(self):
        """Customized tools are wrapped (new Tool created)."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        result = await transform.list_tools([tool])
        assert len(result) == 1
        assert result[0] is not tool
        assert isinstance(result[0], Tool)

    @pytest.mark.asyncio
    async def test_get_tool_passthrough_uncustomized(self):
        """Uncustomized tools from call_next pass through."""
        transform = self.make_transform()
        tool = self.make_tool(customized=False)

        async def call_next(name, version=None):  # noqa: ARG001
            return tool

        result = await transform.get_tool("test_tool", call_next)
        assert result is tool

    @pytest.mark.asyncio
    async def test_get_tool_wraps_customized(self):
        """Customized tools from call_next are wrapped."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        async def call_next(name, version=None):  # noqa: ARG001
            return tool

        result = await transform.get_tool("test_tool", call_next)
        assert result is not tool
        assert isinstance(result, Tool)

    @pytest.mark.asyncio
    async def test_get_tool_none_passthrough(self):
        """None from call_next passes through."""
        transform = self.make_transform()

        async def call_next(name, version=None):  # noqa: ARG001
            return None

        result = await transform.get_tool("test_tool", call_next)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_tools_empty(self):
        """Empty list passes through."""
        transform = self.make_transform()
        result = await transform.list_tools([])
        assert result == []

    @pytest.mark.asyncio
    async def test_wrapped_tool_preserves_metadata(self):
        """Wrapped tool preserves name, tags, description, output_schema."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        result = await transform.list_tools([tool])
        wrapped = result[0]
        assert wrapped.name == "test_tool"
        assert "test" in wrapped.tags
        assert wrapped.description == "Test tool"
        assert wrapped.output_schema == tool.output_schema

    @pytest.mark.asyncio
    async def test_wrapped_tool_executes_transform_fn(self):
        """Calling the wrapped tool's run invokes the transform_fn."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation") as mock_validate,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._convert_labels", new_callable=AsyncMock
            ) as mock_labels,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_array_response", return_value=False
            ),
        ):
            from fastmcp.tools.base import ToolResult

            mock_run.return_value = ToolResult(
                structured_content={"result": "ok"},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]

            output = await wrapped.run(arguments={"key": "value"})

            mock_validate.assert_called_once()
            mock_labels.assert_called_once()
            mock_run.assert_called_once()
            assert output.structured_content == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_validation_error_blocks_execution(self):
        """Validation error prevents tool execution."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        with patch("gitea_mcp_server.server_setup.mcp_builder._run_validation") as mock_validate:
            from gitea_mcp_server.validation import ValidationError

            mock_validate.side_effect = ValidationError("Bad input", field="name")

            result = await transform.list_tools([tool])
            wrapped = result[0]

            with pytest.raises(ValueError, match="Bad input"):
                await wrapped.run(arguments={"name": ""})

    @pytest.mark.asyncio
    async def test_label_conversion_error_to_value_error(self):
        """ValidationError from _convert_labels is converted to ValueError."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        tool.meta["_customization"]["has_labels"] = True

        from gitea_mcp_server.validation import ValidationError

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._convert_labels", new_callable=AsyncMock
            ) as mock_labels,
        ):
            mock_labels.side_effect = ValidationError("Unknown label: foo", field="labels")

            result = await transform.list_tools([tool])
            wrapped = result[0]

            with pytest.raises(ValueError, match="Unknown label"):
                await wrapped.run(arguments={"labels": ["foo"]})

    @pytest.mark.asyncio
    async def test_text_response_wrapping(self):
        """is_text_response wraps unstructured content in result dict."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        tool.meta["_customization"]["is_text_response"] = True

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._convert_labels", new_callable=AsyncMock
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from fastmcp.tools.base import ToolResult
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="raw text")],
                structured_content=None,
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

            assert output.structured_content == {"result": "raw text"}

    @pytest.mark.asyncio
    async def test_array_pagination_injection(self):
        """Array responses get pagination metadata."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        tool.output_schema = {
            "type": "object",
            "properties": {
                "result": {"type": "array", "items": {"type": "object"}},
            },
        }

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._convert_labels", new_callable=AsyncMock
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from fastmcp.tools.base import ToolResult
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="[item]")],
                structured_content={"result": [{"id": 1}], "has_more": False},
            )

            from gitea_mcp_server.pagination import pagination_ctx

            pagination_ctx.set({"total_count": 1})

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={"page": 1})

            assert output.structured_content["has_more"] is False
            assert output.structured_content["total_count"] == 1

            pagination_ctx.set({})
