"""Unit tests for tool annotation functionality."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.constants import LABEL_GUIDANCE, TITLE_TRUNCATE_LIMIT
from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.server_setup.bm25_search import NAME_BOOST, _extract_searchable_text_enhanced
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.tool_annotator import (
    _convert_labels,
    _format_available_labels,
)
from gitea_mcp_server.server_setup.tool_annotator import (
    add_inferred_hints as _add_inferred_hints,
)
from gitea_mcp_server.server_setup.tool_annotator import (
    categorize_tool as _categorize_tool,
)
from gitea_mcp_server.server_setup.tool_annotator import (
    customize_component as _customize_component,
)
from gitea_mcp_server.server_setup.tool_annotator import (
    derive_required_scope as _derive_required_scope,
)
from gitea_mcp_server.server_setup.tool_annotator import (
    generate_tool_title as _generate_tool_title,
)

# Create a label manager for tests that need it
_label_manager = LabelManager()


class TestSearchableText:
    """Tests for _extract_searchable_text_enhanced."""

    def test_name_is_boosted(self):
        """Tool name should appear NAME_BOOST times in the extracted text."""
        tool = Tool(
            name="gitea_user_get_current",
            description="Get the authenticated user",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert result.count("gitea_user_get_current") == NAME_BOOST

    def test_description_included(self):
        """Tool description should appear in the extracted text."""
        tool = Tool(
            name="test_tool",
            description="This is a test description",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "test description" in result

    def test_parameter_names_included(self):
        """Parameter names and descriptions should be part of the searchable text."""
        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={
                "properties": {
                    "owner": {"type": "string", "description": "The owner name"},
                    "repo": {"type": "string", "description": "The repository name"},
                }
            },
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "owner" in result
        assert "repo" in result
        assert "owner name" in result or "repository name" in result

    def test_tags_and_aliases_included(self):
        """Tags should be included with their category aliases."""
        tool = Tool(
            name="test_tool",
            description="A test tool",
            tags={"pull_request", "user"},
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "pull_request" in result
        assert "pull request pr" in result or "pr" in result

    def test_title_included(self):
        """Tool title from annotations should be included."""
        from fastmcp.tools.tool import ToolAnnotations

        tool = Tool(
            name="test_tool",
            description="A test tool",
            annotations=ToolAnnotations(title="Custom Title"),
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "Custom Title" in result

    def test_word_aliases_expanded(self):
        """Word aliases are only expanded on the query side, not in document text."""
        tool = Tool(
            name="test_repo_tool",
            description="Manage repositories",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        # "repos" comes from the description, not alias expansion
        assert "repos" in result

    def test_name_boost_improves_ranking(self):
        """Name boost should make tools findable by name terms not in description.
        
        A tool with distinctive name terms (like 'flag' in 'repo_get_flag') should
        be findable even if the description doesn't contain those exact words.
        """
        tool = Tool(
            name="gitea_repo_get_flag",
            description="Check if a repository has a given flag",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        # The name is repeated NAME_BOOST times, so "flag" from name appears NAME_BOOST times
        name_count = result.count("gitea_repo_get_flag")
        assert name_count == NAME_BOOST

    def test_no_side_effects_on_empty_fields(self):
        """Should handle tools with minimal fields gracefully."""
        tool = Tool(
            name="minimal_tool",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        assert "minimal_tool" in result
        assert isinstance(result, str)
        assert len(result) > 0


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


class TestDeriveRequiredScope:
    """Tests for the derive_required_scope function."""

    def test_admin_tag_returns_sudo(self):
        assert _derive_required_scope({"admin"}, "GET") == "sudo"
        assert _derive_required_scope({"admin"}, "POST") == "sudo"

    def test_repository_get_returns_read(self):
        assert _derive_required_scope({"repository"}, "GET") == "read:repository"

    def test_repository_post_returns_write(self):
        assert _derive_required_scope({"repository"}, "POST") == "write:repository"

    def test_issue_get_returns_read(self):
        assert _derive_required_scope({"issue"}, "GET") == "read:issue"

    def test_issue_post_returns_write(self):
        assert _derive_required_scope({"issue"}, "POST") == "write:issue"

    def test_organization_tag(self):
        assert _derive_required_scope({"organization"}, "GET") == "read:organization"
        assert _derive_required_scope({"organization"}, "PUT") == "write:organization"

    def test_user_tag(self):
        assert _derive_required_scope({"user"}, "GET") == "read:user"
        assert _derive_required_scope({"user"}, "DELETE") == "write:user"

    def test_notification_tag(self):
        assert _derive_required_scope({"notification"}, "GET") == "read:notification"

    def test_package_tag(self):
        assert _derive_required_scope({"package"}, "POST") == "write:package"

    def test_activitypub_tag(self):
        assert _derive_required_scope({"activitypub"}, "GET") == "read:activitypub"

    def test_miscellaneous_maps_to_misc(self):
        assert _derive_required_scope({"miscellaneous"}, "GET") == "read:misc"

    def test_settings_maps_to_repository(self):
        assert _derive_required_scope({"settings"}, "GET") == "read:repository"

    def test_pull_request_tag_not_in_scope_tags(self):
        """pull_request is a category tag, not a Swagger tag."""
        assert _derive_required_scope({"pull_request"}, "GET") is None

    def test_head_and_options_are_read(self):
        assert _derive_required_scope({"repository"}, "HEAD") == "read:repository"
        assert _derive_required_scope({"repository"}, "OPTIONS") == "read:repository"

    def test_put_delete_patch_are_write(self):
        assert _derive_required_scope({"repository"}, "PUT") == "write:repository"
        assert _derive_required_scope({"repository"}, "DELETE") == "write:repository"
        assert _derive_required_scope({"repository"}, "PATCH") == "write:repository"

    def test_none_tags_returns_none(self):
        assert _derive_required_scope(None, "GET") is None

    def test_empty_tags_returns_none(self):
        assert _derive_required_scope(set(), "GET") is None

    def test_missing_method_defaults_to_write(self):
        assert _derive_required_scope({"repository"}, None) == "write:repository"

    def test_first_known_tag_wins(self):
        """First matching tag in iteration order is used."""
        result = _derive_required_scope({"unknown", "repository", "user"}, "GET")
        assert result in ("read:repository", "read:user")


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

    def test_all_hints_added_when_annotations_empty(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        # All hints should be set based on method
        assert new_tool is not None
        assert new_tool.annotations.readOnlyHint is False
        assert new_tool.annotations.destructiveHint is False
        assert new_tool.annotations.idempotentHint is False
        assert new_tool.annotations.openWorldHint is True
        assert new_tool.annotations.title == "Test POST"  # Title uses summary as-is


class TestCustomizeComponent:
    """Tests for the _customize_component function."""

    def test_only_tools_are_customized(self):
        from fastmcp.server.providers.openapi import OpenAPIResource

        # Mock a non-tool component with spec to pass isinstance check
        route = MagicMock(path="/test", summary="Test", operation_id="test_route")
        resource = MagicMock(spec=OpenAPIResource)

        _customize_component(route, resource, _label_manager)

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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert new_tool.annotations is not None
        assert isinstance(new_tool.annotations, ToolAnnotations)
        assert new_tool.annotations.title == "List issues"
        assert "issue" in new_tool.tags

    def test_adds_annotations_to_tool_with_dict(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert isinstance(new_tool.annotations, ToolAnnotations)
        assert new_tool.annotations.title == "List issues"  # Our title overrides dict
        assert "issue" in new_tool.tags

    def test_converts_existing_toolannotations_properly(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert isinstance(new_tool.annotations, ToolAnnotations)
        assert new_tool.annotations.title == "Get pull request"  # Updated
        assert new_tool.annotations.readOnlyHint is True  # Preserved
        assert "pull_request" in new_tool.tags

    def test_category_detection_various_paths(self):
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

            new_tool = _customize_component(route, tool, _label_manager)

            assert new_tool is not None
            assert new_tool.annotations is not None
            assert expected_category in new_tool.tags, (
                f"Failed for {path}: category {expected_category} not in tags"
            )

    def test_title_generation_from_operation_id(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert new_tool.annotations.title == "Get User By Id"

    def test_long_operation_id_truncated(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert len(new_tool.annotations.title) <= TITLE_TRUNCATE_LIMIT
        assert new_tool.annotations.title.endswith("...")

    def test_uses_tool_description_not_doc(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        # The description should come from component.description, not __doc__
        assert "Description from attribute" in new_tool.description
        assert "Docstring should be ignored" not in new_tool.description

    def test_applies_label_guidance_when_labels_parameter_present(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert LABEL_GUIDANCE.strip() in new_tool.description

    def test_applies_label_guidance_with_nullable_array_type(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert LABEL_GUIDANCE.strip() in new_tool.description

    def test_does_not_apply_label_guidance_without_labels(self):
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

        new_tool = _customize_component(route, tool, _label_manager)

        assert new_tool is not None
        assert LABEL_GUIDANCE.strip() not in new_tool.description


class TestSchemaTypeIsArray:
    """Tests for _schema_type_is_array."""

    def test_detects_string_type(self):
        """Should return True for type 'array'."""
        from gitea_mcp_server.server_setup.tool_annotator import _schema_type_is_array

        assert _schema_type_is_array({"type": "array"}) is True

    def test_detects_list_type(self):
        """Should return True for type ['array', 'null']."""
        from gitea_mcp_server.server_setup.tool_annotator import _schema_type_is_array

        assert _schema_type_is_array({"type": ["array", "null"]}) is True

    def test_rejects_non_array_string(self):
        """Should return False for non-array string types."""
        from gitea_mcp_server.server_setup.tool_annotator import _schema_type_is_array

        assert _schema_type_is_array({"type": "string"}) is False
        assert _schema_type_is_array({"type": "object"}) is False

    def test_rejects_non_array_list(self):
        """Should return False when 'array' not in type list."""
        from gitea_mcp_server.server_setup.tool_annotator import _schema_type_is_array

        assert _schema_type_is_array({"type": ["string", "null"]}) is False

    def test_no_type_key(self):
        """Should return False when no type key."""
        from gitea_mcp_server.server_setup.tool_annotator import _schema_type_is_array

        assert _schema_type_is_array({}) is False


class TestFormatAvailableLabels:
    """Tests for _format_available_labels."""

    def test_groups_labels_by_prefix(self):
        """Labels with same prefix should be grouped together."""
        labels = ["type/bug", "priority/high", "type/feature", "priority/low", "status/triage"]
        result = _format_available_labels(labels)
        assert "type/bug, type/feature" in result
        assert "priority/high, priority/low" in result
        assert "status/triage" in result

    def test_labels_without_prefix(self):
        """Labels without a '/' should be grouped under empty prefix."""
        labels = ["urgent", "type/bug", "wontfix"]
        result = _format_available_labels(labels)
        assert "urgent, wontfix" in result
        assert "type/bug" in result

    def test_single_label(self):
        """Single label should produce one line."""
        result = _format_available_labels(["type/bug"])
        assert result == "  - type/bug"

    def test_empty_list(self):
        """Empty list should produce empty string."""
        result = _format_available_labels([])
        assert result == ""


class TestConvertLabels:
    """Tests for _convert_labels."""

    @pytest.fixture
    def _gitea_client(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_converts_known_string_labels_to_ids(self, _gitea_client):
        """Known string label names should be converted to integer IDs."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
            "type/feature": {"id": 2, "name": "type/feature"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug", "type/feature"]}
        await _convert_labels(kwargs, True, MagicMock(), label_manager, _gitea_client)

        assert kwargs["labels"] == [1, 2]

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_unknown_labels(self, _gitea_client):
        """Unknown label names should raise ValidationError with available labels."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
            "type/feature": {"id": 2, "name": "type/feature"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/nonexistent"]}
        with pytest.raises(ValidationError) as excinfo:
            await _convert_labels(kwargs, True, MagicMock(), label_manager, _gitea_client)

        msg = str(excinfo.value)
        assert "type/nonexistent" in msg
        assert "test-owner/test-repo" in msg
        assert "type/bug" in msg
        assert "type/feature" in msg
        assert excinfo.value.field == "labels"

    @pytest.mark.asyncio
    async def test_preserves_integer_labels(self):
        """Integer labels should be passed through unchanged."""
        label_manager = AsyncMock(spec=LabelManager)

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": [1, 2, 3]}
        await _convert_labels(kwargs, True, MagicMock(), label_manager)

        assert kwargs["labels"] == [1, 2, 3]
        label_manager.get_label_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_has_labels_is_false(self):
        """When has_labels is False, no conversion should happen."""
        kwargs = {"labels": ["type/bug"]}
        await _convert_labels(kwargs, False, MagicMock(), MagicMock())
        assert kwargs["labels"] == ["type/bug"]

    @pytest.mark.asyncio
    async def test_skips_when_labels_not_in_kwargs(self):
        """When labels key is missing from kwargs, no conversion should happen."""
        kwargs = {"owner": "test-owner", "repo": "test-repo"}
        await _convert_labels(kwargs, True, MagicMock(), MagicMock())
        assert "labels" not in kwargs

    @pytest.mark.asyncio
    async def test_skips_when_owner_missing(self):
        """When owner is missing, no conversion should happen."""
        label_manager = AsyncMock(spec=LabelManager)

        kwargs = {"repo": "test-repo", "labels": ["type/bug"]}
        await _convert_labels(kwargs, True, MagicMock(), label_manager, AsyncMock())
        assert kwargs["labels"] == ["type/bug"]
        label_manager.get_label_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_gitea_client_missing(self):
        """When gitea_client is None, no conversion should happen."""
        label_manager = AsyncMock(spec=LabelManager)

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug"]}
        await _convert_labels(kwargs, True, MagicMock(), label_manager, gitea_client=None)
        assert kwargs["labels"] == ["type/bug"]
        label_manager.get_label_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_mixed_strings_and_integers(self, _gitea_client):
        """Mixed string and integer labels should all be converted/preserved."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug", 42]}
        await _convert_labels(kwargs, True, MagicMock(), label_manager, _gitea_client)

        assert kwargs["labels"] == [1, 42]

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, _gitea_client):
        """Label matching should be case-insensitive."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "kind/enhancement": {"id": 5, "name": "Kind/Enhancement"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["Kind/Enhancement"]}
        await _convert_labels(kwargs, True, MagicMock(), label_manager, _gitea_client)

        assert kwargs["labels"] == [5]

    @pytest.mark.asyncio
    async def test_formats_error_with_grouped_labels(self, _gitea_client):
        """Error message should group available labels by prefix."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
            "type/feature": {"id": 2, "name": "type/feature"},
            "priority/high": {"id": 3, "name": "priority/high"},
            "priority/low": {"id": 4, "name": "priority/low"},
        }

        kwargs = {"owner": "my-org", "repo": "my-repo", "labels": ["bad/label"]}
        with pytest.raises(ValidationError) as excinfo:
            await _convert_labels(kwargs, True, MagicMock(), label_manager, _gitea_client)

        msg = str(excinfo.value)
        assert "my-org/my-repo" in msg
        assert "  - priority/high, priority/low" in msg
        assert "  - type/bug, type/feature" in msg


class TestErrorHandlingEnhancement:
    """Tests for enhanced error handling using OpenAPI response schemas."""

    @pytest.mark.asyncio
    async def test_formats_404_error_using_openapi_spec(self):
        """When component.run raises a 404, transform_fn should format a clean message using the OpenAPI spec's response description."""
        import httpx

        # Minimal OpenAPI spec with a 404 response definition for the endpoint
        openapi_spec = {
            "paths": {
                "/repos/{owner}/{repo}/pulls": {
                    "post": {
                        "responses": {
                            "404": {
                                "description": "APINotFound: The specified repository or resource does not exist."
                            }
                        }
                    }
                }
            }
        }

        # Create a mock route for the PR creation endpoint
        route = MagicMock(
            path="/repos/{owner}/{repo}/pulls",
            method="POST",
            summary="Create a pull request",
            operation_id="repo_create_pull_request",
        )

        # Create a mock OpenAPITool with necessary attributes
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_create_pull_request"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "head": {"type": "string"},
                "base": {"type": "string"},
            }
        }
        tool.output_schema = None
        tool.description = "Create a pull request"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Simulate HTTP 404 error with a realistic response body
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.reason_phrase = "Not Found"
        error_body = {
            "message": "The target couldn't be found.",
            "errors": [
                "could not find 'feature/74-retry-after-header' to be a commit, branch or tag in the head repository mcp-server/gitea-mcp-server"
            ],
            "url": "https://git.home.lan/api/v1/repos/mcp-server/gitea-mcp-server/pulls",
        }
        mock_response.json.return_value = error_body

        http_error = httpx.HTTPStatusError("404 Not Found", request=None, response=mock_response)
        value_error = ValueError(f"HTTP error 404: {mock_response.reason_phrase} - {error_body}")
        value_error.__cause__ = http_error

        tool.run = AsyncMock(side_effect=value_error)

        # Call customize_component with openapi_spec
        new_tool = _customize_component(route, tool, _label_manager, openapi_spec)

        # Call the transformed tool with necessary arguments
        with pytest.raises(ValueError) as exc_info:
            await new_tool.run(
                {
                    "owner": "mcp-server",
                    "repo": "gitea-mcp-server",
                    "head": "feature/test",
                    "base": "main",
                }
            )

        error_msg = str(exc_info.value)
        # Should include description from OpenAPI spec
        assert "APINotFound" in error_msg
        # Should include message from response body
        assert "The target couldn't be found." in error_msg
        # Should not contain raw "HTTP error 404" format
        assert "HTTP error 404" not in error_msg

    @pytest.mark.asyncio
    async def test_non_http_errors_unchanged(self):
        """Non-HTTP ValueErrors should be re-raised without modification."""

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Raise a ValueError that is NOT from an HTTPStatusError
        value_error = ValueError("Some unrelated validation error")
        tool.run = AsyncMock(side_effect=value_error)

        new_tool = _customize_component(route, tool, _label_manager, openapi_spec)

        with pytest.raises(ValueError) as exc_info:
            await new_tool.run({})

        assert str(exc_info.value) == "Some unrelated validation error"

    @pytest.mark.asyncio
    async def test_formats_network_error_cleanly(self):
        """httpx.NetworkError (without response) should be formatted as a network issue."""
        import httpx

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Simulate a network error (no response attribute)
        network_error = httpx.NetworkError("Connection failed")
        tool.run = AsyncMock(side_effect=network_error)

        new_tool = _customize_component(route, tool, _label_manager, openapi_spec)

        with pytest.raises(ValueError) as exc_info:
            await new_tool.run({})

        error_msg = str(exc_info.value)
        assert "Network error" in error_msg or "Could not connect" in error_msg
        assert "Connection failed" in error_msg

    @pytest.mark.asyncio
    async def test_formats_timeout_error_cleanly(self):
        """httpx.TimeoutException should be formatted as a timeout issue."""
        import httpx

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        timeout_error = httpx.TimeoutException("Request timed out")
        tool.run = AsyncMock(side_effect=timeout_error)

        new_tool = _customize_component(route, tool, _label_manager, openapi_spec)

        with pytest.raises(ValueError) as exc_info:
            await new_tool.run({})

        error_msg = str(exc_info.value)
        assert "timeout" in error_msg.lower() or "timed out" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_formats_unexpected_exception_cleanly(self):
        """Unexpected exceptions (RuntimeError, etc.) should be caught and formatted."""

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Simulate an unexpected error
        unexpected_error = RuntimeError("Something unexpected happened")
        tool.run = AsyncMock(side_effect=unexpected_error)

        new_tool = _customize_component(route, tool, _label_manager, openapi_spec)

        with pytest.raises(ValueError) as exc_info:
            await new_tool.run({})

        error_msg = str(exc_info.value)
        # Should be user-friendly, not expose raw exception type by default
        assert "unexpected" in error_msg.lower()
        # Should not show full Python traceback to user
        assert "RuntimeError" not in error_msg


class TestDeriveOutputSchema:
    """Tests for derive_output_schema function."""

    MINIMAL_SPEC: dict = {
        "openapi": "3.1.0",
        "paths": {
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
                                            "body": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    }
                },
                "delete": {
                    "responses": {
                        "204": {"description": "No Content"},
                    }
                },
            },
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
        },
        "components": {
            "schemas": {
                "Repository": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                }
            },
            "responses": {
                "Repository": {
                    "description": "Repository",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Repository"}
                        }
                    },
                }
            },
        },
    }

    def _make_route(self, path: str, method: str = "GET") -> MagicMock:
        """Helper to create a mock route."""
        return MagicMock(path=path, method=method, summary="Test", operation_id="test_op")

    def test_inline_schema_response(self):
        """Should extract inline schema directly from response content."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)

        assert schema is not None
        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "title" in schema["properties"]

    def test_array_response(self):
        """Should handle array-type response schemas."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues", "GET")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)

        assert schema is not None
        assert schema["type"] == "array"
        assert schema["items"]["type"] == "object"

    def test_ref_response_resolved(self):
        """Should resolve $ref in response to get the schema."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        spec_with_ref: dict = {
            "openapi": "3.1.0",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {
                        "responses": {
                            "200": {"$ref": "#/components/responses/Repository"}
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Repository": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    }
                },
                "responses": {
                    "Repository": {
                        "description": "Repository",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Repository"}
                            }
                        },
                    }
                },
            },
        }

        route = self._make_route("/repos/{owner}/{repo}", "GET")
        schema = derive_output_schema(route, spec_with_ref)

        assert schema is not None
        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "name" in schema["properties"]

    def test_no_content_response_returns_none(self):
        """204 No Content responses should return None."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "DELETE")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)
        assert schema is None

    def test_none_spec_returns_none(self):
        """When spec is None, should return None."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        route = self._make_route("/test", "GET")
        schema = derive_output_schema(route, None)
        assert schema is None

    def test_missing_path_returns_none(self):
        """When route path is not in spec, should return None."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        route = self._make_route("/nonexistent/path", "GET")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)
        assert schema is None

    def test_missing_method_returns_none(self):
        """When route method is not in spec, should return None."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "PATCH")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)
        assert schema is None

    def test_prefers_200_over_201(self):
        """Should prefer 200 over 201 when both are present."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        spec: dict = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "post": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"from_200": {"type": "string"}}}
                                    }
                                },
                            },
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"from_201": {"type": "string"}}}
                                    }
                                },
                            },
                        }
                    }
                }
            },
        }

        route = self._make_route("/test", "POST")
        schema = derive_output_schema(route, spec)
        assert schema is not None
        assert "from_200" in schema["properties"]
        assert "from_201" not in schema["properties"]

    def test_falls_back_to_201_when_no_200(self):
        """Should fall back to 201 when no 200 response exists."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            derive_output_schema,
        )

        spec: dict = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "post": {
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }

        route = self._make_route("/test", "POST")
        schema = derive_output_schema(route, spec)
        assert schema is not None
        assert "id" in schema["properties"]

    def test_integration_via_customize_component(self):
        """customize_component should set output_schema from openapi_spec."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.openapi_converter import _wrap_success_response_schemas
        from gitea_mcp_server.server_setup.tool_annotator import (
            customize_component,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get an issue"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        spec = deepcopy(self.MINIMAL_SPEC)
        _wrap_success_response_schemas(spec)
        new_tool = customize_component(route, tool, _label_manager, spec)

        assert new_tool is not None
        assert new_tool.output_schema is not None
        assert new_tool.output_schema["type"] == "object"
        assert "result" in new_tool.output_schema["properties"]
        assert "id" in new_tool.output_schema["properties"]["result"]["properties"]
        assert "title" in new_tool.output_schema["properties"]["result"]["properties"]

    def test_integration_no_output_schema_without_spec(self):
        """customize_component should not set output_schema when spec is None."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.server_setup.tool_annotator import (
            customize_component,
        )

        route = self._make_route("/test", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = customize_component(route, tool, _label_manager, None)

        assert new_tool is not None
        assert new_tool.output_schema is None

    @pytest.mark.asyncio
    async def test_transform_fn_wraps_result_in_result_key(self):
        """transform_fn should wrap tool result in {'result': ...}."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.server_setup.tool_annotator import (
            customize_component,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
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
        tool.run = AsyncMock(return_value=[{"id": 1}, {"id": 2}])

        new_tool = customize_component(route, tool, _label_manager, self.MINIMAL_SPEC)

        actual = await new_tool.run({"owner": "test", "repo": "test"})
        assert actual.structured_content == {"result": [{"id": 1}, {"id": 2}]}

    @pytest.mark.asyncio
    async def test_object_response_wrapped_by_openapi_tool_via_x_fastmcp(self):
        """When component.output_schema has x-fastmcp-wrap-result, OpenAPITool.run()
        wraps ALL responses in {'result': ...}. The ToolResult flows through
        transform_fn → TransformedTool.run() unchanged."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.server_setup.tool_annotator import (
            customize_component,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        # Mimics enriched spec schema.
        tool.output_schema = {"type": "object", "properties": {"result": {"type": "object", "properties": {"id": {"type": "integer"}}}}}
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        # After customize_component sets x-fastmcp-wrap-result on component,
        # OpenAPITool.run() would wrap the response. Simulate that.
        tool.run = AsyncMock(return_value=ToolResult(structured_content={"result": {"id": 1}}))

        new_tool = customize_component(route, tool, _label_manager, self.MINIMAL_SPEC)

        actual = await new_tool.run({"owner": "test", "repo": "test"})
        assert actual.structured_content == {"result": {"id": 1}}

    @pytest.mark.asyncio
    async def test_array_wrapped_by_openapi_tool_even_without_x_fastmcp(self):
        """OpenAPITool.run() wraps arrays in {'result': [...]} even without
        x-fastmcp-wrap-result (for MCP protocol compliance)."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.server_setup.tool_annotator import (
            customize_component,
        )

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = {"type": "object", "properties": {"result": {"type": "array"}}}
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        # OpenAPITool.run() wraps non-dict in {"result": ...}
        tool.run = AsyncMock(return_value=ToolResult(structured_content={"result": [{"id": 1}]}))

        new_tool = customize_component(route, tool, _label_manager, self.MINIMAL_SPEC)

        actual = await new_tool.run({"owner": "test", "repo": "test"})
        assert actual.structured_content == {"result": [{"id": 1}]}


class TestDeepResolveSchema:
    """Tests for _deep_resolve_schema function."""

    SPEC: dict = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "login": {"type": "string"},
                    },
                },
                "Repository": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "owner": {"$ref": "#/components/schemas/User"},
                    },
                },
                "NestedRef": {
                    "type": "object",
                    "properties": {
                        "repo": {"$ref": "#/components/schemas/Repository"},
                    },
                },
                "AllOfSchema": {
                    "allOf": [
                        {"$ref": "#/components/schemas/User"},
                        {"type": "object", "properties": {"extra": {"type": "string"}}},
                    ],
                },
                "ArraySchema": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/User"},
                },
            },
        },
    }

    def test_resolves_nested_property_refs(self):
        """Resolves $ref inside property values."""
        from gitea_mcp_server.server_setup.tool_annotator import _deep_resolve_schema

        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "user": {"$ref": "#/components/schemas/User"},
            },
        }
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["properties"]["user"]["type"] == "object"
        assert resolved["properties"]["user"]["properties"]["id"]["type"] == "integer"
        assert resolved["properties"]["user"]["properties"]["login"]["type"] == "string"

    def test_resolves_items_ref(self):
        """Resolves $ref in array items."""
        from gitea_mcp_server.server_setup.tool_annotator import _deep_resolve_schema

        schema = {
            "type": "array",
            "items": {"$ref": "#/components/schemas/User"},
        }
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["items"]["type"] == "object"
        assert "id" in resolved["items"]["properties"]

    def test_resolves_chain_of_refs(self):
        """Resolves $ref chains (Repo -> User -> no more refs)."""
        from gitea_mcp_server.server_setup.tool_annotator import _deep_resolve_schema

        schema = {"$ref": "#/components/schemas/NestedRef"}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["type"] == "object"
        assert resolved["properties"]["repo"]["type"] == "object"
        assert resolved["properties"]["repo"]["properties"]["owner"]["type"] == "object"
        assert resolved["properties"]["repo"]["properties"]["owner"]["properties"]["login"]["type"] == "string"

    def test_resolves_allOf_entries(self):
        """Recursively resolves $ref inside allOf entries."""
        from gitea_mcp_server.server_setup.tool_annotator import _deep_resolve_schema

        schema = {"$ref": "#/components/schemas/AllOfSchema"}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["allOf"][0]["type"] == "object"
        assert resolved["allOf"][0]["properties"]["id"]["type"] == "integer"

    def test_resolves_top_level_ref(self):
        """Resolves a top-level $ref."""
        from gitea_mcp_server.server_setup.tool_annotator import _deep_resolve_schema

        schema = {"$ref": "#/components/schemas/User"}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["type"] == "object"
        assert resolved["properties"]["id"]["type"] == "integer"
        assert resolved["properties"]["login"]["type"] == "string"

    def test_leaf_schema_unchanged(self):
        """A schema with no refs should return a copy unchanged."""
        from gitea_mcp_server.server_setup.tool_annotator import _deep_resolve_schema

        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved == schema

    def test_circular_ref_does_not_loop(self):
        """Circular $ref should not cause infinite recursion."""
        from gitea_mcp_server.server_setup.tool_annotator import _deep_resolve_schema

        circular_spec = {
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "child": {"$ref": "#/components/schemas/Node"},
                        },
                    },
                },
            },
        }
        schema = {"$ref": "#/components/schemas/Node"}
        resolved = _deep_resolve_schema(schema, circular_spec)
        assert resolved["type"] == "object"
        assert resolved["properties"]["id"]["type"] == "integer"
        assert resolved["properties"]["child"]["$ref"] == "#/components/schemas/Node"

    def test_deep_resolve_applied_in_derive_output_schema(self):
        """derive_output_schema should deep-resolve nested refs."""
        from gitea_mcp_server.server_setup.tool_annotator import derive_output_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "owner": {"$ref": "#/components/schemas/User"},
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
            },
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "login": {"type": "string"},
                        },
                    },
                },
            },
        }
        route = MagicMock(path="/repos/{owner}/{repo}", method="GET")
        schema = derive_output_schema(route, spec)
        assert schema is not None
        assert schema["properties"]["owner"]["type"] == "object"
        assert schema["properties"]["owner"]["properties"]["login"]["type"] == "string"


class TestCallToolOutputSchema:
    """Tests for call_tool output_schema."""

    def test_call_tool_has_output_schema(self):
        """_make_call_tool should return a Tool with output_schema set."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "result" in tool.output_schema["properties"]
        # call_tool does NOT set x-fastmcp-wrap-result — it passes through
        # the inner tool's already-wrapped ToolResult, so the flag would
        # be a no-op (dead code).  Inner tools handle their own wrapping.
        assert "x-fastmcp-wrap-result" not in tool.output_schema

    def test_call_tool_result_property_accepts_any_type(self):
        """The 'result' property must not have a 'type' constraint (accepts arrays, etc.)."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        result_schema = tool.output_schema["properties"]["result"]
        # No "type" key means any JSON value is accepted (objects, arrays, strings, etc.)
        assert "type" not in result_schema, (
            f"Expected result to accept any type, got 'type': {result_schema.get('type')!r}"
        )


class TestSchemaToExample:
    """Tests for _schema_to_example function."""

    def test_object_with_properties(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "active": {"type": "boolean"},
                "score": {"type": "number"},
            },
        }
        result = _schema_to_example(schema)
        assert isinstance(result, dict)
        assert result["id"] == 0
        assert result["name"] == "text"
        assert result["active"] is True
        assert result["score"] == 0.0

    def test_uses_schema_example(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {
            "type": "object",
            "properties": {
                "color": {"type": "string", "example": "00aabb"},
            },
        }
        result = _schema_to_example(schema)
        assert result["color"] == "00aabb"

    def test_array_type(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {
            "type": "array",
            "items": {"type": "string"},
        }
        result = _schema_to_example(schema)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == "text"

    def test_string_with_enum(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {"type": "string", "enum": ["open", "closed"]}
        assert _schema_to_example(schema) == "open"

    def test_string_with_format_date_time(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {"type": "string", "format": "date-time"}
        result = _schema_to_example(schema)
        assert "2024-01-01" in result
        assert "T" in result

    def test_anyof_skips_null(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {
            "anyOf": [
                {"type": "null"},
                {"type": "string"},
            ],
        }
        assert _schema_to_example(schema) == "text"

    def test_type_list_skips_null(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {"type": ["null", "string"]}
        assert _schema_to_example(schema) == "text"

    def test_depth_limit(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {
                                "c": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
        result = _schema_to_example(schema, max_depth=2)
        # At max_depth, nested objects return {}
        assert result["a"]["b"] == {}

    def test_property_count_limit(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(20)},
        }
        result = _schema_to_example(schema, max_properties=5)
        assert len(result) == 5

    def test_null_type(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        assert _schema_to_example({"type": "null"}) is None

    def test_non_dict_schema_raises(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        with pytest.raises(AttributeError):
            _schema_to_example("not a dict")  # type: ignore[arg-type]

    def test_nested_object_in_array(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string"},
                },
            },
        }
        result = _schema_to_example(schema)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 0
        assert result[0]["label"] == "text"

    def test_empty_object(self):
        from gitea_mcp_server.server_setup.tool_annotator import _schema_to_example

        assert _schema_to_example({"type": "object", "properties": {}}) == {}

    def test_serialize_tool_schema_uses_output_example(self):
        """_serialize_tool_schema should produce output_example instead of output_schema."""
        from fastmcp.tools.base import Tool

        from gitea_mcp_server.server_setup.tool_annotator import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {"x": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        )
        result = _serialize_tool_schema(tool)
        assert "output_example" in result
        assert "output_schema" not in result
        assert result["output_example"]["id"] == 0
        assert result["output_example"]["name"] == "text"

    def test_serialize_tool_schema_no_output_schema(self):
        """_serialize_tool_schema should not include output_example when output_schema is None."""
        from fastmcp.tools.base import Tool

        from gitea_mcp_server.server_setup.tool_annotator import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {}},
            output_schema=None,
        )
        result = _serialize_tool_schema(tool)
        assert "output_example" not in result
        assert "output_schema" not in result


class TestCallToolRuntimeBehavior:
    """Test runtime behavior of the call_tool function.

    call_tool is a proxy that delegates to ctx.fastmcp.call_tool().
    These tests verify it correctly passes ToolResult through without
    double-wrapping, and properly handles argument validation.
    """

    @pytest.mark.asyncio
    async def test_call_tool_passes_toolresult_through(self):
        """call_tool function should return the inner tool's ToolResult as-is."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": 1}, {"id": 2}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        result = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx)

        assert result is inner_result, "call_tool must return the exact ToolResult from inner tool"
        assert result.structured_content == {"result": [{"id": 1}, {"id": 2}]}

    @pytest.mark.asyncio
    async def test_call_tool_no_double_wrap_through_convert_result(self):
        """convert_result must not double-wrap a ToolResult returned by call_tool."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(
            content=[],
            structured_content={"result": {"items": [1, 2, 3], "count": 3}},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        raw = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx)
        final = tool.convert_result(raw)

        assert final is inner_result, "convert_result must pass ToolResult through unchanged"
        assert final.structured_content == {"result": {"items": [1, 2, 3], "count": 3}}
        inner = final.structured_content["result"]
        assert "result" not in inner, (
            f"Double-wrapped! structured_content={final.structured_content}"
        )

    @pytest.mark.asyncio
    async def test_call_tool_preserves_user_meta_from_inner_tool(self):
        """call_tool should preserve meta from the inner tool's ToolResult."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_meta = {"fastmcp": {"wrap_result": True}, "custom": "data"}
        inner_result = ToolResult(
            content=[],
            structured_content={"result": {}},
            meta=inner_meta,
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        raw = await tool.fn("gitea_test_tool", {"arg": "val"}, ctx=mock_ctx)
        final = tool.convert_result(raw)

        assert final.meta == inner_meta

    @pytest.mark.asyncio
    async def test_call_tool_rejects_self_call(self):
        """call_tool should reject calling itself or search_tools."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="synthetic search tool"):
            await tool.fn(transform._call_tool_name, {}, ctx=mock_ctx)

        with pytest.raises(ValueError, match="synthetic search tool"):
            await tool.fn(transform._search_tool_name, {}, ctx=mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_parses_json_string_arguments(self):
        """String arguments should be parsed as JSON before forwarding."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(content=[], structured_content={"result": {}})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        await tool.fn("gitea_test_tool", '{"key": "val", "num": 42}', ctx=mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with(
            "gitea_test_tool", {"key": "val", "num": 42}
        )

    @pytest.mark.asyncio
    async def test_call_tool_rejects_non_dict_and_non_string_arguments(self):
        """Arguments that are neither dict nor None nor a JSON string should be rejected."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await tool.fn("gitea_test_tool", [1, 2, 3], ctx=mock_ctx)

        with pytest.raises(ValueError, match="Arguments must be a dict"):
            await tool.fn("gitea_test_tool", 42, ctx=mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_rejects_invalid_json(self):
        """Invalid JSON string arguments should be rejected."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()
        mock_ctx = MagicMock()

        with pytest.raises(ValueError, match="Invalid JSON"):
            await tool.fn("gitea_test_tool", "{bad json}", ctx=mock_ctx)

    @pytest.mark.asyncio
    async def test_call_tool_handles_none_arguments(self):
        """None arguments should be forwarded as None."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(content=[], structured_content={"result": []})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        await tool.fn("gitea_test_tool", None, ctx=mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with("gitea_test_tool", None)

    @pytest.mark.asyncio
    async def test_call_tool_handles_missing_arguments(self):
        """Omitting arguments should forward None."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(content=[], structured_content={"result": []})
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        await tool.fn("gitea_test_tool", ctx=mock_ctx)
        mock_ctx.fastmcp.call_tool.assert_called_once_with("gitea_test_tool", None)

    @pytest.mark.asyncio
    async def test_call_tool_routes_array_result_from_inner_tool(self):
        """When inner tool returns an array wrapped in {"result": [...]}, pass through."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            TolerantSearchTransform,
        )

        transform = TolerantSearchTransform()
        tool = transform._make_call_tool()

        inner_result = ToolResult(
            content=[],
            structured_content={"result": [{"id": "a"}, {"id": "b"}]},
            meta={"fastmcp": {"wrap_result": True}},
        )
        mock_ctx = MagicMock()
        mock_ctx.fastmcp.call_tool = AsyncMock(return_value=inner_result)

        raw = await tool.fn("gitea_array_tool", ctx=mock_ctx)
        final = tool.convert_result(raw)

        assert final.structured_content == {"result": [{"id": "a"}, {"id": "b"}]}


class TestFunctionToolResultWrapping:
    """Test that FunctionTool.convert_result() wraps when x-fastmcp-wrap-result is set.

    This mirrors the exact pattern used by ``mcp_list_resources`` and
    ``mcp_read_resource`` (``@mcp.tool(output_schema={..., "x-fastmcp-wrap-result": True})``).
    """

    MOCK_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {
                    "resources": {"type": "array"},
                    "count": {"type": "integer"},
                },
            },
        },
        "x-fastmcp-wrap-result": True,
    }

    @pytest.mark.asyncio
    async def test_convert_result_wraps_dict_with_x_fastmcp(self):
        """convert_result should wrap return value in {'result': ...}."""
        from fastmcp.tools.base import Tool
        from fastmcp.tools.function_parsing import ParsedFunction

        tool = Tool(
            name="test_list",
            description="Test list",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = {"resources": [{"uri": "gitea://test"}], "count": 1}
        result = tool.convert_result(raw)

        assert isinstance(result, ToolResult)
        assert result.structured_content == {"result": {"resources": [{"uri": "gitea://test"}], "count": 1}}

    @pytest.mark.asyncio
    async def test_convert_result_wraps_array_with_x_fastmcp(self):
        """convert_result should wrap arrays too."""
        from fastmcp.tools.base import Tool

        tool = Tool(
            name="test_array",
            description="Test array",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = [{"id": 1}, {"id": 2}]
        result = tool.convert_result(raw)

        assert isinstance(result, ToolResult)
        assert result.structured_content == {"result": [{"id": 1}, {"id": 2}]}

    @pytest.mark.asyncio
    async def test_convert_result_sets_meta_when_wrapping(self):
        """When wrapping, meta should be set to bypass MCP SDK validation."""
        from fastmcp.tools.base import Tool

        tool = Tool(
            name="test_meta",
            description="Test meta",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = {"resources": [], "count": 0}
        result = tool.convert_result(raw)

        assert result.meta == {"fastmcp": {"wrap_result": True}}

    def test_convert_result_no_wrap_without_flag(self):
        """Without x-fastmcp-wrap-result, structured_content should not be wrapped."""
        from fastmcp.tools.base import Tool

        schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {
                        "resources": {"type": "array"},
                        "count": {"type": "integer"},
                    },
                },
            },
        }

        tool = Tool(
            name="test_nowrap",
            description="Test no wrap",
            parameters={"properties": {}},
            output_schema=schema,
        )

        raw = {"resources": [], "count": 0}
        result = tool.convert_result(raw)

        assert result.structured_content == {"resources": [], "count": 0}
        assert result.meta is None

    @pytest.mark.asyncio
    async def test_to_mcp_result_returns_calltoolresult_when_meta_set(self):
        """When meta is set (wrapping active), to_mcp_result should return
        CallToolResult directly to bypass MCP SDK output validation."""
        from fastmcp.tools.base import Tool

        tool = Tool(
            name="test_calltool",
            description="Test CallToolResult",
            parameters={"properties": {}},
            output_schema=deepcopy(self.MOCK_SCHEMA),
        )

        raw = {"resources": [], "count": 0}
        result = tool.convert_result(raw)
        mcp_result = result.to_mcp_result()

        from mcp.types import CallToolResult
        assert isinstance(mcp_result, CallToolResult)
        assert mcp_result.structuredContent == {"result": {"resources": [], "count": 0}}


class TestCompactSearchSerializer:
    """Tests for _compact_search_serializer function."""

    def test_returns_name_and_description_only(self):
        """Search results should only include name and description."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            _compact_search_serializer,
        )

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {"id": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
        )
        result = _compact_search_serializer([tool])
        assert len(result) == 1
        assert result[0]["name"] == "test_tool"
        assert result[0]["description"] == "A test tool"
        assert "parameters" not in result[0]
        assert "output_schema" not in result[0]
        assert "output_example" not in result[0]

    def test_handles_empty_fields(self):
        """Should handle tools with minimal fields."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            _compact_search_serializer,
        )

        tool = Tool(
            name="minimal_tool",
            description="",
            parameters={"properties": {}},
            output_schema=None,
        )
        result = _compact_search_serializer([tool])
        assert result[0]["name"] == "minimal_tool"
        assert result[0]["description"] == ""

    def test_handles_multiple_tools(self):
        """Should serialize multiple tools correctly."""
        from gitea_mcp_server.server_setup.tool_annotator import (
            _compact_search_serializer,
        )

        tools = [
            Tool(name="tool_a", description="First tool", parameters={"properties": {}}),
            Tool(name="tool_b", description="Second tool", parameters={"properties": {}}),
        ]
        result = _compact_search_serializer(tools)
        assert len(result) == 2
        assert result[0]["name"] == "tool_a"
        assert result[1]["name"] == "tool_b"
