"""Edge-case integration tests - labels, pagination, cache, deprecated.

Covers scenarios 1-4 from
https://git.home.lan/mcp-server/gitea-mcp-server/issues/333

Scenarios 5 (permission filtering) and 6 (non-JSON handling) are covered in
``test_server.py::TestToolFiltering`` and
``test_tool_behaviour.py::TestNonJsonEndpoint`` respectively.
"""

from __future__ import annotations

import json

import pytest
import respx
from fastmcp.exceptions import ToolError

from tests.integration.conftest import BASE_TEST_URL


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_label_spec() -> dict:
    """Return a Swagger spec with a ``POST /repos/{owner}/{repo}/issues`` endpoint
    that accepts a ``labels`` array in the body.
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {
            "/repos/{owner}/{repo}/issues": {
                "post": {
                    "operationId": "createIssue",
                    "summary": "Create an issue",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "type": "string"},
                        {"name": "repo", "in": "path", "required": True, "type": "string"},
                        {
                            "name": "body",
                            "in": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "labels": {"type": "array", "items": {"type": "integer"}},
                                },
                            },
                        },
                    ],
                    "responses": {"201": {"description": "Created"}},
                }
            },
        },
        "definitions": {},
    }


def _make_pagination_spec() -> dict:
    """Return a Swagger spec with a ``GET /items`` endpoint returning an array
    response - exercises the pagination metadata injection path.
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "summary": "List items",
                    "parameters": [
                        {"name": "page", "in": "query", "required": False, "type": "integer"},
                        {"name": "limit", "in": "query", "required": False, "type": "integer"},
                    ],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "schema": {
                                "type": "array",
                                "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                            },
                        }
                    },
                }
            },
        },
        "definitions": {},
    }


def _make_cache_spec() -> dict:
    """Return a Swagger spec with GET + PUT on ``/repos/{owner}/{repo}``.

    The GET has a schema so ``output_schema`` is set; the PUT is a write
    operation that triggers cache invalidation.
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {
            "/repos/{owner}/{repo}": {
                "get": {
                    "operationId": "repoGet",
                    "summary": "Get a repository",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "type": "string"},
                        {"name": "repo", "in": "path", "required": True, "type": "string"},
                    ],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "owner": {"type": "string"},
                                },
                            },
                        }
                    },
                },
                "put": {
                    "operationId": "repoUpdate",
                    "summary": "Update a repository",
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "type": "string"},
                        {"name": "repo", "in": "path", "required": True, "type": "string"},
                        {
                            "name": "body",
                            "in": "body",
                            "required": True,
                            "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                        },
                    ],
                    "responses": {"200": {"description": "Success"}},
                },
            },
        },
        "definitions": {},
    }


def _make_deprecated_spec() -> dict:
    """Return a Swagger spec with one normal and one deprecated endpoint."""
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {
            "/repos/{owner}/{repo}/issues": {
                "get": {
                    "operationId": "listIssues",
                    "summary": "List issues",
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/repos/{owner}/{repo}/old-endpoint": {
                "get": {
                    "operationId": "oldEndpoint",
                    "summary": "Old endpoint",
                    "deprecated": True,
                    "responses": {"200": {"description": "Success"}},
                }
            },
        },
        "definitions": {},
    }


# ---------------------------------------------------------------------------
# Scenario 1 - Label conversion end-to-end
# ---------------------------------------------------------------------------


class TestLabelConversion:
    """Scenario 1: ``call_tool`` with string ``labels`` is converted to
    integer IDs before reaching the Gitea API.

    The label conversion fetches the label map from the mocked labels API,
    then replaces string names with integer IDs in the tool kwargs.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_label_spec()

    async def test_converts_string_labels_to_ids(self, mcp_server) -> None:
        """String label names are converted to integer IDs in the API request."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/labels").respond(
            200,
            json=[
                {"id": 1, "name": "type/bug"},
                {"id": 2, "name": "type/feature"},
            ],
        )
        api_route = respx.post(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/issues").respond(
            201, json={"id": 1, "title": "Fixed bug"},
        )

        await mcp_server.call_tool(
            "gitea_create_issue",
            {"owner": "owner", "repo": "repo", "title": "Fixed bug", "labels": ["type/bug"]},
        )

        assert api_route.called, "Expected the API route to be called"
        sent = json.loads(api_route.calls[0].request.content)
        assert sent["labels"] == [1], f"Expected labels=[1], got {sent['labels']}"

    async def test_preserves_valid_integers(self, mcp_server) -> None:
        """Valid integer IDs that exist in the label map pass through unchanged.

        Unlike the old ``test_preserves_integer_labels`` (which returned an
        empty label list and relied on the old short-circuit that skipped
        validation for integers), this test verifies that integer IDs are
        **validated** against the remote label map and accepted when found.
        """
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/labels").respond(
            200,
            json=[
                {"id": 42, "name": "priority/high"},
                {"id": 99, "name": "priority/low"},
            ],
        )
        api_route = respx.post(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/issues").respond(
            201, json={"id": 1, "title": "Test"},
        )

        await mcp_server.call_tool(
            "gitea_create_issue",
            {"owner": "owner", "repo": "repo", "title": "Test", "labels": [42, 99]},
        )

        assert api_route.called
        sent = json.loads(api_route.calls[0].request.content)
        assert sent["labels"] == [42, 99]

    async def test_unknown_integer_raises_validation_error(self, mcp_server) -> None:
        """Unknown integer IDs produce a human-readable ValidationError."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/labels").respond(
            200, json=[{"id": 1, "name": "type/bug"}],
        )

        with pytest.raises(ToolError, match="Unknown label ID"):
            await mcp_server.call_tool(
                "gitea_create_issue",
                {"owner": "owner", "repo": "repo", "title": "Test", "labels": [99999]},
            )

    async def test_unknown_label_raises_validation_error(self, mcp_server) -> None:
        """Unknown label names produce a human-readable ValidationError."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/labels").respond(
            200, json=[{"id": 1, "name": "type/bug"}],
        )

        with pytest.raises(ToolError, match="Unknown label"):
            await mcp_server.call_tool(
                "gitea_create_issue",
                {"owner": "owner", "repo": "repo", "title": "Test", "labels": ["type/nonexistent"]},
            )


# ---------------------------------------------------------------------------
# Scenario 2 - Pagination metadata
# ---------------------------------------------------------------------------


class TestPaginationMetadata:
    """Scenario 2: A tool returning a list includes ``has_more``,
    ``next_offset``, and ``total_count`` in the structured result.

    Pagination metadata is injected by ``_ToolWrappingTransform`` when the
    ``output_schema`` is an array type and the result contains a list.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_pagination_spec()

    async def test_paginated_result_has_metadata(self, mcp_server) -> None:
        """Array response includes ``has_more``, ``next_offset``, ``total_count``."""
        respx.get(f"{BASE_TEST_URL}/api/v1/items?page=1&limit=2").respond(
            200, json=[{"id": 1}, {"id": 2}],
        )
        result = await mcp_server.call_tool(
            "gitea_list_items",
            {"page": 1, "limit": 2},
        )
        assert result.structured_content is not None, "Expected structured content"
        assert "has_more" in result.structured_content
        assert "next_offset" in result.structured_content
        assert "total_count" in result.structured_content
        # With 2 items and limit=2, result fills the page so has_more is True
        assert result.structured_content["has_more"] is True

    async def test_paginated_result_no_more_when_partial_page(self, mcp_server) -> None:
        """When result length is less than limit, ``has_more`` is False."""
        respx.get(f"{BASE_TEST_URL}/api/v1/items").respond(
            200, json=[{"id": 1}],
        )
        result = await mcp_server.call_tool(
            "gitea_list_items",
            {"page": 2, "limit": 10},
        )
        assert result.structured_content is not None
        # With 1 item and limit=10, page is not full → has_more should be False
        assert result.structured_content["has_more"] is False


# ---------------------------------------------------------------------------
# Scenario 3 - Cache invalidation end-to-end
# ---------------------------------------------------------------------------


class TestCacheInvalidationEndToEnd:
    """Scenario 3: A write tool call invalidates cached resources so that a
    subsequent resource read fetches fresh data.

    Requires both a GET (resource) and PUT (write tool) on the same path.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_cache_spec()

    async def test_write_invalidates_resource_cache(self, mcp_server) -> None:
        """After calling a write tool, the resource cache is cleared."""
        import httpx

        get_route = respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo")
        get_route.side_effect = [
            httpx.Response(200, json={"name": "repo-v1", "owner": "owner"}),
            httpx.Response(200, json={"name": "repo-v2", "owner": "owner"}),
        ]
        respx.put(f"{BASE_TEST_URL}/api/v1/repos/owner/repo").respond(
            200, json={"name": "updated", "owner": "owner"},
        )

        # First read - should hit API (cache miss) → v1
        result1 = await mcp_server.read_resource("gitea://repos/owner/repo")
        assert result1 is not None
        assert "repo-v1" in str(result1), f"Expected v1, got {result1!r}"

        # Second read - should come from cache → still v1 (no API call)
        result2 = await mcp_server.read_resource("gitea://repos/owner/repo")
        assert result2 is not None
        assert "repo-v1" in str(result2), f"Expected cached v1, got {result2!r}"

        # Write tool - should invalidate the repo cache
        await mcp_server.call_tool(
            "gitea_repo_update",
            {"owner": "owner", "repo": "repo", "name": "updated"},
        )

        # Third read - cache invalidated, should hit API again → v2
        result3 = await mcp_server.read_resource("gitea://repos/owner/repo")
        assert result3 is not None
        assert "repo-v2" in str(result3), f"Expected v2 after invalidation, got {result3!r}"


# ---------------------------------------------------------------------------
# Scenario 4 - Deprecated endpoint exclusion
# ---------------------------------------------------------------------------


class TestDeprecatedExclusion:
    """Scenario 4: Endpoints marked ``deprecated: true`` are not registered
    as tools by the server.

    The exclusion happens via ``route_map_fn`` in ``create_openapi_provider``.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_deprecated_spec()

    async def test_deprecated_endpoint_is_excluded(self, mcp_server) -> None:
        """A deprecated endpoint does not appear in the tool listing."""
        tools = await mcp_server.list_tools()
        tool_names = {t.name for t in tools}

        assert "gitea_list_issues" in tool_names, "Expected non-deprecated tool"
        assert "gitea_old_endpoint" not in tool_names, (
            f"Deprecated endpoint should be excluded, found in: {tool_names}"
        )
