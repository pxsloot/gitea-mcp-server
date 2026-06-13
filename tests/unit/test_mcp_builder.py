"""Unit tests for server_setup/mcp_builder.py (_customize_metadata, _ToolWrappingTransform)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool
from fastmcp.tools.tool import ToolAnnotations

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

    def test_adds_category_tag(self):
        """Category tag is inferred from route path."""
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

        _customize_metadata(route, tool, openapi_spec={})

        assert "issue" in tool.tags

    def test_misc_category_for_unknown_path(self):
        """Unknown paths get 'misc' category."""
        route = MagicMock(
            path="/version", summary="Get version", operation_id="get_version", method="GET"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_version"
        tool.annotations = None
        tool.tags = set()
        tool.description = ""
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert "misc" in tool.tags

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
