"""Unit tests for tool customization (categorize, title, hints, customize_component)."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.constants import LABEL_GUIDANCE, TITLE_TRUNCATE_LIMIT
from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.openapi_converter import _wrap_success_response_schemas
from gitea_mcp_server.pagination import pagination_ctx
from gitea_mcp_server.tools.customize import (
    add_inferred_hints as _add_inferred_hints,
    categorize_tool as _categorize_tool,
    customize_component as _customize_component,
    generate_tool_title as _generate_tool_title,
    _is_array_response,
)


@pytest.fixture
def label_manager():
    """Return a fresh LabelManager per test to avoid shared mutable state."""
    return LabelManager()


class TestCategorizeTool:
    """Tests for the _categorize_tool function."""

    def test_admin_paths(self):
        assert _categorize_tool("/admin/cron") == "admin"
        assert _categorize_tool("/admin/users") == "admin"
        assert _categorize_tool("/admin/emails/search") == "admin"

    def test_organization_paths(self):
        assert _categorize_tool("/orgs") == "organization"
        assert _categorize_tool("/orgs/{org}") == "organization"
        assert _categorize_tool("/org/{org}/repos") == "organization"
        assert _categorize_tool("/orgs/{org}/members") == "organization"

    def test_user_paths(self):
        assert _categorize_tool("/user") == "user"
        assert _categorize_tool("/user/keys") == "user"
        assert _categorize_tool("/users/{username}") == "user"
        assert _categorize_tool("/users/{username}/repos") == "user"

    def test_issue_paths(self):
        assert _categorize_tool("/repos/{owner}/{repo}/issues") == "issue"
        assert _categorize_tool("/repos/issues/search") == "issue"
        assert _categorize_tool("/repos/{owner}/{repo}/issues/{index}/comments") == "issue"
        assert _categorize_tool("/repos/{owner}/{repo}/issues/{index}/labels") == "issue"

    def test_pull_request_paths(self):
        assert _categorize_tool("/repos/{owner}/{repo}/pulls") == "pull_request"
        assert _categorize_tool("/repos/{owner}/{repo}/pulls/{index}") == "pull_request"
        assert _categorize_tool("/repos/{owner}/{repo}/pulls/{base}/{head}") == "pull_request"
        assert _categorize_tool("/repos/{owner}/{repo}/pulls/{index}/reviews") == "pull_request"

    def test_repository_paths(self):
        assert _categorize_tool("/repos/{owner}/{repo}") == "repository"
        assert _categorize_tool("/repos/migrate") == "repository"
        assert _categorize_tool("/repos/{owner}/{repo}/branches") == "repository"
        assert _categorize_tool("/repos/{owner}/{repo}/commits") == "repository"
        assert _categorize_tool("/repos/{owner}/{repo}/contents") == "repository"
        assert _categorize_tool("/repos/{owner}/{repo}/releases") == "repository"
        assert _categorize_tool("/repos/{owner}/{repo}/tags") == "repository"

    def test_misc_paths(self):
        assert _categorize_tool("/version") == "misc"
        assert _categorize_tool("/markdown") == "misc"
        assert _categorize_tool("/notifications") == "misc"
        assert _categorize_tool("/activitypub/actor") == "misc"
        assert _categorize_tool("/licenses") == "misc"
        assert _categorize_tool("/topics/search") == "misc"


class TestGenerateToolTitle:
    """Tests for the _generate_tool_title function."""

    def test_uses_summary_when_short(self):
        route = MagicMock(summary="List all users", operation_id="listUsers")
        title = _generate_tool_title(route)
        assert title == "List all users"

    def test_long_summary_truncated(self):
        route = MagicMock(
            summary="This is a very long summary that exceeds fifty characters and should be truncated",
            operation_id="someOp",
        )
        title = _generate_tool_title(route)
        assert len(title) <= TITLE_TRUNCATE_LIMIT
        assert title.endswith("...")

    def test_uses_operation_id_when_no_summary(self):
        route = MagicMock(summary=None, operation_id="get_user_details")
        title = _generate_tool_title(route)
        assert title == "Get User Details"

    def test_operation_id_title_case(self):
        route = MagicMock(summary=None, operation_id="create_issue_comment")
        title = _generate_tool_title(route)
        assert title == "Create Issue Comment"

    def test_operation_id_with_numbers(self):
        route = MagicMock(summary=None, operation_id="get_v1_users")
        title = _generate_tool_title(route)
        assert title == "Get V1 Users"

    def test_empty_strings(self):
        route = MagicMock(summary="", operation_id="")
        title = _generate_tool_title(route)
        assert title == "Unnamed Tool"

    def test_none_values(self):
        route = MagicMock(summary=None, operation_id=None)
        title = _generate_tool_title(route)
        assert title == "Unnamed Tool"


class TestInferredHints:
    """Tests for annotation hints inferred from HTTP method."""

    def test_readonly_hint_for_get_method(self):
        route = MagicMock(path="/test", method="GET", summary="Test GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = ToolAnnotations()
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is True

    def test_readonly_hint_for_head_method(self):
        route = MagicMock(path="/test", method="HEAD", summary="Test HEAD")
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = ToolAnnotations()
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        assert tool.annotations.readOnlyHint is True

    def test_readonly_hint_for_options_method(self):
        route = MagicMock(path="/test", method="OPTIONS", summary="Test OPTIONS")
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = ToolAnnotations()
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        assert tool.annotations.readOnlyHint is True

    def test_destructive_hint_for_delete_method(self):
        route = MagicMock(path="/test", method="DELETE", summary="Test DELETE")
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = ToolAnnotations()
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        assert tool.annotations.destructiveHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.readOnlyHint is False

    def test_put_method_is_idempotent_but_not_readonly(self):
        route = MagicMock(path="/test", method="PUT", summary="Test PUT")
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = ToolAnnotations()
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        assert tool.annotations.idempotentHint is True
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False

    def test_post_method_is_not_idempotent_by_default(self):
        route = MagicMock(path="/test", method="POST", summary="Test POST")
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = ToolAnnotations()
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        assert tool.annotations.idempotentHint is False
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False

    def test_patch_method_is_not_idempotent_by_default(self):
        route = MagicMock(path="/test", method="PATCH", summary="Test PATCH")
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = ToolAnnotations()
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        assert tool.annotations.idempotentHint is False
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False

    def test_openworld_hint_always_true(self):
        for method in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
            route = MagicMock(path="/test", method=method, summary=f"Test {method}")
            tool = MagicMock(spec=OpenAPITool)
            tool.annotations = ToolAnnotations()
            tool.tags = set()

            _add_inferred_hints(route, tool.annotations)

            assert tool.annotations.openWorldHint is True, f"Failed for method {method}"

    def test_preserves_existing_hints_when_already_set(self):
        """Test that existing hint values are not overwritten by inference."""
        route = MagicMock(path="/test", method="GET", summary="Test GET")

        # GET would normally set readOnlyHint=True, but existing is False
        existing = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
        tool = MagicMock(spec=OpenAPITool)
        tool.annotations = existing
        tool.tags = set()

        _add_inferred_hints(route, tool.annotations)

        # Existing values should be preserved
        assert tool.annotations.readOnlyHint is False  # Not overwritten to True
        assert tool.annotations.destructiveHint is True  # Preserved
        # idempotentHint should be added because it's None
        assert tool.annotations.idempotentHint is True
        # openWorldHint should be added
        assert tool.annotations.openWorldHint is True

    def test_all_hints_added_when_annotations_empty(self, label_manager):
        route = MagicMock(path="/test", method="POST", summary="Test POST")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test_post"
        tool.annotations = ToolAnnotations()  # All fields None
        tool.tags = set()
        tool.parameters = {"properties": {}}  # Provide minimal parameters
        tool.output_schema = None
        tool.description = "Test POST"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        # All hints should be set based on method
        assert new_tool is not None
        assert new_tool.annotations.readOnlyHint is False
        assert new_tool.annotations.destructiveHint is False
        assert new_tool.annotations.idempotentHint is False
        assert new_tool.annotations.openWorldHint is True
        assert new_tool.annotations.title == "Test POST"  # Title uses summary as-is


class TestCustomizeComponent:
    """Tests for the _customize_component function."""

    def test_only_tools_are_customized(self, label_manager):
        from fastmcp.server.providers.openapi import OpenAPIResource

        # Mock a non-tool component with spec to pass isinstance check
        route = MagicMock(path="/test", summary="Test", operation_id="test_route")
        resource = MagicMock(spec=OpenAPIResource)

        _customize_component(route, resource, label_manager)

        # Should return early without modifying
        assert True  # No exception means pass

        route = MagicMock(
            path="/repos/{owner}/{repo}/issues", summary="List issues", operation_id="list_issues"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "List issues"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert new_tool.annotations is not None
        assert isinstance(new_tool.annotations, ToolAnnotations)
        assert new_tool.annotations.title == "List issues"
        assert "issue" in new_tool.tags

    def test_adds_annotations_to_tool_with_dict(self, label_manager):
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues", summary="List issues", operation_id="list_issues"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = {"title": "Old Title"}  # dict that can be unpacked to ToolAnnotations
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "List issues"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert isinstance(new_tool.annotations, ToolAnnotations)
        assert new_tool.annotations.title == "List issues"  # Our title overrides dict
        assert "issue" in new_tool.tags

    def test_converts_existing_toolannotations_properly(self, label_manager):
        route = MagicMock(
            path="/repos/{owner}/{repo}/pulls/{index}",
            summary="Get pull request",
            operation_id="get_pull",
        )
        existing = ToolAnnotations(title="Old Title", readOnlyHint=True)
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_pull"
        tool.annotations = existing
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get pull request"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert isinstance(new_tool.annotations, ToolAnnotations)
        assert new_tool.annotations.title == "Get pull request"  # Updated
        assert new_tool.annotations.readOnlyHint is True  # Preserved
        assert "pull_request" in new_tool.tags

    def test_category_detection_various_paths(self, label_manager):
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
            route = MagicMock(path=path, summary=None, operation_id="test_op")
            tool = MagicMock(spec=OpenAPITool)
            tool.name = "test"
            tool.annotations = None
            tool.tags = set()
            tool.parameters = {"properties": {}}
            tool.output_schema = None
            tool.description = "Test"
            tool.version = "1"
            tool.auth = None
            tool.serializer = None
            tool.meta = {}

            new_tool = _customize_component(route, tool, label_manager)

            assert new_tool is not None
            assert new_tool.annotations is not None
            assert expected_category in new_tool.tags, (
                f"Failed for {path}: category {expected_category} not in tags"
            )

    def test_title_generation_from_operation_id(self, label_manager):
        route = MagicMock(path="/test", summary=None, operation_id="get_user_by_id")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_user_by_id"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get user by ID"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert new_tool.annotations.title == "Get User By Id"

    def test_long_operation_id_truncated(self, label_manager):
        long_op_id = (
            "this_is_a_very_long_operation_id_that_exceeds_fifty_characters_and_needs_truncation"
        )
        route = MagicMock(path="/test", summary=None, operation_id=long_op_id)
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test operation"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert len(new_tool.annotations.title) <= TITLE_TRUNCATE_LIMIT
        assert new_tool.annotations.title.endswith("...")

    def test_uses_tool_description_not_doc(self, label_manager):
        """Verify that component.description is used, not __doc__."""
        route = MagicMock(path="/test", summary="Test", operation_id="test_op")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test_op"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Description from attribute"
        tool.__doc__ = "Docstring should be ignored"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        # The description should come from component.description, not __doc__
        assert "Description from attribute" in new_tool.description
        assert "Docstring should be ignored" not in new_tool.description

    def test_applies_label_guidance_when_labels_parameter_present(self, label_manager):
        """Verify LABEL_GUIDANCE is appended for tools with labels parameter."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues", summary="Create issue", operation_id="create_issue"
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
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert LABEL_GUIDANCE.strip() in new_tool.description

    def test_applies_label_guidance_with_nullable_array_type(self, label_manager):
        """Verify label guidance works with nullable array type ['array', 'null']."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues", summary="Create issue", operation_id="create_issue"
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
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert LABEL_GUIDANCE.strip() in new_tool.description

    def test_does_not_apply_label_guidance_without_labels(self, label_manager):
        """Verify LABEL_GUIDANCE is not added if tool has no labels parameter."""
        route = MagicMock(
            path="/repos/{owner}/{repo}/issues", summary="List issues", operation_id="list_issues"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}  # No labels parameter
        tool.output_schema = None
        tool.description = "List issues"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = _customize_component(route, tool, label_manager)

        assert new_tool is not None
        assert LABEL_GUIDANCE.strip() not in new_tool.description


class TestIsArrayResponse:
    """Tests for _is_array_response function."""

    def test_detects_array_result(self):
        from gitea_mcp_server.tools.customize import _is_array_response

        schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
        }
        assert _is_array_response(schema) is True

    def test_detects_nullable_array_result(self):
        from gitea_mcp_server.tools.customize import _is_array_response

        schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": ["array", "null"],
                    "items": {"type": "object"},
                },
            },
        }
        assert _is_array_response(schema) is True

    def test_rejects_object_result(self):
        from gitea_mcp_server.tools.customize import _is_array_response

        schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                },
            },
        }
        assert _is_array_response(schema) is False

    def test_missing_result_key(self):
        from gitea_mcp_server.tools.customize import _is_array_response

        schema = {"type": "object", "properties": {}}
        assert _is_array_response(schema) is False

    def test_none_input(self):
        from gitea_mcp_server.tools.customize import _is_array_response

        assert _is_array_response(None) is False

    def test_empty_dict(self):
        from gitea_mcp_server.tools.customize import _is_array_response

        assert _is_array_response({}) is False

    def test_properties_not_a_dict(self):
        from gitea_mcp_server.tools.customize import _is_array_response

        schema = {"type": "object", "properties": "not_a_dict"}
        assert _is_array_response(schema) is False


class TestPaginationMetadata:
    """Tests for pagination metadata injection in transform_fn."""

    PAGINATION_SPEC: dict = {
        "openapi": "3.1.1",
        "paths": {
            "/repos/{owner}/{repo}/issues": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "IssueList",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/repos/{owner}/{repo}/issues/{index}": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Issue",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer", "description": "Issue ID"},
                                            "title": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            },
        },
    }

    @pytest.fixture
    def _wrapped_spec(self):
        from gitea_mcp_server.openapi_converter import _wrap_success_response_schemas

        spec = deepcopy(self.PAGINATION_SPEC)
        _wrap_success_response_schemas(spec)
        return spec

    @pytest.mark.asyncio
    async def test_has_more_true_when_result_equals_per_page(self, _wrapped_spec, label_manager):
        """has_more should be True when result length equals per_page."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET", summary="List issues", operation_id="list_issues")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {"page": {"type": "integer"}, "per_page": {"type": "integer"}}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": [{"id": i} for i in range(30)]}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({"page": 1, "per_page": 30})

        assert result.structured_content["has_more"] is True
        assert result.structured_content["next_offset"] == 2
        assert result.structured_content["total_count"] is None
        assert len(result.structured_content["result"]) == 30

    @pytest.mark.asyncio
    async def test_has_more_false_when_result_less_than_per_page(self, _wrapped_spec, label_manager):
        """has_more should be False when result length is less than per_page."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET", summary="List issues", operation_id="list_issues")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {"page": {"type": "integer"}, "per_page": {"type": "integer"}}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": [{"id": i} for i in range(15)]}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({"page": 1, "per_page": 30})

        assert result.structured_content["has_more"] is False
        assert result.structured_content["next_offset"] is None
        assert result.structured_content["total_count"] is None

    @pytest.mark.asyncio
    async def test_defaults_when_kwargs_missing(self, _wrapped_spec, label_manager):
        """Should default to page=1 and limit=100 when no pagination args provided."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET", summary="List issues", operation_id="list_issues")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        # Return exactly 100 items, which equals the default `limit`
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": [{"id": i} for i in range(100)]}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({})

        assert result.structured_content["has_more"] is True
        assert result.structured_content["next_offset"] == 2

    @pytest.mark.asyncio
    async def test_uses_limit_parameter(self, _wrapped_spec, label_manager):
        """Should use 'limit' parameter as fallback when 'per_page' is not present."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET", summary="List issues", operation_id="list_issues")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {"page": {"type": "integer"}, "limit": {"type": "integer"}}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": [{"id": i} for i in range(50)]}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({"page": 1, "limit": 50})

        assert result.structured_content["has_more"] is True
        assert result.structured_content["next_offset"] == 2

    @pytest.mark.asyncio
    async def test_no_pagination_for_non_array_response(self, _wrapped_spec, label_manager):
        """Non-array responses should not get pagination metadata."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues/{index}", method="GET", summary="Get issue", operation_id="get_issue")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": {"id": 1, "title": "Test"}}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({})

        assert result.structured_content == {"result": {"id": 1, "title": "Test"}}
        assert "has_more" not in result.structured_content

    @pytest.mark.asyncio
    async def test_total_count_defaults_to_none(self, _wrapped_spec, label_manager):
        """total_count should be None when no pagination headers captured."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET", summary="List issues", operation_id="list_issues")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {"page": {"type": "integer"}, "per_page": {"type": "integer"}}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": [{"id": i} for i in range(5)]}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({"page": 2, "per_page": 10})

        assert result.structured_content["total_count"] is None

    @pytest.mark.asyncio
    async def test_total_count_from_headers(self, _wrapped_spec, label_manager):
        """total_count should be populated from pagination context var."""
        pagination_ctx.set({"total_count": 42})

        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET", summary="List issues", operation_id="list_issues")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {"page": {"type": "integer"}, "per_page": {"type": "integer"}}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": [{"id": i} for i in range(5)]}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({"page": 1, "per_page": 10})

        assert result.structured_content["total_count"] == 42
        assert result.structured_content["has_more"] is False
        assert result.structured_content["next_offset"] is None

    @pytest.mark.asyncio
    async def test_preserves_original_result_data(self, _wrapped_spec, label_manager):
        """Original result data should be preserved in enhanced response."""
        route = MagicMock(path="/repos/{owner}/{repo}/issues", method="GET", summary="List issues", operation_id="list_issues")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {"page": {"type": "integer"}, "per_page": {"type": "integer"}}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        original_data = [{"id": 1, "title": "First"}, {"id": 2, "title": "Second"}]
        tool.run = AsyncMock(return_value=ToolResult(
            structured_content={"result": original_data}
        ))

        new_tool = _customize_component(route, tool, label_manager, _wrapped_spec)
        result = await new_tool.run({"page": 1, "per_page": 10})

        assert result.structured_content["result"] == original_data


class TestPrepareAnnotationsEdgeCases:
    """Tests for _prepare_annotations edge cases."""

    def test_non_standard_annotations_raises_fallback(self):
        """Non-standard annotations that can't construct ToolAnnotations uses fallback."""
        from gitea_mcp_server.tools.customize import _prepare_annotations

        component = MagicMock()
        component.annotations = "string_annotations"

        result = _prepare_annotations(component, "Test Title")
        assert result.title == "Test Title"
        assert isinstance(result, ToolAnnotations)


class TestCustomizeComponentTextResponse:
    """Tests for _customize_component with text response wrapping."""

    @pytest.mark.asyncio
    async def test_text_response_strips_structured_content(self):
        """Text response transforms content and structured_content."""
        from fastmcp.tools.base import ToolResult
        from mcp.types import TextContent

        from gitea_mcp_server.tools.customize import customize_component

        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            method="GET",
            summary="List issues",
            operation_id="issue_list_issues",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {
            "_customization": {
                "is_text_response": True,
                "route_path": "/repos/{owner}/{repo}/issues",
                "route_method": "GET",
            }
        }
        tool.run = AsyncMock(
            return_value=ToolResult(
                content=[TextContent(type="text", text="raw text output")],
                structured_content=None,
            )
        )

        openapi_spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "x-original-content-types": ["text/plain"],
                    }
                }
            }
        }
        new_tool = customize_component(route, tool, label_manager, openapi_spec=openapi_spec)
        result = await new_tool.run({})
        assert result.structured_content == {"result": "raw text output"}
        assert result.content[0].text == "raw text output"
