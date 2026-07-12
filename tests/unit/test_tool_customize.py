"""Unit tests for tool customization (categorize, title, hints, metadata, wrapping)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations


from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.pagination import pagination_ctx
from gitea_mcp_server.server_setup.mcp_builder import (
    _customize_metadata,
    _ToolWrappingTransform,
)
from gitea_mcp_server.tools.customize import (
    _is_array_response,
    _snake_to_title,
    add_inferred_hints as _add_inferred_hints,
    categorize_tool as _categorize_tool,
    generate_tool_title as _generate_tool_title,
)


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
    """Tests for generate_tool_title (uses operationId)."""

    def test_with_summary_ignored(self):
        """The summary field is no longer used for the title."""
        route = MagicMock(summary="This is a long summary that would have been truncated before", operation_id="issue_create_issue")
        title = _generate_tool_title(route)
        assert title == "Create Issue"

    def test_uses_operation_id(self):
        route = MagicMock(summary="ignored", operation_id="issue_create_issue")
        title = _generate_tool_title(route)
        assert title == "Create Issue"

    def test_repo_list_pull_requests(self):
        route = MagicMock(summary="ignored", operation_id="repo_list_pull_requests")
        title = _generate_tool_title(route)
        assert title == "List Pull Requests"

    def test_user_get_current(self):
        route = MagicMock(summary="ignored", operation_id="user_get_current")
        title = _generate_tool_title(route)
        assert title == "Get Current"

    def test_domain_strip_verb_only(self):
        """Single verb after domain strip appends domain noun."""
        route = MagicMock(summary="ignored", operation_id="repo_edit")
        title = _generate_tool_title(route)
        assert title == "Edit Repository"

    def test_org_create(self):
        route = MagicMock(summary="ignored", operation_id="org_create")
        title = _generate_tool_title(route)
        assert title == "Create Organization"

    def test_activitypub_kept_prefix(self):
        """activitypub domain is kept as the entity name."""
        route = MagicMock(summary="ignored", operation_id="activitypub_person")
        title = _generate_tool_title(route)
        assert title == "Activitypub Person"

    def test_empty_operation_id(self):
        route = MagicMock(summary="", operation_id="")
        title = _generate_tool_title(route)
        assert title == "Unnamed Tool"

    def test_none_operation_id(self):
        route = MagicMock(summary=None, operation_id=None)
        title = _generate_tool_title(route)
        assert title == "Unnamed Tool"


class TestSnakeToTitle:
    """Tests for the _snake_to_title helper."""

    def test_domain_verb_object(self):
        assert _snake_to_title("issue_create_issue") == "Create Issue"

    def test_domain_verb_object_compound(self):
        assert _snake_to_title("repo_list_pull_requests") == "List Pull Requests"

    def test_verb_only_appends_domain_noun(self):
        assert _snake_to_title("repo_edit") == "Edit Repository"
        assert _snake_to_title("issue_delete") == "Delete Issue"
        assert _snake_to_title("org_create") == "Create Organization"
        assert _snake_to_title("user_get") == "Get User"

    def test_unknown_domain_kept(self):
        assert _snake_to_title("render_markdown") == "Render Markdown"

    def test_activitypub_kept(self):
        assert _snake_to_title("activitypub_person") == "Activitypub Person"
        assert _snake_to_title("activitypub_instance_actor_inbox") == "Activitypub Instance Actor Inbox"

    def test_single_word(self):
        assert _snake_to_title("version") == "Version"

    def test_empty_string(self):
        assert _snake_to_title("") == "Unnamed Tool"

    def test_unknown_domain_logs_warning(self, caplog):
        """Unknown domain prefixes should log a warning."""
        import logging
        from gitea_mcp_server.tools.customize import _snake_to_title

        caplog.set_level(logging.WARNING)
        _snake_to_title("render_markdown")
        assert len(caplog.records) == 1
        assert "Unknown operationId domain 'render'" in caplog.records[0].message
        assert "render_markdown" in caplog.records[0].message

    def test_known_domain_does_not_log_warning(self, caplog):
        """Known domain prefixes should not log a warning."""
        import logging
        from gitea_mcp_server.tools.customize import _snake_to_title

        caplog.set_level(logging.WARNING)
        _snake_to_title("issue_create_issue")
        assert len(caplog.records) == 0


class TestDomainConfigConsistency:
    """All _DOMAINS entries are valid _DomainConfig instances (type-level guarantee).

    The single-dict design makes cross-collection sync errors impossible.
    This test ensures the structural invariant holds at runtime.
    """

    def test_all_values_are_domain_config(self):
        from gitea_mcp_server.tools.customize import _DOMAINS, _DomainConfig

        for key, config in _DOMAINS.items():
            assert isinstance(config, _DomainConfig), (
                f"_DOMAINS['{key}'] is not a _DomainConfig instance"
            )

    def test_strip_true_entries_have_noun(self):
        """Every strip=True entry must have a non-empty noun (structural, always true)."""
        from gitea_mcp_server.tools.customize import _DOMAINS

        for key, config in _DOMAINS.items():
            if config.strip:
                assert config.noun, (
                    f"_DOMAINS['{key}'] has strip=True but empty noun"
                )


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
        """Mini-integration check: _customize_metadata sets all hints and title when annotations are empty."""
        route = MagicMock(path="/test", method="POST", summary="Test POST", operation_id="test_post")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test_post"
        tool.annotations = ToolAnnotations()  # All fields None
        tool.tags = set()
        tool.parameters = {"properties": {}}  # Provide minimal parameters
        tool.output_schema = None
        tool.description = "Test POST"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        # All hints should be set based on method
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is False
        assert tool.annotations.openWorldHint is True
        # Title comes from operationId, not summary
        assert tool.annotations.title == "Test Post"


class TestIsArrayResponse:
    """Tests for _is_array_response function."""

    def test_detects_array_result(self):
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
        schema = {"type": "object", "properties": {}}
        assert _is_array_response(schema) is False

    def test_none_input(self):
        assert _is_array_response(None) is False

    def test_empty_dict(self):
        assert _is_array_response({}) is False

    def test_properties_not_a_dict(self):
        schema = {"type": "object", "properties": "not_a_dict"}
        assert _is_array_response(schema) is False


class TestPaginationMetadata:
    """Tests for pagination metadata injection via _ToolWrappingTransform."""

    ARRAY_OUTPUT_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "result": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
            },
        },
    }

    OBJECT_OUTPUT_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "title": {"type": "string"}},
            },
        },
    }

    def _make_transform(self):
        return _ToolWrappingTransform(
            label_manager=LabelManager(),
            openapi_spec={},
        )

    def _make_tool(
        self,
        name: str = "issue_list_issues",
        output_schema: dict | None = None,
        page_param: bool = True,
        per_page_param: bool = True,
        limit_param: bool = False,
    ) -> Tool:
        props: dict = {}
        if page_param:
            props["page"] = {"type": "integer"}
        if per_page_param:
            props["per_page"] = {"type": "integer"}
        if limit_param:
            props["limit"] = {"type": "integer"}

        return Tool(
            name=name,
            tags={"issue"},
            description="",
            parameters={"properties": props},
            output_schema=output_schema or self.ARRAY_OUTPUT_SCHEMA,
            meta={
                "_customization_applied": True,
                "_customization": {
                    "has_labels": False,
                    "is_text_response": False,
                    "route_path": "/repos/{owner}/{repo}/issues",
                    "route_method": "GET",
                },
            },
            annotations=ToolAnnotations(title="List issues"),
        )

    @pytest.mark.asyncio
    async def test_has_more_true_when_result_equals_per_page(self):
        """has_more should be True when result length equals per_page."""
        transform = self._make_transform()
        tool = self._make_tool()

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                structured_content={"result": [{"id": i} for i in range(30)]},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={"page": 1, "per_page": 30})

            assert output.structured_content["has_more"] is True
            assert output.structured_content["next_offset"] == 2
            assert output.structured_content["total_count"] is None
            assert len(output.structured_content["result"]) == 30

    @pytest.mark.asyncio
    async def test_has_more_false_when_result_less_than_per_page(self):
        """has_more should be False when result length is less than per_page."""
        transform = self._make_transform()
        tool = self._make_tool()

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                structured_content={"result": [{"id": i} for i in range(15)]},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={"page": 1, "per_page": 30})

            assert output.structured_content["has_more"] is False
            assert output.structured_content["next_offset"] is None
            assert output.structured_content["total_count"] is None

    @pytest.mark.asyncio
    async def test_defaults_when_kwargs_missing(self):
        """Should default to page=1 and limit=100 when no pagination args provided."""
        transform = self._make_transform()
        tool = self._make_tool()

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            # Return exactly 100 items, which equals the default `limit`
            mock_run.return_value = ToolResult(
                structured_content={"result": [{"id": i} for i in range(100)]},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

            assert output.structured_content["has_more"] is True
            assert output.structured_content["next_offset"] == 2

    @pytest.mark.asyncio
    async def test_uses_limit_parameter(self):
        """Should use 'limit' parameter as fallback when 'per_page' is not present."""
        transform = self._make_transform()
        tool = self._make_tool(limit_param=True)

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                structured_content={"result": [{"id": i} for i in range(50)]},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={"page": 1, "limit": 50})

            assert output.structured_content["has_more"] is True
            assert output.structured_content["next_offset"] == 2

    @pytest.mark.asyncio
    async def test_no_pagination_for_non_array_response(self):
        """Non-array responses should not get pagination metadata."""
        transform = self._make_transform()
        tool = self._make_tool(
            name="issue_get_issue",
            output_schema=self.OBJECT_OUTPUT_SCHEMA,
        )

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                structured_content={"result": {"id": 1, "title": "Test"}},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

            assert output.structured_content == {"result": {"id": 1, "title": "Test"}}
            assert "has_more" not in output.structured_content

    @pytest.mark.asyncio
    async def test_total_count_defaults_to_none(self):
        """total_count should be None when no pagination headers captured."""
        transform = self._make_transform()
        tool = self._make_tool()

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                structured_content={"result": [{"id": i} for i in range(5)]},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={"page": 2, "per_page": 10})

            assert output.structured_content["total_count"] is None

    @pytest.mark.asyncio
    async def test_total_count_from_headers(self):
        """total_count should be populated from pagination context var."""
        pagination_ctx.set({"total_count": 42})
        try:
            transform = self._make_transform()
            tool = self._make_tool()

            with patch(
                "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
                new_callable=AsyncMock,
            ) as mock_run:
                mock_run.return_value = ToolResult(
                    structured_content={"result": [{"id": i} for i in range(5)]},
                )

                result = await transform.list_tools([tool])
                wrapped = result[0]
                output = await wrapped.run(arguments={"page": 1, "per_page": 10})

                assert output.structured_content["total_count"] == 42
                # total_count=42, page=1, per_page=10 → more pages exist
                assert output.structured_content["has_more"] is True
                assert output.structured_content["next_offset"] == 2
        finally:
            pagination_ctx.set({})

    @pytest.mark.asyncio
    async def test_preserves_original_result_data(self):
        """Original result data should be preserved in enhanced response."""
        transform = self._make_transform()
        tool = self._make_tool()
        original_data = [{"id": 1, "title": "First"}, {"id": 2, "title": "Second"}]

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                structured_content={"result": original_data},
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={"page": 1, "per_page": 10})

            assert output.structured_content["result"] == original_data


class TestPrepareAnnotationsEdgeCases:
    """Tests for _prepare_annotations edge cases."""

    def test_non_standard_annotations_uses_fallback(self):
        """Non-standard annotations that can't construct ToolAnnotations use fallback."""
        from gitea_mcp_server.tools.customize import _prepare_annotations

        component = MagicMock()
        component.annotations = "string_annotations"

        result = _prepare_annotations(component, "Test Title")
        assert result.title == "Test Title"
        assert isinstance(result, ToolAnnotations)


class TestCustomizeComponentTextResponse:
    """Tests for text response wrapping via _ToolWrappingTransform."""

    @pytest.mark.asyncio
    async def test_text_response_strips_structured_content(self):
        """Text response transforms content and structured_content."""
        from mcp.types import TextContent

        transform = _ToolWrappingTransform(
            label_manager=LabelManager(),
            openapi_spec={},
        )

        tool = Tool(
            name="issue_list_issues",
            tags={"issue"},
            description="",
            parameters={"properties": {}},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            meta={
                "_customization_applied": True,
                "_customization": {
                    "has_labels": False,
                    "is_text_response": True,
                    "route_path": "/repos/{owner}/{repo}/issues",
                    "route_method": "GET",
                },
            },
            annotations=ToolAnnotations(title="List issues"),
        )

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="raw text output")],
                structured_content=None,
            )

            result = await transform.list_tools([tool])
            wrapped = result[0]
            output = await wrapped.run(arguments={})

            assert output.structured_content == {"result": "raw text output"}
            assert output.content[0].text == "raw text output"
