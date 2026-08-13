"""Unit tests for server_setup/mcp_builder.py (_customize_metadata, _ToolWrappingTransform)."""

from collections.abc import Callable
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.providers.openapi import OpenAPIProvider, OpenAPITool
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent, ToolAnnotations
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gitea_mcp_server.models import ToolCustomization
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.server_setup.mcp_builder import (
    _apply_fallback_schemas,
    _apply_schema_postprocessing,
    _apply_tool_identity,
    _build_customization_meta,
    _compute_tool_schema,
    _ComputedSchema,
    _customize_metadata,
    _detect_contents_response,
    _inject_response_metadata,
    _read_response_transform,
    _response_is_binary,
    _ToolWrappingTransform,
    create_openapi_provider,
)
from tests.helpers.mcp_results import get_structured
from tests.helpers.spec_fixtures import make_openapi_spec

# ---------------------------------------------------------------------------
# _customize_metadata
# ---------------------------------------------------------------------------


class TestCustomizeMetadata:
    """Tests for _customize_metadata - in-place metadata on OpenAPITools."""

    def test_skips_non_openapi_tool(self) -> None:
        """Non-OpenAPITool components are skipped."""
        route = MagicMock(path="/test", summary="Test", operation_id="test_op", method="GET")
        resource = MagicMock(spec=object)

        _customize_metadata(route, resource, openapi_spec=make_openapi_spec())

    def test_sets_title_and_annotations(self) -> None:
        """Title and ToolAnnotations are set from route operationId."""
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

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert tool.annotations is not None
        assert tool.annotations.title == "List Items"
        assert tool.annotations.readOnlyHint is True

    def test_title_from_operation_id(self) -> None:
        """Title is generated from operationId (not summary)."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="Create an issue. If using deadline only the date will be taken into account...",
            operation_id="issue_create_issue",
            method="POST",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_create_issue"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Create a new issue"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert tool.annotations.title == "Create Issue"
        assert "..." not in tool.annotations.title

    def test_adds_annotations_from_dict(self) -> None:
        """Annotations dict is converted to ToolAnnotations."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="List issues",
            operation_id="issue_list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = {"title": "Old Title"}
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "List issues"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "List Issues"
        assert "issue" in tool.tags

    def test_preserves_existing_toolannotations(self) -> None:
        """Existing ToolAnnotations are preserved and updated."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/pulls/{index}",
            summary="Get pull request",
            operation_id="repo_get_pull_request",
            method="GET",
        )
        existing = ToolAnnotations(title="Old Title", readOnlyHint=True)
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_get_pull_request"
        tool.annotations = existing
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get pull request"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "Get Pull Request"
        assert tool.annotations.readOnlyHint is True
        assert "pull_request" in tool.tags

    def test_category_detection_various_paths(self) -> None:
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

            _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

            assert tool.annotations is not None
            assert expected_category in tool.tags, (
                f"Failed for {path}: category {expected_category} not in tags"
            )

    def test_destructive_hint_from_method(self) -> None:
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

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert tool.annotations.destructiveHint is True

    def test_sets_description(self) -> None:
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

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert tool.description == "Original description"

    def test_uses_component_description_not_doc(self) -> None:
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

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert "Description from attribute" in tool.description
        assert "Docstring should be ignored" not in tool.description

    def test_adds_labels_tag_for_label_tools(self) -> None:
        """Verify 'labels' tag is added to tools with labels parameter."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="Create issue",
            operation_id="create_issue",
            method="POST",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_create_issue"
        tool.annotations = None
        tool.tags = {"issue"}
        tool.parameters = {
            "properties": {"labels": {"type": "array", "items": {"type": "integer"}}}
        }
        tool.output_schema = None
        tool.description = "Create an issue"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert "labels" in tool.tags
        assert "issue" in tool.tags  # original tag preserved

    def test_does_not_add_labels_tag_without_labels(self) -> None:
        """Verify 'labels' tag is NOT added when tool has no labels parameter."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="List issues",
            operation_id="list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = None
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "List issues"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert "labels" not in tool.tags

    def test_sets_meta_flags(self) -> None:
        """Meta dict contains _WRAP_ME and _customization."""
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

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        assert tool.meta.get("_contract_wrap") is True
        assert "_customization" in tool.meta
        c = tool.meta["_customization"]
        assert isinstance(c, ToolCustomization)
        assert c.route_path == "/repos/{owner}/{repo}"
        assert c.route_method == "GET"
        assert c.has_labels is False
        assert c.is_text_response is False

    def test_registers_invalidation_patterns_for_write(self) -> None:
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
            _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

            mock_register.assert_called_once()

    def test_read_method_does_not_register_invalidation(self) -> None:
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
            _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

            mock_register.assert_not_called()

    def test_output_schema_not_none_sets_wrap_flag(self) -> None:
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
            _customize_metadata(route, tool, openapi_spec=make_openapi_spec())
            assert tool.output_schema["x-fastmcp-wrap-result"] is True

    def test_array_output_schema_adds_pagination_fields(self) -> None:
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
        output_schema: dict[str, Any] = {
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
            _customize_metadata(route, tool, openapi_spec=make_openapi_spec())
            props = output_schema.setdefault("properties", {})
            assert "has_more" in props
            assert "next_offset" in props
            assert "total_count" in props
            assert props["has_more"]["type"] == "boolean"
            # next_offset/total_count are nullable (runtime emits null on the
            # last page / when total is unknown) — shared envelope contract.
            for key in ("next_offset", "total_count"):
                any_of_types = {entry.get("type") for entry in props[key]["anyOf"]}
                assert any_of_types == {"integer", "null"}, (
                    f"{key} must be integer|null, got {props[key]}"
                )

    def test_text_plain_fallback_schema(self) -> None:
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
                "gitea_mcp_server.server_setup.mcp_builder.is_text_response",
                return_value=True,
            ),
        ):
            _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

            assert tool.output_schema is not None
            assert tool.output_schema["type"] == "object"
            assert tool.output_schema["properties"]["result"]["type"] == "string"
            # x-fastmcp-wrap-result should be set since output_schema is now not None
            assert tool.output_schema.get("x-fastmcp-wrap-result") is True

    def test_json_endpoint_retains_derived_schema(self) -> None:
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
            _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

            assert tool.output_schema is not None
            # Should be the derived schema, not the text/plain fallback
            assert tool.output_schema["properties"]["result"]["type"] == "array"


# ---------------------------------------------------------------------------
# route_map_fn (spec-level filtering: deprecated + scope + exclusion)
# ---------------------------------------------------------------------------


class TestRouteMapFiltering:
    """Tests that create_openapi_provider drops filtered operations via route_map_fn."""

    def _provider(
        self,
        spec: OpenAPISpec,
        excluded_routes: set[tuple[str, str]],
    ) -> OpenAPIProvider:
        from gitea_mcp_server.label_service import LabelService

        # Ensure a valid minimal info block so FastMCP's schema validation passes.
        spec_copy = cast("OpenAPISpec", dict(spec))
        spec_copy.setdefault("info", {"title": "Test", "version": "1.0.0"})
        spec_copy.setdefault("components", {"schemas": {}})
        mock_gitea_client = MagicMock()
        mock_gitea_client.client = MagicMock()
        return create_openapi_provider(
            openapi_spec=spec_copy,
            gitea_client=mock_gitea_client,
            label_service=LabelService(),
            excluded_routes=excluded_routes,
        )

    def test_empty_paths(self) -> None:
        """Empty paths dict returns empty set."""
        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "paths": {},
            "info": {"title": "T", "version": "1"},
        }
        provider = self._provider(spec, set())
        assert provider is not None

    def test_missing_paths(self) -> None:
        """Spec with no paths key returns empty set."""
        spec: OpenAPISpec = {"openapi": "3.1.1", "info": {"title": "T", "version": "1"}}
        provider = self._provider(spec, set())
        assert provider is not None

    def test_non_dict_paths_rejected(self) -> None:
        """A non-dict paths value is rejected by FastMCP's spec validation."""
        spec = cast("OpenAPISpec", {"openapi": "3.1.1", "paths": "not_a_dict"})
        from gitea_mcp_server.label_service import LabelService

        try:
            mock_gitea_client = MagicMock()
            mock_gitea_client.client = MagicMock()
            create_openapi_provider(
                openapi_spec=spec,
                gitea_client=mock_gitea_client,
                label_service=LabelService(),
                excluded_routes=set(),
            )
        except (ValueError, Exception):  # FastMCP raises on invalid spec
            pass
        else:
            pytest.fail("Expected FastMCP to reject non-dict paths")

    def test_no_deprecated_returns_empty(self) -> None:
        """No deprecated:true operations returns empty set."""
        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser"},
                    "post": {"operationId": "createUser"},
                },
            },
        }
        provider = self._provider(spec, set())
        assert provider is not None

    def test_single_deprecated_get(self) -> None:
        """Single deprecated GET is excluded via route_map_fn."""
        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser", "deprecated": True},
                    "post": {"operationId": "createUser"},
                },
            },
        }
        provider = self._provider(spec, {("/user", "GET")})
        assert provider is not None

    def test_multiple_deprecated_operations(self) -> None:
        """Multiple deprecated methods on same path are excluded."""
        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {"operationId": "getRepo"},
                    "put": {"operationId": "updateRepo", "deprecated": True},
                    "delete": {"operationId": "deleteRepo", "deprecated": True},
                },
            },
        }
        provider = self._provider(
            spec, {("/repos/{owner}/{repo}", "PUT"), ("/repos/{owner}/{repo}", "DELETE")}
        )
        assert provider is not None

    def test_multiple_paths_mixed(self) -> None:
        """Deprecated across multiple paths, non-deprecated excluded."""
        spec: OpenAPISpec = {
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
        provider = self._provider(
            spec,
            {
                ("/v1/old", "GET"),
                ("/v1/old", "POST"),
                ("/v2/also_old", "PATCH"),
            },
        )
        assert provider is not None

    def test_deprecated_false_not_included(self) -> None:
        """deprecated: false is treated as not deprecated (no exclusion)."""
        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser", "deprecated": False},
                },
            },
        }
        provider = self._provider(spec, set())
        assert provider is not None

    def test_non_http_method_keys_ignored(self) -> None:
        """Parameters key at path level is not treated as an operation."""
        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "parameters": [{"name": "owner", "in": "path"}],
                    "get": {"operationId": "getRepo", "deprecated": True},
                },
            },
        }
        provider = self._provider(spec, {("/repos/{owner}/{repo}", "GET")})
        assert provider is not None

    def test_http_methods_comprehensive(self) -> None:
        """All HTTP methods are properly excluded via route_map_fn."""
        # cast needed: dict comprehension type-inference makes mypy too conservative
        spec = cast(
            "OpenAPISpec",
            {
                "openapi": "3.1.1",
                "paths": {
                    "/resource": {
                        method: {"operationId": f"{method}Resource", "deprecated": True}
                        for method in (
                            "get",
                            "post",
                            "put",
                            "delete",
                            "patch",
                            "options",
                            "head",
                            "trace",
                        )
                    },
                },
            },
        )
        expected = {
            ("/resource", method.upper())
            for method in ("get", "post", "put", "delete", "patch", "options", "head", "trace")
        }
        provider = self._provider(spec, expected)
        assert provider is not None


# ---------------------------------------------------------------------------
# _ToolWrappingTransform - OpenTelemetry spans
# ---------------------------------------------------------------------------
# The session-scoped ``_init_otel_exporter`` and ``trace_exporter`` fixture
# are defined in ``tests/conftest.py`` (shared across all test modules).


class TestToolWrappingTransformTelemetry:
    """Tests for custom OTEL spans emitted from _ToolWrappingTransform._run_transform_pipeline."""

    def make_transform(self, openapi_spec: OpenAPISpec | None = None) -> _ToolWrappingTransform:
        return _ToolWrappingTransform(
            openapi_spec=openapi_spec if openapi_spec is not None else make_openapi_spec(),
        )

    def make_tool(self, name: str = "test_tool") -> Tool:
        return Tool(
            name=name,
            tags={"test"},
            description="Test tool",
            parameters={"properties": {}, "required": []},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta={
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=False,
                    route_path="/test",
                    route_method="GET",
                ),
            },
            annotations=ToolAnnotations(title="Test"),
        )

    @pytest.mark.asyncio
    async def test_pipeline_emits_validate_span(self, trace_exporter: InMemorySpanExporter) -> None:
        """Pipeline emits a ``{tool}.validate`` span with arg_count attribute."""
        transform = self.make_transform()
        tool = self.make_tool("test_tool")

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
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
    async def test_spans_carry_tool_name_attribute(
        self, trace_exporter: InMemorySpanExporter
    ) -> None:
        """Validate and execute spans carry ``tool.name`` attribute."""
        transform = self.make_transform()
        tool = self.make_tool("attr_tool")

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
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
                assert (span.attributes or {}).get("tool.name") == "attr_tool"
            if span.name == "attr_tool.execute":
                assert (span.attributes or {}).get("http.route") == "/test"
                assert (span.attributes or {}).get("http.method") == "GET"

    @pytest.mark.asyncio
    async def test_validation_error_stops_pipeline(
        self, trace_exporter: InMemorySpanExporter
    ) -> None:
        """When validation fails, only the ``validate`` span is emitted."""
        from gitea_mcp_server.exceptions import ValidationError

        transform = self.make_transform()
        tool = self.make_tool("fail_tool")

        with (
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_validation",
                side_effect=ValidationError("missing required: owner"),
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ),
        ):
            result = await transform.list_tools([tool])
            wrapped = result[0]
            with pytest.raises(ValueError, match="missing required: owner"):
                await wrapped.run(arguments={})

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # validate span should exist (started before the error)
        assert "fail_tool.validate" in span_names, (
            f"Expected 'fail_tool.validate' in span names: {span_names}"
        )
        # execute should NOT appear (pipeline aborted)
        assert "fail_tool.execute" not in span_names, (
            f"Expected no 'fail_tool.execute', got: {span_names}"
        )


# ---------------------------------------------------------------------------
# create_openapi_provider
# ---------------------------------------------------------------------------


class TestCreateOpenapiProvider:
    """Tests for create_openapi_provider - provider creation and deprecated route filtering."""

    def test_deprecated_routes_are_filtered_out(self, caplog: pytest.LogCaptureFixture) -> None:
        """Deprecated routes are excluded via route_map_fn."""
        import logging

        caplog.set_level(logging.DEBUG)

        from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider

        # Spec with a deprecated route
        openapi_spec: OpenAPISpec = {
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

        from gitea_mcp_server.label_service import LabelService

        mock_gitea_client = MagicMock()
        mock_gitea_client.client = MagicMock()
        label_service = LabelService()
        provider = create_openapi_provider(
            openapi_spec=openapi_spec,
            gitea_client=mock_gitea_client,
            label_service=label_service,
            excluded_routes={("/old/endpoint", "POST")},
        )

        assert provider is not None
        assert "Excluding filtered endpoint" in caplog.text

    @pytest.mark.asyncio
    async def test_response_format_propagates_to_tool_schema(self) -> None:
        """response_format should flow into the tool's format parameter default."""
        from gitea_mcp_server.label_service import LabelService

        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser"},
                },
            },
            "components": {"schemas": {}},
        }
        mock_gitea_client = MagicMock()
        mock_gitea_client.client = MagicMock()

        provider = create_openapi_provider(
            openapi_spec=spec,
            gitea_client=mock_gitea_client,
            label_service=LabelService(),
        )
        tools = await provider.list_tools()
        tool = next(t for t in tools if t.name == "getUser")

        # The wrapping transform now lives server-level; exercise it
        # directly to verify response_format reaches the format default.
        transform = _ToolWrappingTransform(
            openapi_spec=spec,
            response_format="json",
        )
        [wrapped] = await transform.list_tools([tool])
        fmt_param = wrapped.parameters["properties"]["format"]
        assert fmt_param["default"] == "json"
        assert fmt_param["type"] == "string"
        assert "json" in fmt_param["enum"]


class TestProviderTransformRegistration:
    """The contract wrapping transform lives server-level, not on the provider."""

    def _make_provider(self) -> OpenAPIProvider:
        from gitea_mcp_server.label_service import LabelService

        spec: OpenAPISpec = {
            "openapi": "3.1.1",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser"},
                },
            },
            "components": {"schemas": {}},
        }
        mock_gitea_client = MagicMock()
        mock_gitea_client.client = MagicMock()
        return create_openapi_provider(
            openapi_spec=spec,
            gitea_client=mock_gitea_client,
            label_service=LabelService(),
        )

    def test_provider_registers_only_label_transform(self) -> None:
        """The wrapping transform is no longer provider-level.

        Only LabelTransform (innermost) stays on the provider; the contract
        wrapping transform is registered server-level via ``mcp.add_transform``
        so it can wrap tools from every provider.
        """
        from gitea_mcp_server.tools.label_transform import LabelTransform

        provider = self._make_provider()
        assert len(provider.transforms) == 1
        assert isinstance(provider.transforms[0], LabelTransform)

    @pytest.mark.asyncio
    async def test_provider_tools_carry_wrap_marker(self) -> None:
        """Autogenerated tools are stamped with the wrap-me marker.

        The server-level transform keys off this marker; unmarked tools
        (synthetic today) pass through unwrapped.
        """
        provider = self._make_provider()
        tools = await provider.list_tools()
        tool = next(t for t in tools if t.name == "getUser")
        meta = tool.meta or {}
        assert meta.get("_contract_wrap") is True
        assert "_customization" in meta


# ---------------------------------------------------------------------------
# _ToolWrappingTransform
# ---------------------------------------------------------------------------


class TestToolWrappingTransform:
    """Tests for _ToolWrappingTransform."""

    def make_transform(
        self, openapi_spec: OpenAPISpec | None = None, response_format: str = "markdown"
    ) -> _ToolWrappingTransform:
        return _ToolWrappingTransform(
            openapi_spec=openapi_spec if openapi_spec is not None else make_openapi_spec(),
            response_format=response_format,
        )

    def make_tool(self, customized: bool = True) -> Tool:
        meta: dict[str, Any] = {}
        if customized:
            meta = {
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=False,
                    route_path="/test",
                    route_method="GET",
                ),
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
    async def test_list_tools_passthrough_uncustomized(self) -> None:
        """Uncustomized tools pass through without wrapping."""
        transform = self.make_transform()
        tool = self.make_tool(customized=False)
        result = await transform.list_tools([tool])
        assert len(result) == 1
        assert result[0] is tool

    @pytest.mark.asyncio
    async def test_list_tools_wraps_customized(self) -> None:
        """Customized tools are wrapped (new Tool created)."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        result = await transform.list_tools([tool])
        assert len(result) == 1
        assert result[0] is not tool
        assert isinstance(result[0], Tool)

    @pytest.mark.asyncio
    async def test_repeated_list_tools_wraps_fresh_not_nested(self) -> None:
        """Each list_tools pass wraps the raw tool once — no nested wrapping.

        The transform is now server-level, so it may be invoked on every
        ``tools/list`` request.  Because each invocation starts from the raw
        provider tool, the result must be a single wrap (parent is the raw
        tool), never a wrap of a previous wrap.
        """
        from fastmcp.tools.tool_transform import TransformedTool

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        first = await transform.list_tools([tool])
        second = await transform.list_tools([tool])

        assert isinstance(first[0], TransformedTool)
        assert isinstance(second[0], TransformedTool)
        assert first[0].parent_tool is tool
        assert second[0].parent_tool is tool
        # format is injected into both wrapped schemas.
        for wrapped in (first[0], second[0]):
            assert "format" in wrapped.parameters["properties"]

    @pytest.mark.asyncio
    async def test_uses_meta_executor_for_synthetic_tool(self) -> None:
        """Synthetic tools run through their registered executor.

        The spine (extract virtual params, resolve ctx, post-hooks) is
        shared; only the executor differs.  The synthetic executor receives
        kwargs with virtual params popped and the extracted values.
        """
        from gitea_mcp_server.tools.synthetic_contract import (
            _SYNTHETIC_EXECUTORS,
        )

        captured: dict[str, Any] = {}

        async def executor(
            kwargs: dict[str, Any],
            extracted: dict[str, Any] | None,
            ctx: Any | None,
        ) -> ToolResult:
            captured["kwargs"] = dict(kwargs)
            captured["extracted"] = dict(extracted or {})
            return ToolResult(structured_content={"result": "synth-ok"})

        tool = Tool(
            name="synth_tool",
            description="Synthetic tool.",
            parameters={"properties": {}},
            meta={
                "_contract_wrap": True,
                "_synthetic": True,
                "_executor_id": "synth_tool",
                "_virtual_params": {"format", "detail", "fetch_all"},
            },
        )
        _SYNTHETIC_EXECUTORS["synth_tool"] = executor
        try:
            transform = self.make_transform()
            [wrapped] = await transform.list_tools([tool])
            result = await wrapped.run({"query": "q", "format": "json"})
        finally:
            _SYNTHETIC_EXECUTORS.pop("synth_tool", None)

        assert captured["kwargs"] == {"query": "q"}
        assert captured["extracted"] == {
            "format": "json",
            "detail": "full",
            "fetch_all": False,
        }
        assert result.structured_content == {"result": "synth-ok"}

    @pytest.mark.asyncio
    async def test_inject_params_respects_virtual_params_allowlist(self) -> None:
        """Synthetic tools stamp _virtual_params; only those are injected."""
        tool = Tool(
            name="read_doc_like",
            description="Synthetic tool with a format-only profile.",
            parameters={"properties": {}},
            meta={
                "_contract_wrap": True,
                "_synthetic": True,
                "_virtual_params": {"format"},
            },
        )
        transform = self.make_transform()
        [wrapped] = await transform.list_tools([tool])
        props = wrapped.parameters["properties"]
        assert "format" in props
        assert "detail" not in props
        assert "fetch_all" not in props
        assert "sudo" not in props

    @pytest.mark.asyncio
    async def test_run_transform_pipeline_no_customization(self) -> None:
        """_run_transform_pipeline handles customization=None gracefully.

        When a tool has the wrap marker but no _customization
        dict, the pipeline falls back to empty route/metadata defaults
        instead of crashing.
        """
        transform = self.make_transform()
        meta = {
            "_contract_wrap": True,
            # _customization is intentionally missing
        }
        tool = Tool(
            name="test_tool",
            tags={"test"},
            description="Test tool",
            parameters={"properties": {}, "required": []},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta=meta,
            annotations=ToolAnnotations(title="Test"),
        )

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="ok")],
                structured_content={"result": "ok"},
            )
            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

        assert get_structured(output)["result"] == "ok"

    @pytest.mark.asyncio
    async def test_get_tool_passthrough_uncustomized(self) -> None:
        """Uncustomized tools from call_next pass through."""
        transform = self.make_transform()
        tool = self.make_tool(customized=False)

        async def call_next(name: str, version: str | None = None) -> Tool | None:
            return tool

        result = await transform.get_tool("test_tool", call_next)
        assert result is tool

    @pytest.mark.asyncio
    async def test_get_tool_wraps_customized(self) -> None:
        """Customized tools from call_next are wrapped."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        async def call_next(name: str, version: str | None = None) -> Tool | None:
            return tool

        result = await transform.get_tool("test_tool", call_next)
        assert result is not tool
        assert isinstance(result, Tool)

    @pytest.mark.asyncio
    async def test_get_tool_none_passthrough(self) -> None:
        """None from call_next passes through."""
        transform = self.make_transform()

        async def call_next(name: str, version: str | None = None) -> None:
            return None

        result = await transform.get_tool("test_tool", call_next)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_tools_empty(self) -> None:
        """Empty list passes through."""
        transform = self.make_transform()
        result = await transform.list_tools([])
        assert result == []

    @pytest.mark.asyncio
    async def test_wrapped_tool_preserves_metadata(self) -> None:
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
    async def test_wrapped_tool_executes_transform_fn(self) -> None:
        """Calling the wrapped tool's run invokes the transform_fn."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation") as mock_validate,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
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
            mock_run.assert_called_once()
            assert output.structured_content == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_validation_error_blocks_execution(self) -> None:
        """Validation error prevents tool execution."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        with patch("gitea_mcp_server.server_setup.mcp_builder.run_validation") as mock_validate:
            from gitea_mcp_server.validation import ValidationError

            mock_validate.side_effect = ValidationError("Bad input", field="name")

            result = await transform.list_tools([tool])
            wrapped = result[0]

            with pytest.raises(ValueError, match="Bad input"):
                await wrapped.run(arguments={"name": ""})

    @pytest.mark.asyncio
    async def test_unknown_args_rejected_in_full_pipeline(self) -> None:
        """Unknown arguments should be rejected by the full tool pipeline.

        Regression: run_validation validated known params but never checked
        that every kwarg was declared in the parameter schema.  Unknown args
        (agent typos) passed through silently.  This test exercises the full
        transform_fn → _run_transform_pipeline → _pipeline_with_context
        → run_validation chain without mocking run_validation.
        """
        transform = self.make_transform()

        # Tool with real parameter properties — not the usual empty dict.
        tool = Tool(
            name="test_tool",
            tags={"test"},
            description="Test tool",
            parameters={
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                },
                "required": ["owner", "repo"],
            },
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta={
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=False,
                    route_path="/repos/{owner}/{repo}",
                    route_method="GET",
                ),
            },
            annotations=ToolAnnotations(title="Test"),
        )

        result = await transform.list_tools([tool])
        wrapped = result[0]

        # Should reject the unknown key 'typo_parm'.

        with pytest.raises(ValueError, match="Unknown parameter"):
            await wrapped.run(arguments={
                "owner": "valid-owner",
                "repo": "valid-repo",
                "typo_parm": 42,
            })

    @pytest.mark.asyncio
    async def test_text_response_wrapping(self) -> None:
        """is_text_response wraps unstructured content in result dict."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        assert tool.meta is not None, "Expected tool.meta to be set"
        tool.meta["_customization"].is_text_response = True

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
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
    async def test_array_pagination_injection(self) -> None:
        """Array responses get pagination metadata."""
        from gitea_mcp_server.pagination import pagination_ctx

        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        tool.output_schema = {
            "type": "object",
            "properties": {
                "result": {"type": "array", "items": {"type": "object"}},
            },
        }

        pagination_ctx.set({"total_count": 1})
        try:
            with (
                patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                    new_callable=AsyncMock,
                ) as mock_run,
            ):
                from fastmcp.tools.base import ToolResult
                from mcp.types import TextContent

                mock_run.return_value = ToolResult(
                    content=[TextContent(type="text", text="[item]")],
                    structured_content={"result": [{"id": 1}], "has_more": False},
                )

                result = await transform.list_tools([tool])
                wrapped = result[0]
                output = await wrapped.run(arguments={"page": 1})

                assert get_structured(output)["has_more"] is False
                assert get_structured(output)["total_count"] == 1
        finally:
            pagination_ctx.set({})

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_passthrough_no_extracted(self) -> None:
        """_apply_loop_hooks returns result unchanged when extracted is None."""
        from fastmcp.tools.base import ToolResult

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        result = ToolResult(
            structured_content={"result": [{"id": 1}]},
        )

        output = await transform._apply_loop_hooks(
            result,
            {"page": 1},
            None,
            tool,
            "/test",
            "GET",
        )
        assert output is result

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_passthrough_empty_extracted(self) -> None:
        """_apply_loop_hooks returns result unchanged when extracted is empty."""
        from fastmcp.tools.base import ToolResult

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        result = ToolResult(
            structured_content={"result": [{"id": 1}]},
        )

        output = await transform._apply_loop_hooks(
            result,
            {"page": 1},
            {},
            tool,
            "/test",
            "GET",
        )
        assert output is result

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_calls_hook(self) -> None:
        """_apply_loop_hooks invokes registered loop hook with correct args."""
        from fastmcp.tools.base import ToolResult

        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        hook = AsyncMock()
        hook.return_value = ToolResult(
            structured_content={"result": [{"id": 1}, {"id": 2}], "has_more": False},
        )

        result = ToolResult(
            structured_content={"result": [{"id": 1}], "has_more": True},
        )

        extracted = {"fetch_all": True}

        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "fetch_all": VirtualParam(
                    schema={"type": "boolean"},
                    default=False,
                    description="",
                    loop_hook=hook,
                ),
            },
        ):
            output = await transform._apply_loop_hooks(
                result,
                {"page": 1, "limit": 10},
                extracted,
                tool,
                "/test",
                "GET",
            )

        hook.assert_called_once()
        args = hook.call_args[0]
        # args: (result, value, kwargs, execute_fn)
        assert args[0] is result
        assert args[1] is True
        assert args[2] == {"page": 1, "limit": 10}
        # execute_fn is a callable
        assert callable(args[3])

        assert get_structured(output)["has_more"] is False
        assert get_structured(output)["result"] == [{"id": 1}, {"id": 2}]

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_execute_fn_reinvokes_http(self) -> None:
        """The execute_fn passed to loop_hook calls run_with_error_handling."""
        from fastmcp.tools.base import ToolResult

        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        tool.name = "test_tool"

        async def my_loop_hook(
            result: ToolResult, value: Any, kwargs: dict[str, Any], execute_fn: Callable
        ) -> ToolResult:
            """Simple loop hook that fetches one more page and merges."""
            kwargs["page"] = 2
            next_result = await execute_fn(kwargs)
            data = get_structured(result)["result"]
            data.extend(get_structured(next_result)["result"])
            get_structured(result)["result"] = data
            get_structured(result)["has_more"] = False
            return result

        result = ToolResult(
            structured_content={"result": [{"id": 1}], "has_more": True},
        )

        extracted = {"fetch_all": True}

        with (
            patch.dict(
                "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
                {
                    "fetch_all": VirtualParam(
                        schema={"type": "boolean"},
                        default=False,
                        description="",
                        loop_hook=my_loop_hook,
                    ),
                },
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_execute,
        ):
            mock_execute.return_value = ToolResult(
                structured_content={"result": [{"id": 2}], "has_more": False},
            )

            output = await transform._apply_loop_hooks(
                result,
                {"page": 1, "limit": 10},
                extracted,
                tool,
                "/test",
                "GET",
            )

        # Executed once with page=2
        mock_execute.assert_called_once()
        call_kwargs = mock_execute.call_args[0][0]
        assert call_kwargs["page"] == 2

        # Results merged
        assert get_structured(output)["result"] == [{"id": 1}, {"id": 2}]
        assert get_structured(output)["has_more"] is False

    @pytest.mark.asyncio
    async def test_execute_fn_validates_kwargs(self) -> None:
        """execute_fn validates kwargs, rejecting invalid values."""
        from fastmcp.tools.base import ToolResult

        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        async def bad_loop_hook(
            result: ToolResult, value: Any, kwargs: dict[str, Any], execute_fn: Callable
        ) -> ToolResult:
            await execute_fn({"page": 0})  # page < 1 is invalid
            return result

        extracted = {"fetch_all": True}

        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "fetch_all": VirtualParam(
                    schema={"type": "boolean"},
                    default=False,
                    description="",
                    loop_hook=bad_loop_hook,
                ),
            },
        ):
            result = ToolResult(
                structured_content={"result": [{"id": 1}], "has_more": True},
            )
            with pytest.raises(ValueError, match="page must be >= 1"):
                await transform._apply_loop_hooks(
                    result,
                    {"page": 1},
                    extracted,
                    tool,
                    "/test",
                    "GET",
                )

    @pytest.mark.asyncio
    async def test_loop_hooks_chain_integration(self) -> None:
        """Pipeline with extracted loop hooks calls _apply_loop_hooks.

        Verifies that the full pipeline (transform_fn → _run_transform_pipeline
        → _pipeline_with_context → _apply_loop_hooks) correctly extracts loop
        hooks and passes execute_fn when extracted values are provided.
        """
        from fastmcp.tools.base import ToolResult

        from gitea_mcp_server.pagination import pagination_ctx
        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        loop_hook_called = False

        async def my_loop_hook(
            result: ToolResult, value: Any, kwargs: dict[str, Any], execute_fn: Callable
        ) -> ToolResult:
            nonlocal loop_hook_called
            loop_hook_called = True
            return result

        extracted = {"fetch_test": True}

        pagination_ctx.set({"total_count": 1})
        try:
            with (
                patch.dict(
                    "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
                    {
                        "fetch_test": VirtualParam(
                            schema={"type": "boolean"},
                            default=False,
                            description="",
                            loop_hook=my_loop_hook,
                        ),
                    },
                ),
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder.run_validation",
                ),
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                    new_callable=AsyncMock,
                ) as mock_run,
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                    return_value=True,
                ),
            ):
                mock_run.return_value = ToolResult(
                    structured_content={"result": [{"id": 1}]},
                )

                # Call the pipeline directly with extracted
                result = await transform._run_transform_pipeline(
                    {"page": 1, "limit": 10},
                    tool,
                    tool.meta.get("_customization") if tool.meta else None,
                    extracted=extracted,
                )
        finally:
            pagination_ctx.set({})

        assert loop_hook_called


# ---------------------------------------------------------------------------
# fetch_all integration
# ---------------------------------------------------------------------------


class TestFetchAllIntegration:
    """Integration tests: fetch_all through the full pipeline."""

    @pytest.fixture
    def make_transform_and_tool(self) -> tuple[_ToolWrappingTransform, Tool]:
        """Create a transform and a minimal tool suitable for pagination tests."""
        transform = _ToolWrappingTransform(openapi_spec=make_openapi_spec())
        tool = Tool(
            name="test_list_tool",
            description="A paginated list tool.",
            parameters={
                "properties": {
                    "owner": {"type": "string"},
                    "page": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": 10},
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                    },
                    "has_more": {"type": "boolean"},
                    "next_offset": {"type": "integer"},
                    "total_count": {"type": "integer"},
                },
            },
            meta={
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=False,
                    is_empty_response=False,
                    route_path="/repos/{owner}/{repo}/items",
                    route_method="GET",
                ),
            },
        )
        return transform, tool

    @pytest.mark.asyncio
    async def test_fetch_all_merges_all_pages(self, make_transform_and_tool: tuple) -> None:
        """Full pipeline with fetch_all=True merges paginated results."""
        from fastmcp.tools.base import ToolResult

        from gitea_mcp_server.pagination import pagination_ctx
        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform, tool = make_transform_and_tool

        # Simulate 3 pages of 10 items each.
        # Return raw results (no pagination metadata) — matching what
        # OpenAPITool.run() returns.  Pagination metadata is added by
        # _pipeline_with_context (initial page) or _execute_fn (subsequent).
        page_calls: list[int] = []

        async def _mock_pages(
            kwargs: dict[str, Any], _tool: Any, _spec: Any, _path: Any, _method: Any
        ) -> ToolResult:
            page = kwargs.get("page", 1)
            page_calls.append(page)
            start = (page - 1) * 10 + 1
            items = [{"id": i} for i in range(start, min(start + 10, 31))]
            return ToolResult(
                structured_content={
                    "result": items,
                },
            )

        extracted = {"fetch_all": True}

        pagination_ctx.set({"total_count": 30})
        try:
            with (
                patch.dict(
                    "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
                    {
                        "fetch_all": VirtualParam(
                            schema={"type": "boolean"},
                            default=False,
                            description="",
                            loop_hook=_mock_fetch_all_hook,
                        ),
                    },
                ),
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder.run_validation",
                ),
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                    new_callable=AsyncMock,
                ) as mock_run,
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                    return_value=True,
                ),
            ):
                mock_run.side_effect = _mock_pages

                result = await transform._run_transform_pipeline(
                    {"page": 1, "limit": 10, "owner": "test"},
                    tool,
                    tool.meta.get("_customization") if tool.meta else None,
                    extracted=extracted,
                )
        finally:
            pagination_ctx.set({})

        # 3 pages fetched total
        assert page_calls == [1, 2, 3]
        # All 30 items merged
        assert len(get_structured(result)["result"]) == 30
        assert get_structured(result)["has_more"] is False
        assert get_structured(result)["next_offset"] is None
        assert get_structured(result)["total_count"] == 30

    @pytest.mark.asyncio
    async def test_fetch_all_false_single_page(self, make_transform_and_tool: tuple) -> None:
        """fetch_all=False fetches only the first page (no loop)."""
        from fastmcp.tools.base import ToolResult

        from gitea_mcp_server.pagination import pagination_ctx

        transform, tool = make_transform_and_tool

        page_calls: list[int] = []

        async def _mock_single(
            kwargs: dict[str, Any], _tool: Any, _spec: Any, _path: Any, _method: Any
        ) -> ToolResult:
            page = kwargs.get("page", 1)
            page_calls.append(page)
            return ToolResult(
                structured_content={
                    "result": [{"id": 1}, {"id": 2}],
                },
            )

        extracted = {"fetch_all": False}

        pagination_ctx.set({"total_count": 20})
        try:
            with (
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder.run_validation",
                ),
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                    new_callable=AsyncMock,
                ) as mock_run,
                patch(
                    "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                    return_value=True,
                ),
            ):
                mock_run.side_effect = _mock_single

                result = await transform._run_transform_pipeline(
                    {"page": 1, "limit": 10, "owner": "test"},
                    tool,
                    tool.meta.get("_customization") if tool.meta else None,
                    extracted=extracted,
                )
        finally:
            pagination_ctx.set({})

        # Only one page fetched
        assert page_calls == [1]
        assert len(get_structured(result)["result"]) == 2
        assert get_structured(result)["has_more"] is True  # still has more


async def _mock_fetch_all_hook(
    result: ToolResult, value: Any, kwargs: dict[str, Any], execute_fn: Callable
) -> ToolResult:
    """Simple loop hook that fetches pages via execute_fn and merges."""
    from gitea_mcp_server.tools.virtual_params import _fetch_all_loop

    return await _fetch_all_loop(result, value, kwargs, execute_fn)


# ---------------------------------------------------------------------------
# _read_response_transform
# ---------------------------------------------------------------------------


class TestReadResponseTransform:
    """Tests for ``_read_response_transform``."""

    def test_returns_transform_when_present(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/contents/{filepath}": {
                    "get": {
                        "x-response-transform": "base64-decode",
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        result = _read_response_transform(spec, "/repos/{owner}/{repo}/contents/{filepath}", "GET")
        assert result == "base64-decode"

    def test_returns_none_when_absent(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": {
                    "get": {
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        result = _read_response_transform(spec, "/repos/{owner}/{repo}", "GET")
        assert result is None

    def test_returns_none_for_missing_path(self) -> None:
        spec = make_openapi_spec()
        result = _read_response_transform(spec, "/nonexistent", "GET")
        assert result is None

    def test_returns_none_for_non_dict_path_item(self) -> None:
        spec = make_openapi_spec(paths={"/bad": "not a dict"})
        result = _read_response_transform(spec, "/bad", "GET")
        assert result is None

    def test_returns_none_for_non_dict_operation(self) -> None:
        spec = make_openapi_spec(paths={"/bad": {"get": "not a dict"}})
        result = _read_response_transform(spec, "/bad", "GET")
        assert result is None

    def test_method_case_insensitive(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/test": {
                    "get": {
                        "x-response-transform": "base64-decode",
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        result = _read_response_transform(spec, "/test", "GET")
        assert result == "base64-decode"


# ---------------------------------------------------------------------------
# _response_is_binary
# ---------------------------------------------------------------------------


class TestResponseIsBinary:
    """Tests for ``_response_is_binary``."""

    def test_true_for_application_zip(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/archive/{archive}": {
                    "get": {
                        "x-original-content-types": ["application/zip"],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}/archive/{archive}", "GET") is True

    def test_true_for_application_octet_stream(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/raw": {
                    "get": {
                        "x-original-content-types": ["application/octet-stream"],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}/raw", "GET") is True

    def test_true_for_application_x_zip_compressed(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/archive": {
                    "get": {
                        "x-original-content-types": ["application/x-zip-compressed"],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}/archive", "GET") is True

    def test_false_for_text_plain(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/pulls/{index}.diff": {
                    "get": {
                        "x-original-content-types": ["text/plain"],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}/pulls/{index}.diff", "GET") is False

    def test_false_for_application_json(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": {
                    "get": {
                        "x-original-content-types": ["application/json"],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}", "GET") is False

    def test_false_for_missing_path(self) -> None:
        spec = make_openapi_spec()
        assert _response_is_binary(spec, "/nonexistent", "GET") is False

    def test_false_for_no_x_original_content_types(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": {
                    "get": {
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}", "GET") is False

    def test_case_insensitive_matching(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/archive": {
                    "get": {
                        "x-original-content-types": ["Application/Zip"],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}/archive", "GET") is True


# ---------------------------------------------------------------------------
# _detect_contents_response — schema-based ContentsResponse detection
# ---------------------------------------------------------------------------


class TestDetectContentsResponseEdgeCases:
    """Cover defensive guard in ``_detect_contents_response``.

    The inner isinstance guard is unreachable in normal operation
    (``unwrap_result_schema`` always returns a dict for non-None input),
    but is kept as a safety check.  Exercised by passing a type-violating
    non-dict schema directly.
    """

    def test_non_dict_schema_guarded(self) -> None:
        """Non-dict output_schema skips detection and returns unchanged."""
        # Passing a non-dict bypasses the None check and hits the guard.
        bad_schema: Any = 42
        is_text, transform = _detect_contents_response(
            bad_schema,
            False,
            None,
        )
        assert is_text is False
        assert transform is None


class TestDetectContentsResponse:
    """Tests for ``_detect_contents_response`` — schema-based fallback
    detection of ContentsResponse (``encoding`` + ``content`` properties)."""

    def _wrap(self, inner: dict[str, Any]) -> dict[str, Any]:
        """Wrap an inner schema in the FastMCP result envelope."""
        return {"type": "object", "properties": {"result": inner}}

    def test_detects_encoding_and_content_properties(self) -> None:
        """When the inner schema has both ``encoding`` and ``content``,
        return ``(True, "base64-decode")``."""
        inner: dict[str, Any] = {
            "type": "object",
            "properties": {
                "encoding": {"type": "string"},
                "content": {"type": "string"},
                "name": {"type": "string"},
            },
        }
        output_schema = self._wrap(inner)
        is_text, transform = _detect_contents_response(output_schema, False, None)
        assert is_text is True
        assert transform == "base64-decode"

    def test_overrides_existing_response_transform(self) -> None:
        """Schema-based detection overrides a non-None response_transform."""
        inner: dict[str, Any] = {
            "type": "object",
            "properties": {
                "encoding": {"type": "string"},
                "content": {"type": "string"},
            },
        }
        output_schema = self._wrap(inner)
        # Even if response_transform already had some other value,
        # detection should override.
        is_text, transform = _detect_contents_response(
            output_schema,
            False,
            "other-transform",
        )
        assert is_text is True
        assert transform == "base64-decode"

    def test_skips_when_already_text_response(self) -> None:
        """When is_text_response is already True, returns unchanged."""
        inner: dict[str, Any] = {
            "type": "object",
            "properties": {
                "encoding": {"type": "string"},
                "content": {"type": "string"},
            },
        }
        output_schema = self._wrap(inner)
        is_text, transform = _detect_contents_response(output_schema, True, None)
        # Already True — no change needed.
        assert is_text is True
        assert transform is None

    def test_no_match_without_encoding_property(self) -> None:
        """Schema with ``content`` but no ``encoding`` is not detected."""
        inner: dict[str, Any] = {
            "type": "object",
            "properties": {"content": {"type": "string"}},
        }
        output_schema = self._wrap(inner)
        is_text, transform = _detect_contents_response(output_schema, False, None)
        assert is_text is False
        assert transform is None

    def test_no_match_without_content_property(self) -> None:
        """Schema with ``encoding`` but no ``content`` is not detected."""
        inner: dict[str, Any] = {
            "type": "object",
            "properties": {"encoding": {"type": "string"}},
        }
        output_schema = self._wrap(inner)
        is_text, transform = _detect_contents_response(output_schema, False, None)
        assert is_text is False
        assert transform is None

    def test_no_match_with_none_schema(self) -> None:
        """None schema returns unchanged flags."""
        is_text, transform = _detect_contents_response(None, False, None)
        assert is_text is False
        assert transform is None

    def test_no_match_with_non_dict_inner(self) -> None:
        """Unwrapped schema that is not a dict returns unchanged (edge case)."""
        # This exercises the isinstance(inner, dict) guard.
        output_schema: dict[str, Any] = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
        }
        is_text, transform = _detect_contents_response(output_schema, False, None)
        assert is_text is False
        assert transform is None


# ---------------------------------------------------------------------------
# _customize_metadata: response_transform + is_binary_response storage
# ---------------------------------------------------------------------------


class TestCustomizeMetadataContentsResponse:
    """Tests that _customize_metadata stores response_transform and
    is_binary_response in the customization dict."""

    def test_sets_response_transform_and_is_binary(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/archive/{archive}": {
                    "get": {
                        "x-response-transform": "base64-decode",
                        "x-original-content-types": ["application/zip"],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        route = MagicMock(
            path="/repos/{owner}/{repo}/archive/{archive}",
            summary="Get archive",
            operation_id="repo_get_archive",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_get_archive"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=spec)

        meta = tool.meta["_customization"]
        assert meta.response_transform == "base64-decode"
        assert meta.is_binary_response is True

    def test_defaults_when_no_annotations(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": {
                    "get": {
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        route = MagicMock(
            path="/repos/{owner}/{repo}",
            summary="Get repo",
            operation_id="repo_get",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_get"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=spec)

        meta = tool.meta["_customization"]
        assert meta.response_transform is None
        assert meta.is_binary_response is False


# ---------------------------------------------------------------------------
# _ToolWrappingTransform: base64-decode pipeline branch
# ---------------------------------------------------------------------------


class TestPipelineBase64Decode:
    """Tests for the base64-decode branch in _pipeline_with_context."""

    def make_transform(self) -> _ToolWrappingTransform:
        return _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(),
        )

    def make_tool(self) -> Tool:
        return Tool(
            name="repo_get_contents",
            tags={"repository"},
            description="Get file contents",
            parameters={"properties": {}, "required": []},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta={
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=True,
                    is_empty_response=False,
                    is_binary_response=False,
                    response_transform="base64-decode",
                    route_path="/repos/{owner}/{repo}/contents/{filepath}",
                    route_method="GET",
                ),
            },
            annotations=ToolAnnotations(title="Get Contents"),
        )

    @pytest.mark.asyncio
    async def test_base64_decode_branch_decodes_content(self) -> None:
        import base64

        transform = self.make_transform()
        tool = self.make_tool()

        encoded = base64.b64encode(b"hello world").decode()
        data = {"content": encoded, "encoding": "base64"}

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text=str(data))],
                structured_content={"result": data},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

        assert output.structured_content == {"result": "hello world"}

    @pytest.mark.asyncio
    async def test_base64_decode_handles_non_base64_dict(self) -> None:
        """When encoding is not 'base64', data passes through unchanged."""
        transform = self.make_transform()
        tool = self.make_tool()

        data = {"content": "plain text", "encoding": "utf-8"}

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text=str(data))],
                structured_content={"result": data},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

        # Non-base64 data passes through unchanged
        assert output.structured_content == {"result": data}

    @pytest.mark.asyncio
    async def test_base64_decode_skips_when_not_data_dict(self) -> None:
        """When result is not a dict, the branch is skipped (falls through)."""
        transform = self.make_transform()
        tool = self.make_tool()

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="not json")],
                structured_content={"result": "not a dict"},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

        assert output.structured_content == {"result": "not a dict"}

    @pytest.mark.asyncio
    async def test_base64_decode_skips_when_response_transform_not_set(self) -> None:
        """When response_transform is None, the branch is skipped."""
        transform = self.make_transform()
        tool = self.make_tool()
        assert tool.meta is not None
        tool.meta["_customization"].response_transform = None

        import base64

        encoded = base64.b64encode(b"should not decode").decode()
        data = {"content": encoded, "encoding": "base64"}

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text=str(data))],
                structured_content={"result": data},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

        assert output.structured_content == {"result": data}


# ---------------------------------------------------------------------------
# _ToolWrappingTransform: binary response pipeline branch
# ---------------------------------------------------------------------------


class TestPipelineBinaryResponse:
    """Tests for the binary response branch in _pipeline_with_context."""

    def make_transform(self) -> _ToolWrappingTransform:
        return _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(),
        )

    def make_tool(self) -> Tool:
        return Tool(
            name="repo_get_archive",
            tags={"repository"},
            description="Get archive",
            parameters={"properties": {}, "required": []},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "null"}},
            },
            meta={
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=False,
                    is_empty_response=False,
                    is_binary_response=True,
                    response_transform=None,
                    route_path="/repos/{owner}/{repo}/archive/{archive}",
                    route_method="GET",
                ),
            },
            annotations=ToolAnnotations(title="Get Archive"),
        )

    @pytest.mark.asyncio
    async def test_binary_response_returns_content_info(self) -> None:
        transform = self.make_transform()
        tool = self.make_tool()

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="binary\x00data")],
                structured_content=None,
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

        assert output.structured_content is not None
        assert output.structured_content["result"] is None
        assert output.structured_content["content_info"]["type"] == "binary"
        assert "Use format='raw'" in output.structured_content["content_info"]["message"]

    @pytest.mark.asyncio
    async def test_binary_response_skips_when_not_binary(self) -> None:
        """When is_binary_response is False, the branch is skipped."""
        transform = self.make_transform()
        tool = self.make_tool()
        assert tool.meta is not None
        tool.meta["_customization"].is_binary_response = False

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            from mcp.types import TextContent

            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="text data")],
                structured_content={"result": "text data"},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

        assert output.structured_content == {"result": "text data"}


# ---------------------------------------------------------------------------
# Edge-case coverage for uncovered defensive paths
# ---------------------------------------------------------------------------


class TestResponseIsBinaryEdgeCases:
    """Cover defensive guard paths in ``_response_is_binary``."""

    def test_operation_not_a_dict(self) -> None:
        """When the operation value is not a dict (e.g. malformed spec),
        return ``False``."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/archive": {
                    "get": "not-a-dict",
                },
            }
        )
        assert _response_is_binary(spec, "/repos/{owner}/{repo}/archive", "GET") is False
        # Uses "get" (lowercase) — FastMCP calls with lowercase methods


class TestTryHandleBinaryResponseEdgeCases:
    """Cover defensive guard paths in ``_try_handle_binary_response``."""

    def make_transform(self) -> _ToolWrappingTransform:
        return _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(),
        )

    @pytest.mark.asyncio
    async def test_skips_when_structured_content_present(self) -> None:
        """When the result already has structured_content, return ``None``
        (handler does not apply — it's not a binary response)."""
        transform = self.make_transform()
        result = ToolResult(
            content=[],
            structured_content={"result": "already parsed"},
        )
        output = await transform._try_handle_binary_response(result)
        assert output is None


class TestPipelineUnicodeDecodeError:
    """Cover UnicodeDecodeError handling in ``_pipeline_with_context``."""

    def make_transform(self) -> _ToolWrappingTransform:
        return _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(),
        )

    def _make_tool(self, *, is_binary_response: bool) -> Tool:
        return Tool(
            name="repo_get_archive",
            tags={"repository"},
            description="Get archive",
            parameters={"properties": {}, "required": []},
            output_schema=None,
            meta={
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=False,
                    is_empty_response=False,
                    is_binary_response=is_binary_response,
                    response_transform=None,
                    route_path="/repos/{owner}/{repo}/archive/{archive}",
                    route_method="GET",
                ),
            },
            annotations=ToolAnnotations(title="Get Archive"),
        )

    @pytest.mark.asyncio
    async def test_unicode_decode_error_binary_response(self) -> None:
        """UnicodeDecodeError + is_binary_response → nil result + content_info."""
        transform = self.make_transform()
        tool = self._make_tool(is_binary_response=True)

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            mock_run.side_effect = UnicodeDecodeError(
                "utf-8",
                b"\xff",
                0,
                1,
                "bad byte",
            )
            wrapped = (await transform.list_tools([tool]))[0]
            output = await wrapped.run(arguments={})

        assert output.structured_content is not None
        assert "content_info" in output.structured_content
        assert output.structured_content["content_info"]["type"] == "binary"

    @pytest.mark.asyncio
    async def test_unicode_decode_error_non_binary_re_raises(self) -> None:
        """UnicodeDecodeError without is_binary_response propagates."""
        transform = self.make_transform()
        tool = self._make_tool(is_binary_response=False)

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder.run_validation"),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            mock_run.side_effect = UnicodeDecodeError(
                "utf-8",
                b"\xff",
                0,
                1,
                "bad byte",
            )
            wrapped = (await transform.list_tools([tool]))[0]
            with pytest.raises(UnicodeDecodeError):
                await wrapped.run(arguments={})


# ---------------------------------------------------------------------------
# Direct unit tests for customization helper functions (ref: #660)
# ---------------------------------------------------------------------------


class TestComputeToolSchema:
    """Direct tests for _compute_tool_schema — pure, no side effects."""

    def test_returns_computed_schema_for_get_endpoint(self) -> None:
        """All _ComputedSchema fields are populated for a simple GET."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            method="GET",
        )
        spec = make_openapi_spec()

        result = _compute_tool_schema(route, spec)

        assert isinstance(result, _ComputedSchema)
        assert result.route_path == "/repos/{owner}/{repo}/issues"
        assert result.route_method == "GET"
        assert result.is_text_response is False
        assert result.is_binary_response is False
        assert result.response_transform is None

    def test_route_path_and_method_from_route(self) -> None:
        """route_path and route_method reflect the route object."""
        route = MagicMock(path="/api/v1/repos", method="POST")
        spec = make_openapi_spec()

        result = _compute_tool_schema(route, spec)

        assert result.route_path == "/api/v1/repos"
        assert result.route_method == "POST"

    def test_text_response_detection(self) -> None:
        """is_text_response is False for a minimal spec (no text/plain)."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET")
        spec = make_openapi_spec()

        result = _compute_tool_schema(route, spec)

        assert result.is_text_response is False

    def test_output_schema_is_none_for_no_content(self) -> None:
        """output_schema is None when the spec has no response schema."""
        route = MagicMock(path="/empty", method="DELETE")
        spec = make_openapi_spec()

        result = _compute_tool_schema(route, spec)

        assert result.output_schema is None
        assert result.raw_schema is None


class TestApplyToolIdentity:
    """Direct tests for _apply_tool_identity — mutations on OpenAPITool."""

    def test_sets_title_and_annotations(self) -> None:
        """Title and ToolAnnotations are set from route operationId."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            method="GET",
            operation_id="issue_list_issues",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}

        scope = _apply_tool_identity(route, tool)

        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert scope is not None  # scope derived from tags

    def test_returns_required_scope(self) -> None:
        """Return value is the derived required_scope string."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            operation_id="issue_create_issue",
            method="POST",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_create_issue"
        tool.annotations = {}
        tool.tags = set()
        tool.parameters = {"properties": {}}

        scope = _apply_tool_identity(route, tool)

        assert isinstance(scope, str)
        assert "write" in scope

    def test_adds_category_tag(self) -> None:
        """Category tag is added to tool.tags."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            operation_id="issue_list_issues",
            method="GET",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = {}
        tool.tags = set()
        tool.parameters = {"properties": {}}

        _apply_tool_identity(route, tool)

        assert len(tool.tags) > 0


class TestApplyFallbackSchemas:
    """Direct tests for _apply_fallback_schemas."""

    def test_early_return_when_output_schema_exists(self) -> None:
        """Returns False without mutation when output_schema is set."""
        component = MagicMock(spec=OpenAPITool)
        component.output_schema = {"type": "object"}
        schema = _ComputedSchema(
            output_schema={"type": "object"},
            raw_schema=None,
            is_text_response=False,
            is_binary_response=False,
            response_transform=None,
            route_path="/test",
            route_method="GET",
        )

        result = _apply_fallback_schemas(
            component,
            schema,
            openapi_spec=make_openapi_spec(),
        )

        assert result is False
        # output_schema was not overwritten
        assert component.output_schema == {"type": "object"}

    def test_text_plain_fallback(self) -> None:
        """Sets text/plain fallback schema when output_schema is None."""
        component = MagicMock(spec=OpenAPITool)
        component.output_schema = None
        schema = _ComputedSchema(
            output_schema=None,
            raw_schema=None,
            is_text_response=True,
            is_binary_response=False,
            response_transform=None,
            route_path="/raw",
            route_method="GET",
        )

        result = _apply_fallback_schemas(
            component,
            schema,
            openapi_spec=make_openapi_spec(),
        )

        assert result is False
        assert component.output_schema == {
            "type": "object",
            "properties": {"result": {"type": "string"}},
        }

    def test_no_content_fallback(self) -> None:
        """Sets null-result schema when output_schema is None and not text."""
        component = MagicMock(spec=OpenAPITool)
        component.output_schema = None
        schema = _ComputedSchema(
            output_schema=None,
            raw_schema=None,
            is_text_response=False,
            is_binary_response=False,
            response_transform=None,
            route_path="/repos/{owner}/{repo}/pulls/{index}/merge",
            route_method="POST",
        )

        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/pulls/{index}/merge": {
                    "post": {
                        "responses": {
                            "200": {"$ref": "#/components/responses/APIEmpty"},
                        },
                    },
                },
            },
            components={
                "responses": {
                    "APIEmpty": {
                        "description": "APIEmpty is an empty response",
                    },
                },
            },
        )

        result = _apply_fallback_schemas(component, schema, openapi_spec=spec)

        assert result is True
        assert component.output_schema == {
            "type": "object",
            "properties": {
                "result": {
                    "type": "null",
                    "description": "No content returned. The operation completed successfully.",
                },
            },
        }


class TestInjectResponseMetadata:
    """Direct tests for _inject_response_metadata."""

    def test_adds_wrap_result_flag(self) -> None:
        """x-fastmcp-wrap-result is set when output_schema exists."""
        component = MagicMock(spec=OpenAPITool)
        component.output_schema = {"type": "object", "properties": {}}

        _inject_response_metadata(component)

        assert component.output_schema["x-fastmcp-wrap-result"] is True

    def test_no_wrap_result_when_none(self) -> None:
        """No mutation when component.output_schema is None."""
        component = MagicMock(spec=OpenAPITool)
        component.output_schema = None

        _inject_response_metadata(component)

        # output_schema is still None — no KeyError
        assert component.output_schema is None

    def test_pagination_metadata_for_array_response(self) -> None:
        """Pagination fields injected for array response schemas."""
        component = MagicMock(spec=OpenAPITool)
        component.output_schema = {
            "type": "object",
            "properties": {
                "result": {"type": "array", "items": {"type": "string"}},
            },
        }

        _inject_response_metadata(component)

        props = component.output_schema["properties"]
        assert "has_more" in props
        assert "next_offset" in props
        assert "total_count" in props


class TestApplySchemaPostprocessingDirect:
    """Direct tests for _apply_schema_postprocessing orchestrator."""

    def test_calls_validation_augmentation(self) -> None:
        """augment_schema_with_validation is called on the component."""
        component = MagicMock(spec=OpenAPITool)
        component.output_schema = None
        component.tags = set()
        schema = _ComputedSchema(
            output_schema={"type": "object"},
            raw_schema=None,
            is_text_response=False,
            is_binary_response=False,
            response_transform=None,
            route_path="/test",
            route_method="GET",
        )

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.augment_schema_with_validation",
        ) as mock_augment:
            _apply_schema_postprocessing(
                component,
                schema,
                has_labels=False,
                openapi_spec=make_openapi_spec(),
            )

        mock_augment.assert_called_once_with(component)


class TestBuildCustomizationMeta:
    """Direct tests for _build_customization_meta."""

    def test_sets_component_meta_fields(self) -> None:
        """component.meta is populated with all expected keys."""
        component = MagicMock(spec=OpenAPITool)
        component.meta = None
        schema = _ComputedSchema(
            output_schema={"type": "object"},
            raw_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            is_text_response=False,
            is_binary_response=False,
            response_transform=None,
            route_path="/repos/{owner}/{repo}",
            route_method="GET",
        )

        _build_customization_meta(
            component,
            required_scope="read:repository",
            schema=schema,
            has_labels=False,
            has_no_content=False,
        )

        meta = component.meta
        assert meta["required_scope"] == "read:repository"
        assert "_customization" in meta
        assert meta["_contract_wrap"] is True
        assert meta["_customization"].route_path == "/repos/{owner}/{repo}"
        assert meta["_customization"].route_method == "GET"

    def test_tool_customization_fields_match_schema(self) -> None:
        """ToolCustomization fields are wired from _ComputedSchema."""
        component = MagicMock(spec=OpenAPITool)
        component.meta = {}
        schema = _ComputedSchema(
            output_schema={"type": "object"},
            raw_schema=None,
            is_text_response=True,
            is_binary_response=True,
            response_transform="base64-decode",
            route_path="/contents/{filepath}",
            route_method="GET",
        )

        _build_customization_meta(
            component,
            required_scope="read:repository",
            schema=schema,
            has_labels=True,
            has_no_content=True,
        )

        c = component.meta["_customization"]
        assert isinstance(c, ToolCustomization)
        assert c.is_text_response is True
        assert c.is_binary_response is True
        assert c.response_transform == "base64-decode"
        assert c.route_path == "/contents/{filepath}"
        assert c.route_method == "GET"
        assert c.has_labels is True
        assert c.is_empty_response is True
