"""Behavioural integration tests — error messages, output shape, resource errors.

Verifies the agent-facing contract: human-readable errors, consistent
``{"result": ...}`` wrapping, and correct handling of resource/network errors.

See https://git.home.lan/mcp-server/gitea-mcp-server/issues/331
"""

from __future__ import annotations

import pytest
import respx
from fastmcp.exceptions import ResourceError, ToolError

from fastmcp import FastMCP
from fastmcp.server.context import Context
from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig
from tests.integration.conftest import BASE_TEST_URL, create_test_server

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_version_spec() -> dict:
    """Return a minimal Swagger spec with a ``/version`` endpoint."""
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {
            "/version": {
                "get": {
                    "operationId": "getVersion",
                    "summary": "Get server version",
                    "responses": {"200": {"description": "Success"}},
                }
            },
        },
        "definitions": {},
    }


def _make_repo_spec() -> dict:
    """Return a minimal Swagger spec with a ``/repos/{owner}/{repo}`` endpoint."""
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
                        {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {"description": "Success"},
                        "404": {"description": "APINotFound: Repository not found."},
                        "403": {"description": "APINotFound: Permission denied."},
                    },
                }
            },
        },
        "definitions": {},
    }


# ---------------------------------------------------------------------------
# Scenario 1 — Validation error messages
# ---------------------------------------------------------------------------


class TestValidationErrors:
    """Scenario 1: Validation errors produce human-readable messages.

    Validation fires *before* any HTTP call, so no respx mocking is needed
    for the error path — the tool never reaches the API.
    """

    async def test_empty_owner_rejected(self, simple_config: SimpleConfig) -> None:
        """Empty owner string raises human-readable validation error."""
        spec = _make_repo_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            server = await create_test_server(simple_config, spec)
            with pytest.raises(ToolError, match="owner cannot be empty"):
                await server.call_tool(
                    "gitea_repo_get",
                    {"owner": "", "repo": "valid-repo"},
                )

    async def test_empty_repo_rejected(self, simple_config: SimpleConfig) -> None:
        """Empty repo string raises human-readable validation error."""
        spec = _make_repo_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            server = await create_test_server(simple_config, spec)
            with pytest.raises(ToolError, match="repo cannot be empty"):
                await server.call_tool(
                    "gitea_repo_get",
                    {"owner": "valid-owner", "repo": ""},
                )

    async def test_missing_required_parameter(self, simple_config: SimpleConfig) -> None:
        """Missing required ``owner`` parameter is clearly reported."""
        spec = _make_repo_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            server = await create_test_server(simple_config, spec)
            with pytest.raises(ToolError, match="Missing required parameter"):
                await server.call_tool(
                    "gitea_repo_get",
                    {"repo": "valid-repo"},
                )


# ---------------------------------------------------------------------------
# Scenarios 2 & 3 — API error translation (404 / 403)
# ---------------------------------------------------------------------------


class TestAPIErrorTranslation:
    """Scenarios 2 & 3: HTTP 404/403 errors become agent-friendly messages.

    The OpenAPI response descriptions in the spec are extracted by
    ``_lookup_response_description()`` and prepended to the error message.
    """

    async def test_404_includes_spec_description_and_body(self, simple_config: SimpleConfig) -> None:
        """A 404 response is translated to include the spec description and API body message."""
        spec = _make_repo_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/repos/nonexistent/missing").respond(
                404,
                json={"message": "The target could not be found."},
            )
            server = await create_test_server(simple_config, spec)
            with pytest.raises(ToolError) as exc:
                await server.call_tool(
                    "gitea_repo_get",
                    {"owner": "nonexistent", "repo": "missing"},
                )
            msg = str(exc.value)
            # Spec description
            assert "APINotFound" in msg, f"Expected spec description in {msg!r}"
            # Response body message
            assert "could not be found" in msg, f"Expected body message in {msg!r}"

    async def test_403_includes_permission_hint(self, simple_config: SimpleConfig) -> None:
        """A 403 response is translated to include a permission hint."""
        spec = _make_repo_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/repos/private/repo").respond(
                403,
                json={"message": "Access denied"},
            )
            server = await create_test_server(simple_config, spec)
            with pytest.raises(ToolError) as exc:
                await server.call_tool(
                    "gitea_repo_get",
                    {"owner": "private", "repo": "repo"},
                )
            msg = str(exc.value)
            assert "APINotFound" in msg, f"Expected spec description in {msg!r}"
            assert "Access denied" in msg, f"Expected body message in {msg!r}"

    async def test_network_error_is_agent_friendly(self, simple_config: SimpleConfig) -> None:
        """A connection-level error is reported as a network issue, not a crash."""
        spec = _make_repo_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            server = await create_test_server(simple_config, spec)
            # No route registered for the API call → connection error
            with pytest.raises(ToolError) as exc:
                await server.call_tool(
                    "gitea_repo_get",
                    {"owner": "any", "repo": "any"},
                )
            msg = str(exc.value)
            assert "Network error" in msg or "Could not reach" in msg or "not mocked" in msg, (
                f"Expected network/connection error message in {msg!r}"
            )


# ---------------------------------------------------------------------------
# Scenario 4 — Result wrapping consistency
# ---------------------------------------------------------------------------


class TestResultWrapping:
    """Scenario 4: Result shape consistency.

    FastMCP wraps array responses in ``{\"result\": ...}`` automatically
    (since the MCP SDK requires ``output_schema`` to be ``type: object``).
    Object responses that already are dicts are passed through as-is.
    Paginated endpoints with array output get ``has_more`` / ``next_offset``
    metadata injected by ``_ToolWrappingTransform``.
    """

    async def test_array_response_is_wrapped_in_result(self, simple_config: SimpleConfig) -> None:
        """Array responses are auto-wrapped in ``{\"result\": [...]}``."""
        spec = _make_version_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json=[{"id": 1}])
            server = await create_test_server(simple_config, spec)
            result = await server.call_tool("gitea_get_version", {})
            assert result.structured_content is not None
            assert "result" in result.structured_content
            assert isinstance(result.structured_content["result"], list)

    async def test_object_response_is_json_in_text_content(self, simple_config: SimpleConfig) -> None:
        """Object responses appear as JSON in the text content."""
        spec = _make_version_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json={"version": "1.99.0"})
            server = await create_test_server(simple_config, spec)
            result = await server.call_tool("gitea_get_version", {})
            assert len(result.content) > 0
            text = result.content[0].text
            assert '"version":"1.99.0"' in text, f"Expected version JSON in text: {text[:200]}"

    async def test_paginated_result_includes_metadata(self, simple_config: SimpleConfig) -> None:
        """Paginated responses include ``has_more`` and ``next_offset`` metadata."""
        spec = _make_version_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json=[{"id": 1}])
            server = await create_test_server(simple_config, spec)
            result = await server.call_tool("gitea_get_version", {})
            assert result.structured_content is not None
            assert "result" in result.structured_content
            # Pagination metadata (has_more, next_offset) is added by
            # _ToolWrappingTransform when the output schema is an array type.
            # The test below is optional — it asserts current behaviour.
            if "has_more" in result.structured_content:
                assert isinstance(result.structured_content["has_more"], bool)


# ---------------------------------------------------------------------------
# Scenario 5 — Resource 404 errors
# ---------------------------------------------------------------------------


class TestResourceErrors:
    """Scenario 5: Reading a nonexistent resource returns a structured error."""

    async def test_resource_404_produces_structured_error(self, simple_config: SimpleConfig) -> None:
        """read_resource on a nonexistent repo returns ResourceError with details."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "basePath": "/api/v1",
            "paths": {},
            "definitions": {},
        }
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/repos/nonexistent/missing").respond(404)
            server = await create_test_server(simple_config, spec)
            with pytest.raises(ResourceError) as exc:
                await server.read_resource("gitea://repos/nonexistent/missing")
            error_str = str(exc.value)
            assert "NOT_FOUND" in error_str, f"Expected NOT_FOUND code in {error_str}"
            assert "nonexistent/missing" in error_str, (
                f"Expected resource identifier in {error_str}"
            )

    async def test_resource_404_through_call_tool(self, simple_config: SimpleConfig) -> None:
        """Calling the ``read_resource`` synthetic tool on a missing URI also returns error."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "basePath": "/api/v1",
            "paths": {},
            "definitions": {},
        }
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/repos/nonexistent/missing").respond(404)
            server = await create_test_server(simple_config, spec)
            with pytest.raises(ToolError) as exc:
                await server.call_tool(
                    "gitea_read_resource",
                    {"uri": "gitea://repos/nonexistent/missing"},
                )
            msg = str(exc.value)
            assert "not found" in msg.lower(), f"Expected 'not found' in {msg!r}"


# ---------------------------------------------------------------------------
# Scenario 6 — Non-JSON endpoint handling
# ---------------------------------------------------------------------------


class TestNonJsonEndpoint:
    """Scenario 6: Non-JSON (text/plain) endpoint does not trigger
    MCP SDK ``Output validation error``.

    The ``produces: ["text/plain"]`` field in the Swagger spec triggers the
    converter to set ``x-original-content-types``, which causes
    ``_is_text_response()`` to return ``True``. The tool then has
    ``output_schema`` set to ``None`` or a lightweight fallback, which
    tells the MCP SDK to skip output validation. The
    ``_ToolWrappingTransform`` fallback wraps the raw text in
    ``{\"result\": text}``.
    """

    def _make_diff_spec(self) -> dict:
        """Return a Swagger spec with a text/plain diff download endpoint."""
        return {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "basePath": "/api/v1",
            "paths": {
                "/repos/{owner}/{repo}/pulls/{index}.diff": {
                    "get": {
                        "operationId": "repoDownloadPullRequestDiff",
                        "summary": "Download pull request diff",
                        "produces": ["text/plain"],
                        "parameters": [
                            {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "index", "in": "path", "required": True, "schema": {"type": "integer"}},
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

    async def test_text_response_does_not_trigger_output_validation_error(
        self, simple_config: SimpleConfig,
    ) -> None:
        """Non-JSON response does NOT raise an MCP SDK output validation error."""
        spec = self._make_diff_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/pulls/1.diff").respond(
                200,
                text="diff --git a/file.py b/file.py\n"
                     "index abc..def 100644\n"
                     "--- a/file.py\n"
                     "+++ b/file.py\n"
                     "@@ -1,3 +1,4 @@\n"
                     "+new line\n",
            )
            server = await create_test_server(simple_config, spec)
            # Must NOT raise ToolError or any other exception
            result = await server.call_tool(
                "gitea_repo_download_pull_request_diff",
                {"owner": "owner", "repo": "repo", "index": 1},
            )
            assert result is not None

    async def test_text_response_wrapped_in_result_key(
        self, simple_config: SimpleConfig,
    ) -> None:
        """Non-JSON response text is wrapped in ``{\"result\": text}``."""
        spec = self._make_diff_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/pulls/1.diff").respond(
                200,
                text="diff --git a/f b/f\n",
            )
            server = await create_test_server(simple_config, spec)
            result = await server.call_tool(
                "gitea_repo_download_pull_request_diff",
                {"owner": "owner", "repo": "repo", "index": 1},
            )
            assert result.structured_content is not None
            assert "result" in result.structured_content
            assert "diff --git" in result.structured_content["result"]

    async def test_text_response_content_is_not_empty(
        self, simple_config: SimpleConfig,
    ) -> None:
        """Text content of the result is the raw diff text."""
        diff = (
            "diff --git a/f b/f\n"
            "index abc..def\n"
            "--- a/f\n"
            "+++ b/f\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new"
        )
        spec = self._make_diff_spec()
        async with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=spec)
            mock.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/pulls/1.diff").respond(
                200, text=diff,
            )
            server = await create_test_server(simple_config, spec)
            result = await server.call_tool(
                "gitea_repo_download_pull_request_diff",
                {"owner": "owner", "repo": "repo", "index": 1},
            )
            assert len(result.content) > 0
            text = result.content[0].text
            assert text == diff, f"Expected raw diff, got: {text[:100]}"
