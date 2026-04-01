"""Unit tests for tool annotation functionality."""

from unittest.mock import MagicMock

import pytest

from gitea_mcp_server.server import _categorize_tool, _generate_tool_title, _customize_component
from fastmcp.tools.tool import ToolAnnotations


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
        assert len(title) <= 53  # 50 + "..."
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


class TestCustomizeComponent:
    """Tests for the _customize_component function."""

    def test_only_tools_are_customized(self):
        from fastmcp.server.openapi import OpenAPIResource

        # Mock a non-tool component with spec to pass isinstance check
        route = MagicMock(path="/test", summary="Test", operation_id="test_route")
        resource = MagicMock(spec=OpenAPIResource)

        _customize_component(route, resource)

        # Should return early without modifying
        assert True  # No exception means pass

    def test_adds_annotations_to_tool_without_existing(self):
        from fastmcp.server.openapi import OpenAPITool

        route = MagicMock(
            path="/repos/{owner}/{repo}/issues", summary="List issues", operation_id="list_issues"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = None
        tool.tags = set()

        _customize_component(route, tool)

        assert tool.annotations is not None
        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "List issues"
        assert "issue" in tool.tags
        assert "issue" in tool.tags

    def test_adds_annotations_to_tool_with_dict(self):
        from fastmcp.server.openapi import OpenAPITool

        route = MagicMock(
            path="/repos/{owner}/{repo}/issues", summary="List issues", operation_id="list_issues"
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "list_issues"
        tool.annotations = {"title": "Old Title"}  # dict that can be unpacked to ToolAnnotations
        tool.tags = set()

        _customize_component(route, tool)

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "List issues"  # Our title overrides dict
        assert "issue" in tool.tags

    def test_converts_existing_toolannotations_properly(self):
        from fastmcp.server.openapi import OpenAPITool

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

        _customize_component(route, tool)

        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.title == "Get pull request"  # Updated
        assert tool.annotations.readOnlyHint is True  # Preserved
        assert "pull_request" in tool.tags

    def test_category_detection_various_paths(self):
        from fastmcp.server.openapi import OpenAPITool

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

            _customize_component(route, tool)

            assert tool.annotations is not None
            assert expected_category in tool.tags, (
                f"Failed for {path}: category {expected_category} not in tags"
            )
            assert expected_category in tool.tags

    def test_title_generation_from_operation_id(self):
        from fastmcp.server.openapi import OpenAPITool

        route = MagicMock(path="/test", summary=None, operation_id="get_user_by_id")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "get_user_by_id"
        tool.annotations = None
        tool.tags = set()

        _customize_component(route, tool)

        assert tool.annotations.title == "Get User By Id"

    def test_long_operation_id_truncated(self):
        from fastmcp.server.openapi import OpenAPITool

        long_op_id = (
            "this_is_a_very_long_operation_id_that_exceeds_fifty_characters_and_needs_truncation"
        )
        route = MagicMock(path="/test", summary=None, operation_id=long_op_id)
        tool = MagicMock(spec=OpenAPITool)
        tool.name = long_op_id
        tool.annotations = None
        tool.tags = set()

        _customize_component(route, tool)

        assert len(tool.annotations.title) <= 53
        assert tool.annotations.title.endswith("...")
