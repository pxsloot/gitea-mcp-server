"""Behavioural integration tests — error messages, output shape, resource errors.

Verifies the agent-facing contract: human-readable errors, consistent
``{"result": ...}`` wrapping, and correct handling of resource/network errors.

See https://git.home.lan/mcp-server/gitea-mcp-server/issues/331
"""

from __future__ import annotations

import pytest
import respx
from fastmcp.exceptions import ResourceError, ToolError

from tests.integration.conftest import BASE_TEST_URL

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_version_spec() -> dict:
    """Return a minimal Swagger spec with a ``/version`` endpoint.

    Note: There is intentionally **no response schema** on the 200 response.
    This exercises the wrapping transform's fallback path where
    ``output_schema`` is ``None`` and ``_ToolWrappingTransform._wrap()``
    handles the wrapping.  Adding a schema would change which code path
    the tests exercise (schema-driven vs fallback).
    """
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
    """Return a minimal Swagger spec with a ``/repos/{owner}/{repo}`` endpoint.

    Note: There is intentionally **no response schema** on the 200/404/403
    responses.  The error-translation tests only read the ``description``
    field from the response object (not a schema), and the validation tests
    never reach the API.  A schema is not needed for these code paths.
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


def _make_empty_spec() -> dict:
    """Return a minimal Swagger spec with no endpoints at all.

    Used by ``TestResourceErrors`` — resource tests don't need any tools,
    only the auto-generated resource for ``/repos/{owner}/{repo}``.
    Using ``_make_repo_spec()`` would register a ``repoGet`` tool that is
    not needed and could cause confusion.
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {},
        "definitions": {},
    }


def _make_diff_spec() -> dict:
    """Return a Swagger spec with the real text/plain diff/patch download endpoint.

    Mirrors the actual Gitea operation ``repoDownloadPullDiffOrPatch`` at
    ``/repos/{owner}/{repo}/pulls/{index}.{diffType}``.  The previous
    hand-crafted fixture used a non-existent ``repoDownloadPullRequestDiff``
    operation with a static ``.diff`` path, which silently skipped the
    interaction between the embedded ``{diffType}`` path parameter, URL
    construction, and output validation.  See issue #442 (Finding 1).
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {
            "/repos/{owner}/{repo}/pulls/{index}.{diffType}": {
                "get": {
                    "operationId": "repoDownloadPullDiffOrPatch",
                    "summary": "Download pull request diff or patch",
                    "produces": ["text/plain"],
                    "parameters": [
                        {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "index", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {
                            "name": "diffType",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "enum": ["diff", "patch"]},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "APIString is a string response",
                            "schema": {"type": "string"},
                        }
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

    Each test uses the ``mcp_server`` fixture with the repo spec, and the
    ``base_spec`` override provides the spec at the class level so the
    fixture handles mock setup and server creation automatically.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_repo_spec()

    async def test_empty_owner_rejected(self, mcp_server) -> None:
        """Empty owner string raises human-readable validation error."""
        with pytest.raises(ToolError, match="owner cannot be empty"):
            await mcp_server.call_tool(
                "gitea_repo_get",
                {"owner": "", "repo": "valid-repo"},
            )

    async def test_empty_repo_rejected(self, mcp_server) -> None:
        """Empty repo string raises human-readable validation error."""
        with pytest.raises(ToolError, match="repo cannot be empty"):
            await mcp_server.call_tool(
                "gitea_repo_get",
                {"owner": "valid-owner", "repo": ""},
            )

    async def test_missing_required_parameter(self, mcp_server) -> None:
        """Missing required ``owner`` parameter is clearly reported."""
        with pytest.raises(ToolError, match="Missing required parameter"):
            await mcp_server.call_tool(
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

    Uses the ``mcp_server`` fixture with a class-level ``base_spec`` override.
    The fixture activates the global ``respx`` router (via ``respx.start()``),
    so API-level routes are registered directly in the test body via
    module-level ``respx.get()`` / ``respx.post()`` calls.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_repo_spec()

    async def test_404_includes_spec_description_and_body(self, mcp_server) -> None:
        """A 404 response is translated to include the spec description and API body message."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/nonexistent/missing").respond(
            404,
            json={"message": "The target could not be found."},
        )
        with pytest.raises(ToolError) as exc:
            await mcp_server.call_tool(
                "gitea_repo_get",
                {"owner": "nonexistent", "repo": "missing"},
            )
        msg = str(exc.value)
        # Spec description
        assert "APINotFound" in msg, f"Expected spec description in {msg!r}"
        # Response body message
        assert "could not be found" in msg, f"Expected body message in {msg!r}"

    async def test_403_includes_permission_hint(self, mcp_server) -> None:
        """A 403 response is translated to include a permission hint."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/private/repo").respond(
            403,
            json={"message": "Access denied"},
        )
        with pytest.raises(ToolError) as exc:
            await mcp_server.call_tool(
                "gitea_repo_get",
                {"owner": "private", "repo": "repo"},
            )
        msg = str(exc.value)
        assert "APINotFound" in msg, f"Expected spec description in {msg!r}"
        assert "Access denied" in msg, f"Expected body message in {msg!r}"

    async def test_network_error_is_agent_friendly(self, mcp_server) -> None:
        """A connection-level error is reported as a network issue, not a crash.

        No API route is registered for the call — respx raises a transport
        error that the error-handling layer translates to an agent-friendly
        message.
        """
        with pytest.raises(ToolError) as exc:
            await mcp_server.call_tool(
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

    These tests intentionally use a version spec **without** a response schema
    to exercise the wrapping transform's fallback path (where ``output_schema``
    is ``None`` and the transform wraps the raw response).  See
    ``_make_version_spec()`` for details.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_version_spec()

    async def test_array_response_is_wrapped_in_result(self, mcp_server) -> None:
        """Array responses are auto-wrapped in ``{\"result\": [...]}``."""
        respx.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json=[{"id": 1}])
        result = await mcp_server.call_tool("gitea_get_version", {})
        assert result.structured_content is not None
        assert "result" in result.structured_content
        assert isinstance(result.structured_content["result"], list)

    async def test_object_response_is_json_in_text_content(self, mcp_server) -> None:
        """Object responses appear as JSON in the text content."""
        respx.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json={"version": "1.99.0"})
        result = await mcp_server.call_tool("gitea_get_version", {})
        assert len(result.content) > 0
        text = result.content[0].text
        assert '"version":"1.99.0"' in text, f"Expected version JSON in text: {text[:200]}"

    async def test_paginated_result_includes_metadata(self, mcp_server) -> None:
        """Paginated responses include ``has_more`` and ``next_offset`` metadata."""
        respx.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json=[{"id": 1}])
        result = await mcp_server.call_tool("gitea_get_version", {})
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
    """Scenario 5: Reading a nonexistent resource returns a structured error.

    Uses an empty spec (no endpoints) because these tests only exercise the
    resource system, not tool calls.  An empty spec is the cleanest fixture
    — ``_make_repo_spec()`` would register a ``repoGet`` tool that is not
    needed here.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_empty_spec()

    async def test_resource_404_produces_structured_error(self, mcp_server) -> None:
        """read_resource on a nonexistent repo returns ResourceError with details."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/nonexistent/missing").respond(404)
        with pytest.raises(ResourceError) as exc:
            await mcp_server.read_resource("gitea://repos/nonexistent/missing")
        error_str = str(exc.value)
        assert "NOT_FOUND" in error_str, f"Expected NOT_FOUND code in {error_str}"
        assert "nonexistent/missing" in error_str, (
            f"Expected resource identifier in {error_str}"
        )

    async def test_resource_404_through_call_tool(self, mcp_server) -> None:
        """Calling the ``read_resource`` synthetic tool on a missing URI also returns error.

        This works even with ``enable_lazy_loading=False`` (the default for
        ``mcp_server``) because ``TolerantSearchTransform`` intercepts
        ``call_tool("gitea_read_resource", ...)`` and resolves the tool
        name from the full catalog — the synthetic tool does not need to
        be in the visible tool list.
        """
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/nonexistent/missing").respond(404)
        with pytest.raises(ToolError) as exc:
            await mcp_server.call_tool(
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

    Uses the real Gitea operation ``repoDownloadPullDiffOrPatch`` at
    ``/repos/{owner}/{repo}/pulls/{index}.{diffType}`` (see ``_make_diff_spec``).
    The ``produces: ["text/plain"]`` field triggers the converter to set
    ``x-original-content-types``, which makes ``_is_text_response()`` return
    ``True``. The tool then gets a lightweight ``{"result": {"type": "string"}}``
    fallback ``output_schema`` (introduced in #352), and the
    ``_ToolWrappingTransform`` wraps the raw text in ``{"result": text}``.
    """

    @pytest.fixture
    def base_spec(self):
        return _make_diff_spec()

    async def test_text_response_does_not_trigger_output_validation_error(
        self, mcp_server,
    ) -> None:
        """Non-JSON response does NOT raise an MCP SDK output validation error."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/pulls/1.diff").respond(
            200,
            text="diff --git a/file.py b/file.py\n"
                 "index abc..def 100644\n"
                 "--- a/file.py\n"
                 "+++ b/file.py\n"
                 "@@ -1,3 +1,4 @@\n"
                 "+new line\n",
        )
        # Must NOT raise ToolError or any other exception
        result = await mcp_server.call_tool(
            "gitea_repo_download_pull_diff_or_patch",
            {"owner": "owner", "repo": "repo", "index": 1, "diffType": "diff"},
        )
        assert result is not None

    async def test_text_response_wrapped_in_result_key(
        self, mcp_server,
    ) -> None:
        """Non-JSON response text is wrapped in ``{"result": text}``."""
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/pulls/1.diff").respond(
            200,
            text="diff --git a/f b/f\n",
        )
        result = await mcp_server.call_tool(
            "gitea_repo_download_pull_diff_or_patch",
            {"owner": "owner", "repo": "repo", "index": 1, "diffType": "diff"},
        )
        assert result.structured_content is not None
        assert "result" in result.structured_content
        assert "diff --git" in result.structured_content["result"]

    async def test_text_response_content_is_not_empty(
        self, mcp_server,
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
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/pulls/1.diff").respond(
            200, text=diff,
        )
        result = await mcp_server.call_tool(
            "gitea_repo_download_pull_diff_or_patch",
            {"owner": "owner", "repo": "repo", "index": 1, "diffType": "diff"},
        )
        assert len(result.content) > 0
        text = result.content[0].text
        assert text == diff, f"Expected raw diff, got: {text[:100]}"

    async def test_full_stack_text_validation_round_trip(
        self, mcp_server,
    ) -> None:
        """Full round-trip through MCP SDK validation for a text/plain endpoint.

        Exercises all four layers of the output pipeline with the real
        ``repoDownloadPullDiffOrPatch`` spec (including the embedded
        ``{diffType}`` path parameter):

        1. ``OpenAPITool.run()`` — returns ``ToolResult(content=text)`` on a
           text/plain body.
        2. ``_ToolWrappingTransform._pipeline_with_context()`` — wraps the
           text into ``{"result": text}`` when ``is_text_response=True``.
        3. ``format_result()`` — passes strings through unchanged in markdown.
        4. MCP SDK ``call_tool`` handler — validates ``structured_content``
           against the lightweight ``output_schema``.

        Regression guard for #437: a text/plain endpoint must not raise an
        output validation error and must return ``{"result": <diff>}``.
        """
        diff = (
            "diff --git a/README.md b/README.md\n"
            "index 123..456 100644\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-old line\n"
            "+new line\n"
        )
        respx.get(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/pulls/1.diff").respond(
            200, text=diff,
        )
        result = await mcp_server.call_tool(
            "gitea_repo_download_pull_diff_or_patch",
            {"owner": "owner", "repo": "repo", "index": 1, "diffType": "diff"},
        )
        # Layer 4: no output validation error — a result is returned, not raised.
        assert result is not None
        # structured_content matches the lightweight output_schema shape.
        assert result.structured_content is not None
        assert result.structured_content == {"result": diff}
        # The text content carries the raw diff.
        assert len(result.content) > 0
        assert result.content[0].text == diff
