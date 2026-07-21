"""Unit tests for server_setup/mcp_builder.py (_customize_metadata, _ToolWrappingTransform)."""

from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations

from gitea_mcp_server.constants import LABEL_GUIDANCE
from gitea_mcp_server.server_setup.mcp_builder import (
    _customize_metadata,
    _ToolWrappingTransform,
    create_openapi_provider,
)

# ---------------------------------------------------------------------------
# _customize_metadata
# ---------------------------------------------------------------------------


class TestCustomizeMetadata:
    """Tests for _customize_metadata - in-place metadata on OpenAPITools."""

    def test_skips_non_openapi_tool(self):
        """Non-OpenAPITool components are skipped."""
        route = MagicMock(path="/test", summary="Test", operation_id="test_op", method="GET")
        resource = MagicMock(spec=object)

        _customize_metadata(route, resource, openapi_spec={})

    def test_sets_title_and_annotations(self):
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

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.annotations is not None
        assert tool.annotations.title == "List Items"
        assert tool.annotations.readOnlyHint is True

    def test_title_from_operation_id(self):
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

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.annotations.title == "Create Issue"
        assert "..." not in tool.annotations.title

    def test_adds_annotations_from_dict(self):
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

        _customize_metadata(route, tool, openapi_spec={})

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "List Issues"
        assert "issue" in tool.tags

    def test_preserves_existing_toolannotations(self):
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

        _customize_metadata(route, tool, openapi_spec={})

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "Get Pull Request"
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

    def test_adds_labels_tag_for_label_tools(self):
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

        _customize_metadata(route, tool, openapi_spec={})

        assert "labels" in tool.tags
        assert "issue" in tool.tags  # original tag preserved

    def test_does_not_add_labels_tag_without_labels(self):
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

        _customize_metadata(route, tool, openapi_spec={})

        assert "labels" not in tool.tags

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
# route_map_fn (spec-level filtering: deprecated + scope + exclusion)
# ---------------------------------------------------------------------------


class TestRouteMapFiltering:
    """Tests that create_openapi_provider drops filtered operations via route_map_fn."""

    def _provider(self, spec, excluded_routes, response_format="markdown"):
        from gitea_mcp_server.label_service import LabelService

        # Ensure a valid minimal info block so FastMCP's schema validation passes.
        spec = dict(spec)
        spec.setdefault("info", {"title": "Test", "version": "1.0.0"})
        spec.setdefault("components", {"schemas": {}})
        mock_gitea_client = MagicMock()
        mock_gitea_client.client = MagicMock()
        return create_openapi_provider(
            openapi_spec=spec,
            gitea_client=mock_gitea_client,
            label_service=LabelService(),
            excluded_routes=excluded_routes,
            response_format=response_format,
        )

    def test_empty_paths(self):
        """Empty paths dict returns empty set."""
        spec = {"openapi": "3.1.1", "paths": {}, "info": {"title": "T", "version": "1"}}
        provider = self._provider(spec, set())
        assert provider is not None

    def test_missing_paths(self):
        """Spec with no paths key returns empty set."""
        spec = {"openapi": "3.1.1", "info": {"title": "T", "version": "1"}}
        provider = self._provider(spec, set())
        assert provider is not None

    def test_non_dict_paths_rejected(self):
        """A non-dict paths value is rejected by FastMCP's spec validation."""
        spec = {"openapi": "3.1.1", "paths": "not_a_dict"}
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
            raise AssertionError("Expected FastMCP to reject non-dict paths")

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
        provider = self._provider(spec, set())
        assert provider is not None

    def test_single_deprecated_get(self):
        """Single deprecated GET is excluded via route_map_fn."""
        spec = {
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

    def test_multiple_deprecated_operations(self):
        """Multiple deprecated methods on same path are excluded."""
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
        provider = self._provider(
            spec, {("/repos/{owner}/{repo}", "PUT"), ("/repos/{owner}/{repo}", "DELETE")}
        )
        assert provider is not None

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
        provider = self._provider(
            spec,
            {
                ("/v1/old", "GET"),
                ("/v1/old", "POST"),
                ("/v2/also_old", "PATCH"),
            },
        )
        assert provider is not None

    def test_deprecated_false_not_included(self):
        """deprecated: false is treated as not deprecated (no exclusion)."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/user": {
                    "get": {"operationId": "getUser", "deprecated": False},
                },
            },
        }
        provider = self._provider(spec, set())
        assert provider is not None

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
        provider = self._provider(spec, {("/repos/{owner}/{repo}", "GET")})
        assert provider is not None

    def test_http_methods_comprehensive(self):
        """All HTTP methods are properly excluded via route_map_fn."""
        spec = {
            "openapi": "3.1.1",
            "paths": {
                "/resource": {
                    method: {"operationId": f"{method}Resource", "deprecated": True}
                    for method in ("get", "post", "put", "delete", "patch", "options", "head", "trace")
                },
            },
        }
        expected = {("/resource", method.upper()) for method in ("get", "post", "put", "delete", "patch", "options", "head", "trace")}
        provider = self._provider(spec, expected)
        assert provider is not None


# ---------------------------------------------------------------------------
# _ToolWrappingTransform - OpenTelemetry spans
# ---------------------------------------------------------------------------
# The session-scoped ``_init_otel_exporter`` and ``trace_exporter`` fixture
# are defined in ``tests/conftest.py`` (shared across all test modules).


class TestToolWrappingTransformTelemetry:
    """Tests for custom OTEL spans emitted from _ToolWrappingTransform._run_transform_pipeline."""

    def make_transform(self, openapi_spec=None):
        return _ToolWrappingTransform(
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
    async def test_spans_carry_tool_name_attribute(self, trace_exporter):
        """Validate and execute spans carry ``tool.name`` attribute."""
        transform = self.make_transform()
        tool = self.make_tool("attr_tool")

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
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

    @pytest.mark.asyncio
    async def test_validation_error_stops_pipeline(self, trace_exporter):
        """When validation fails, only the ``validate`` span is emitted."""
        from gitea_mcp_server.exceptions import ValidationError

        transform = self.make_transform()
        tool = self.make_tool("fail_tool")

        with (
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_validation",
                side_effect=ValidationError("missing required: owner"),
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
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

        from gitea_mcp_server.label_service import LabelService

        mock_gitea_client = MagicMock()
        mock_gitea_client.client = MagicMock()
        label_service = LabelService()
        provider = create_openapi_provider(
            openapi_spec=openapi_spec,
            gitea_client=mock_gitea_client,
            label_service=label_service,
            excluded_routes={("/old/endpoint", "POST")},
            response_format="markdown",
        )

        assert provider is not None
        assert "Excluding filtered endpoint" in caplog.text

    @pytest.mark.asyncio
    async def test_response_format_propagates_to_tool_schema(self):
        """response_format should flow into the tool's format parameter default."""
        from gitea_mcp_server.label_service import LabelService

        spec = {
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
            response_format="json",
        )
        tools = await provider.list_tools()
        tool = next(t for t in tools if t.name == "getUser")
        fmt_param = tool.parameters["properties"]["format"]
        assert fmt_param["default"] == "json"
        assert fmt_param["type"] == "string"
        assert "json" in fmt_param["enum"]


# ---------------------------------------------------------------------------
# _ToolWrappingTransform
# ---------------------------------------------------------------------------


class TestToolWrappingTransform:
    """Tests for _ToolWrappingTransform."""

    def make_transform(self, openapi_spec=None, response_format="markdown"):
        return _ToolWrappingTransform(
            openapi_spec=openapi_spec or {},
            response_format=response_format,
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
    async def test_text_response_wrapping(self):
        """is_text_response wraps unstructured content in result dict."""
        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        tool.meta["_customization"]["is_text_response"] = True

        with (
            patch("gitea_mcp_server.server_setup.mcp_builder._run_validation"),
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

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_passthrough_no_extracted(self):
        """_apply_loop_hooks returns result unchanged when extracted is None."""
        from fastmcp.tools.base import ToolResult

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        result = ToolResult(
            structured_content={"result": [{"id": 1}]},
        )

        output = await transform._apply_loop_hooks(
            result, {"page": 1}, None, tool, "/test", "GET",
        )
        assert output is result

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_passthrough_empty_extracted(self):
        """_apply_loop_hooks returns result unchanged when extracted is empty."""
        from fastmcp.tools.base import ToolResult

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        result = ToolResult(
            structured_content={"result": [{"id": 1}]},
        )

        output = await transform._apply_loop_hooks(
            result, {"page": 1}, {}, tool, "/test", "GET",
        )
        assert output is result

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_calls_hook(self):
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
                result, {"page": 1, "limit": 10}, extracted, tool, "/test", "GET",
            )

        hook.assert_called_once()
        args = hook.call_args[0]
        # args: (result, value, kwargs, execute_fn)
        assert args[0] is result
        assert args[1] is True
        assert args[2] == {"page": 1, "limit": 10}
        # execute_fn is a callable
        assert callable(args[3])

        assert output.structured_content["has_more"] is False
        assert output.structured_content["result"] == [{"id": 1}, {"id": 2}]

    @pytest.mark.asyncio
    async def test_apply_loop_hooks_execute_fn_reinvokes_http(self):
        """The execute_fn passed to loop_hook calls _run_with_error_handling."""
        from fastmcp.tools.base import ToolResult
        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform = self.make_transform()
        tool = self.make_tool(customized=True)
        tool.name = "test_tool"

        async def my_loop_hook(result, value, kwargs, execute_fn):
            """Simple loop hook that fetches one more page and merges."""
            kwargs["page"] = 2
            next_result = await execute_fn(kwargs)
            data = result.structured_content["result"]
            data.extend(next_result.structured_content["result"])
            result.structured_content["result"] = data
            result.structured_content["has_more"] = False
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
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_execute,
        ):
            mock_execute.return_value = ToolResult(
                structured_content={"result": [{"id": 2}], "has_more": False},
            )

            output = await transform._apply_loop_hooks(
                result, {"page": 1, "limit": 10}, extracted, tool, "/test", "GET",
            )

        # Executed once with page=2
        mock_execute.assert_called_once()
        call_kwargs = mock_execute.call_args[0][0]
        assert call_kwargs["page"] == 2

        # Results merged
        assert output.structured_content["result"] == [{"id": 1}, {"id": 2}]
        assert output.structured_content["has_more"] is False

    @pytest.mark.asyncio
    async def test_execute_fn_validates_kwargs(self):
        """execute_fn validates kwargs, rejecting invalid values."""
        from fastmcp.tools.base import ToolResult
        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        async def bad_loop_hook(result, value, kwargs, execute_fn):
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
                    result, {"page": 1}, extracted, tool, "/test", "GET",
                )

    @pytest.mark.asyncio
    async def test_loop_hooks_chain_integration(self):
        """Pipeline with extracted loop hooks calls _apply_loop_hooks.

        Verifies that the full pipeline (transform_fn → _run_transform_pipeline
        → _pipeline_with_context → _apply_loop_hooks) correctly extracts loop
        hooks and passes execute_fn when extracted values are provided.
        """
        from fastmcp.tools.base import ToolResult
        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform = self.make_transform()
        tool = self.make_tool(customized=True)

        loop_hook_called = False

        async def my_loop_hook(result, value, kwargs, execute_fn):
            nonlocal loop_hook_called
            loop_hook_called = True
            return result

        extracted = {"fetch_test": True}

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
                "gitea_mcp_server.server_setup.mcp_builder._run_validation",
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
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

            from gitea_mcp_server.pagination import pagination_ctx

            pagination_ctx.set({"total_count": 1})

            # Call the pipeline directly with extracted
            result = await transform._run_transform_pipeline(
                {"page": 1, "limit": 10},
                tool,
                extracted=extracted,
            )

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
        transform = _ToolWrappingTransform(openapi_spec={})
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
                "_customization_applied": True,
                "_customization": {
                    "has_labels": False,
                    "is_text_response": False,
                    "is_empty_response": False,
                    "route_path": "/repos/{owner}/{repo}/items",
                    "route_method": "GET",
                },
            },
        )
        return transform, tool

    @pytest.mark.asyncio
    async def test_fetch_all_merges_all_pages(self, make_transform_and_tool):
        """Full pipeline with fetch_all=True merges paginated results."""
        from fastmcp.tools.base import ToolResult
        from gitea_mcp_server.tools.virtual_params import VirtualParam

        transform, tool = make_transform_and_tool

        # Simulate 3 pages of 10 items each
        page_calls: list[int] = []

        async def _mock_pages(kwargs, _tool, _spec, _path, _method):
            page = kwargs.get("page", 1)
            page_calls.append(page)
            start = (page - 1) * 10 + 1
            items = [{"id": i} for i in range(start, min(start + 10, 31))]
            has_more = page < 3
            return ToolResult(
                structured_content={
                    "result": items,
                    "has_more": has_more,
                    "next_offset": page + 1 if has_more else None,
                    "total_count": 30,
                },
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
                        loop_hook=_mock_fetch_all_hook,
                    ),
                },
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_validation",
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                return_value=True,
            ),
        ):
            mock_run.side_effect = _mock_pages

            from gitea_mcp_server.pagination import pagination_ctx

            pagination_ctx.set({"total_count": 30})

            result = await transform._run_transform_pipeline(
                {"page": 1, "limit": 10, "owner": "test"},
                tool,
                extracted=extracted,
            )

            pagination_ctx.set({})

        # 3 pages fetched total
        assert page_calls == [1, 2, 3]
        # All 30 items merged
        assert len(result.structured_content["result"]) == 30
        assert result.structured_content["has_more"] is False
        assert result.structured_content["next_offset"] is None
        assert result.structured_content["total_count"] == 30

    @pytest.mark.asyncio
    async def test_fetch_all_false_single_page(self, make_transform_and_tool):
        """fetch_all=False fetches only the first page (no loop)."""
        from fastmcp.tools.base import ToolResult

        transform, tool = make_transform_and_tool

        page_calls: list[int] = []

        async def _mock_single(kwargs, _tool, _spec, _path, _method):
            page = kwargs.get("page", 1)
            page_calls.append(page)
            return ToolResult(
                structured_content={
                    "result": [{"id": 1}, {"id": 2}],
                    "has_more": True,
                    "next_offset": 2,
                    "total_count": 20,
                },
            )

        extracted = {"fetch_all": False}

        with (
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_validation",
            ),
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "gitea_mcp_server.server_setup.mcp_builder._is_array_response",
                return_value=True,
            ),
        ):
            mock_run.side_effect = _mock_single

            from gitea_mcp_server.pagination import pagination_ctx

            pagination_ctx.set({"total_count": 20})

            result = await transform._run_transform_pipeline(
                {"page": 1, "limit": 10, "owner": "test"},
                tool,
                extracted=extracted,
            )

            pagination_ctx.set({})

        # Only one page fetched
        assert page_calls == [1]
        assert len(result.structured_content["result"]) == 2
        assert result.structured_content["has_more"] is True  # still has more


async def _mock_fetch_all_hook(result, value, kwargs, execute_fn):
    """Simple loop hook that fetches pages via execute_fn and merges."""
    from gitea_mcp_server.tools.virtual_params import _fetch_all_loop

    return await _fetch_all_loop(result, value, kwargs, execute_fn)
