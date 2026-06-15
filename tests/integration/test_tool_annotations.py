"""Integration tests for annotation and hint correctness through the full transform chain.

Verifies that annotations (readOnlyHint, destructiveHint, idempotentHint,
openWorldHint) and category tags are correctly propagated from
``_customize_metadata()`` through all transforms (namespace, extension metadata,
exclusion) to the final tool metadata that agents see.

See https://git.home.lan/mcp-server/gitea-mcp-server/issues/332
"""

from __future__ import annotations

from pathlib import Path

import pytest
import respx
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool, ToolAnnotations

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig
from tests.integration.conftest import BASE_TEST_URL


# ---------------------------------------------------------------------------
# Shared spec with endpoints across all HTTP methods + all 7 categories
# ---------------------------------------------------------------------------

# Expected tool names after GiteaNamespace prefix (camelCase→snake_case):
#
#   repoGet              → gitea_repo_get
#   repoUpdate           → gitea_repo_update
#   repoDelete           → gitea_repo_delete
#   issueCreateIssue     → gitea_issue_create_issue
#   adminGetCron         → gitea_admin_get_cron
#   orgGet               → gitea_org_get
#   userGetCurrent       → gitea_user_get_current
#   repoListPullRequests → gitea_repo_list_pull_requests
#   getVersion           → gitea_get_version


def _make_annotation_spec() -> dict:
    """Comprehensive spec covering all HTTP methods and all 7 category paths.

    There is intentionally **no response schema** on these endpoints —
    the tests only inspect tool *metadata* (annotations), never execute a tool
    call, so schemas are not needed.
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {
            # repository category — GET (read-only, idempotent)
            "/repos/{owner}/{repo}": {
                "get": {
                    "operationId": "repoGet",
                    "summary": "Get a repository",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Success"}},
                },
                # repository category — PUT (not read-only, idempotent)
                "put": {
                    "operationId": "repoUpdate",
                    "summary": "Update a repository",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Success"}},
                },
                # repository category — DELETE (destructive, idempotent)
                "delete": {
                    "operationId": "repoDelete",
                    "summary": "Delete a repository",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"204": {"description": "No Content"}},
                },
            },
            # issue category — POST (not read-only, not idempotent, not destructive)
            "/repos/{owner}/{repo}/issues": {
                "post": {
                    "operationId": "issueCreateIssue",
                    "summary": "Create an issue",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"201": {"description": "Created"}},
                },
            },
            # admin category
            "/admin/cron": {
                "get": {
                    "operationId": "adminGetCron",
                    "summary": "List cron tasks",
                    "responses": {"200": {"description": "Success"}},
                },
            },
            # organization category
            "/orgs/{org}": {
                "get": {
                    "operationId": "orgGet",
                    "summary": "Get an organization",
                    "parameters": [
                        {"name": "org", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Success"}},
                },
            },
            # user category
            "/user": {
                "get": {
                    "operationId": "userGetCurrent",
                    "summary": "Get current user",
                    "responses": {"200": {"description": "Success"}},
                },
            },
            # pull_request category
            "/repos/{owner}/{repo}/pulls": {
                "get": {
                    "operationId": "repoListPullRequests",
                    "summary": "List pull requests",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Success"}},
                },
            },
            # misc category (no matching category prefix)
            "/version": {
                "get": {
                    "operationId": "getVersion",
                    "summary": "Get server version",
                    "responses": {"200": {"description": "Success"}},
                },
            },
        },
        "definitions": {},
    }


# ---------------------------------------------------------------------------
# Helper: build a tool-name → Tool dict from server.list_tools()
# ---------------------------------------------------------------------------


def _tool_map(tools: list[Tool]) -> dict[str, Tool]:
    return {t.name: t for t in tools}


# ===================================================================
# Scenario 1 — readOnlyHint
# ===================================================================


class TestReadOnlyHint:
    """``readOnlyHint`` must be True for GET, False for write methods."""

    @pytest.fixture
    def base_spec(self):
        return _make_annotation_spec()

    async def test_get_is_read_only(self, mcp_server) -> None:
        """GET endpoint → readOnlyHint=True."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_get"]
        assert t.annotations is not None
        assert t.annotations.readOnlyHint is True

    async def test_post_is_not_read_only(self, mcp_server) -> None:
        """POST endpoint → readOnlyHint=False."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_issue_create_issue"]
        assert t.annotations is not None
        assert t.annotations.readOnlyHint is False

    async def test_put_is_not_read_only(self, mcp_server) -> None:
        """PUT endpoint → readOnlyHint=False."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_update"]
        assert t.annotations is not None
        assert t.annotations.readOnlyHint is False

    async def test_delete_is_not_read_only(self, mcp_server) -> None:
        """DELETE endpoint → readOnlyHint=False."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_delete"]
        assert t.annotations is not None
        assert t.annotations.readOnlyHint is False


# ===================================================================
# Scenario 2 & 3 — destructiveHint
# ===================================================================


class TestDestructiveHint:
    """``destructiveHint`` must be True for DELETE, False for other methods."""

    @pytest.fixture
    def base_spec(self):
        return _make_annotation_spec()

    async def test_delete_is_destructive(self, mcp_server) -> None:
        """DELETE endpoint → destructiveHint=True."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_delete"]
        assert t.annotations is not None
        assert t.annotations.destructiveHint is True

    async def test_post_is_not_destructive(self, mcp_server) -> None:
        """POST endpoint → destructiveHint=False."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_issue_create_issue"]
        assert t.annotations is not None
        assert t.annotations.destructiveHint is False

    async def test_get_is_not_destructive(self, mcp_server) -> None:
        """GET endpoint → destructiveHint=False."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_get"]
        assert t.annotations is not None
        assert t.annotations.destructiveHint is False


# ===================================================================
# Scenario 4 & 5 — idempotentHint
# ===================================================================


class TestIdempotentHint:
    """``idempotentHint`` must be True for GET/PUT/DELETE, False for POST."""

    @pytest.fixture
    def base_spec(self):
        return _make_annotation_spec()

    async def test_get_is_idempotent(self, mcp_server) -> None:
        """GET endpoint → idempotentHint=True."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_get"]
        assert t.annotations is not None
        assert t.annotations.idempotentHint is True

    async def test_put_is_idempotent(self, mcp_server) -> None:
        """PUT endpoint → idempotentHint=True."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_update"]
        assert t.annotations is not None
        assert t.annotations.idempotentHint is True

    async def test_delete_is_idempotent(self, mcp_server) -> None:
        """DELETE endpoint → idempotentHint=True."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_repo_delete"]
        assert t.annotations is not None
        assert t.annotations.idempotentHint is True

    async def test_post_is_not_idempotent(self, mcp_server) -> None:
        """POST endpoint → idempotentHint=False."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools["gitea_issue_create_issue"]
        assert t.annotations is not None
        assert t.annotations.idempotentHint is False


# ===================================================================
# Scenario 6 — openWorldHint on API tools
# ===================================================================


class TestOpenWorldHintAPITools:
    """All spec-derived (API) tools must have ``openWorldHint=True``."""

    # All API tools from the annotation spec
    _API_TOOLS = [
        "gitea_repo_get",
        "gitea_repo_update",
        "gitea_repo_delete",
        "gitea_issue_create_issue",
        "gitea_admin_get_cron",
        "gitea_org_get",
        "gitea_user_get_current",
        "gitea_repo_list_pull_requests",
        "gitea_get_version",
    ]

    @pytest.fixture
    def base_spec(self):
        return _make_annotation_spec()

    @pytest.mark.parametrize("tool_name", _API_TOOLS)
    async def test_api_tool_has_open_world_hint(self, mcp_server, tool_name: str) -> None:
        """API tool ``{tool_name}`` → openWorldHint=True."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools[tool_name]
        assert t.annotations is not None
        assert t.annotations.openWorldHint is True, (
            f"Expected openWorldHint=True for {tool_name}, got "
            f"readOnly={t.annotations.readOnlyHint}, "
            f"destructive={t.annotations.destructiveHint}, "
            f"idempotent={t.annotations.idempotentHint}, "
            f"openWorld={t.annotations.openWorldHint}"
        )


# ===================================================================
# Scenario 7 — openWorldHint on synthetic tools
# ===================================================================


class TestOpenWorldHintSyntheticTools:
    """Synthetic tools must have the correct ``openWorldHint`` values.

    Tools that call the Gitea API (call_tool, read_resource) have
    openWorldHint=True.  Tools that operate on in-memory data
    (search_tools, tool_info) have openWorldHint=False.

    These tests use ``search_mcp_server`` (lazy loading enabled) because
    ``register_synthetic_tools()`` (search_tools, call_tool, tool_info)
    only runs when ``enable_lazy_loading=True``.  ``read_resource`` and
    ``list_resources`` are registered unconditionally via
    ``register_mcp_resource_tools()``.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_annotation_spec()

    async def test_search_tools_is_local(self, search_mcp_server) -> None:
        """``search_tools`` operates on in-memory data → openWorldHint=False."""
        tools = _tool_map(await search_mcp_server.list_tools())
        t = tools.get("gitea_search_tools")
        assert t is not None, "gitea_search_tools not found in tool listing"
        assert t.annotations is not None
        assert t.annotations.openWorldHint is False

    async def test_tool_info_is_local(self, search_mcp_server) -> None:
        """``tool_info`` queries in-memory catalog → openWorldHint=False."""
        tools = _tool_map(await search_mcp_server.list_tools())
        t = tools.get("gitea_tool_info")
        assert t is not None, "gitea_tool_info not found in tool listing"
        assert t.annotations is not None
        assert t.annotations.openWorldHint is False

    async def test_call_tool_is_external(self, search_mcp_server) -> None:
        """``call_tool`` delegates to Gitea API → openWorldHint=True."""
        tools = _tool_map(await search_mcp_server.list_tools())
        t = tools.get("gitea_call_tool")
        assert t is not None, "gitea_call_tool not found in tool listing"
        assert t.annotations is not None
        assert t.annotations.openWorldHint is True

    async def test_read_resource_is_external(self, search_mcp_server) -> None:
        """``read_resource`` fetches from Gitea API → openWorldHint=True."""
        tools = _tool_map(await search_mcp_server.list_tools())
        t = tools.get("gitea_read_resource")
        assert t is not None, "gitea_read_resource not found in tool listing"
        assert t.annotations is not None
        assert t.annotations.openWorldHint is True


# ===================================================================
# Scenario 8 — Category tags
# ===================================================================


class TestCategoryTags:
    """Category tag must match the path prefix for each endpoint."""

    # (tool_name, expected_category)
    _CATEGORY_CASES: list[tuple[str, str]] = [
        ("gitea_admin_get_cron", "admin"),
        ("gitea_org_get", "organization"),
        ("gitea_user_get_current", "user"),
        ("gitea_issue_create_issue", "issue"),
        ("gitea_repo_list_pull_requests", "pull_request"),
        ("gitea_repo_get", "repository"),
        ("gitea_get_version", "misc"),
    ]

    @pytest.fixture
    def base_spec(self):
        return _make_annotation_spec()

    @pytest.mark.parametrize("tool_name,expected_category", _CATEGORY_CASES)
    async def test_category_tag(
        self, mcp_server, tool_name: str, expected_category: str,
    ) -> None:
        """``{tool_name}`` has category tag ``{expected_category}``."""
        tools = _tool_map(await mcp_server.list_tools())
        t = tools[tool_name]
        tags = t.tags or set()
        assert expected_category in tags, (
            f"Expected tag '{expected_category}' in {tags} for {tool_name}"
        )


# ===================================================================
# Scenario 9 — Hints persist through exclusion config
# ===================================================================


class TestAnnotationsSurviveExclusion:
    """Annotations remain correct even when some tools are excluded.

    Exclusion only affects tool *visibility* (list_tools), not the annotation
    data on non-excluded tools.
    """

    async def test_non_excluded_tools_have_correct_annotations(self, tmp_path: Path) -> None:
        """Excluding some tools does not corrupt annotations on other tools."""
        cfg = tmp_path / "exclude.yaml"
        cfg.write_text("exclude:\n  - gitea_repo_delete")
        config = SimpleConfig(
            url=BASE_TEST_URL,
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
            enable_lazy_loading=False,
            exclude_config_path=str(cfg),
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=_make_annotation_spec())
            mcp = await create_mcp_server(gitea_client)

            tools = _tool_map(await mcp.list_tools())

            # Excluded tool is hidden
            assert "gitea_repo_delete" not in tools, (
                "gitea_repo_delete should be excluded from list_tools"
            )

            # Non-excluded tools have correct annotations
            repo_get = tools["gitea_repo_get"]
            assert repo_get.annotations is not None
            assert repo_get.annotations.readOnlyHint is True
            assert repo_get.annotations.destructiveHint is False
            assert repo_get.annotations.idempotentHint is True
            assert repo_get.annotations.openWorldHint is True
            assert repo_get.annotations.title is not None
            assert "repository" in (repo_get.tags or set())

            issue_create = tools["gitea_issue_create_issue"]
            assert issue_create.annotations is not None
            assert issue_create.annotations.readOnlyHint is False
            assert issue_create.annotations.destructiveHint is False
            assert issue_create.annotations.idempotentHint is False
            assert issue_create.annotations.openWorldHint is True
            assert "issue" in (issue_create.tags or set())



# ===================================================================
# Scenario 9b — tool_info returns correct annotations
# ===================================================================


class TestToolInfoAnnotations:
    """``tool_info`` tool returns full annotation metadata for any tool.

    Uses ``search_mcp_server`` (lazy loading enabled) because the synthetic
    tools (search_tools, call_tool, tool_info) are only registered when
    ``enable_lazy_loading=True``.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_annotation_spec()

    async def test_synthetic_tool_annotations_via_tool_info(self, search_mcp_server) -> None:
        """tool_info returns correct annotations for a synthetic tool."""
        # The GiteaNamespace prefixes all tool names, so the namespaced
        # tool name is ``gitea_tool_info``, not ``tool_info``.
        result = await search_mcp_server.call_tool(
            "gitea_tool_info",
            {"name": "gitea_search_tools", "format": "json"},
        )
        assert result.structured_content is not None
        data = result.structured_content["result"]
        assert isinstance(data, dict)
        annotations = data.get("annotations", {})
        assert annotations.get("openWorldHint") is False, (
            f"Expected openWorldHint=False via tool_info, got {annotations}"
        )

    async def test_api_tool_annotations_via_tool_info(self, search_mcp_server) -> None:
        """tool_info returns correct annotations for an API tool."""
        result = await search_mcp_server.call_tool(
            "gitea_tool_info",
            {"name": "gitea_repo_get", "format": "json"},
        )
        assert result.structured_content is not None
        data = result.structured_content["result"]
        assert isinstance(data, dict)
        annotations = data.get("annotations", {})
        assert annotations.get("readOnlyHint") is True
        assert annotations.get("destructiveHint") is False
        assert annotations.get("idempotentHint") is True
        assert annotations.get("openWorldHint") is True
        assert annotations.get("title") is not None
