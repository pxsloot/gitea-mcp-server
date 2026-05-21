"""Unit tests for tool annotation functionality."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.constants import LABEL_GUIDANCE, TITLE_TRUNCATE_LIMIT
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.tool_annotator import (
    NAME_BOOST,
    _extract_searchable_text_enhanced,
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
        """Word aliases should expand singular/plural and synonym variations."""
        tool = Tool(
            name="test_repo_tool",
            description="Manage repositories",
            parameters={"properties": {}},
        )
        result = _extract_searchable_text_enhanced(tool)
        # "repo" in name triggers expansion to "repo repository repos"
        assert "repos" in result or "repository" in result

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
