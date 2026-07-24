"""Tests for the resource factory (``make_api_resource``)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.resources import ResourceResult

from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND
from gitea_mcp_server.resources.custom import _decode_base64_content
from gitea_mcp_server.resources.factory import (
    _auto_derive_schema,
    _registered_uris,
    make_api_resource,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_openapi_spec(paths: dict | None = None) -> dict:
    """Create a minimal OpenAPI 3.1 spec for testing."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": paths or {
            "/repos/{owner}/{repo}": {
                "get": {
                    "operationId": "getRepo",
                    "summary": "Get a repository",
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "/user": {
                "get": {
                    "operationId": "getCurrentUser",
                    "summary": "Get current user",
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "login": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "/repos/{owner}/{repo}/labels": {
                "get": {
                    "operationId": "listLabels",
                    "summary": "List labels",
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def _make_mock_mcp() -> MagicMock:
    """Create a mock FastMCP that tracks resource registrations."""
    mcp = MagicMock(spec=FastMCP)
    mcp.resource = MagicMock(return_value=lambda func: func)
    return mcp


def _make_mock_client(json_response: object = None) -> AsyncMock:
    """Create a mock GiteaClient with a canned JSON response."""
    client = AsyncMock()
    client.request = AsyncMock(return_value=json_response or {"result": "ok"})
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registered_uris() -> None:
    """Clear ``_registered_uris`` before each test to ensure test isolation.

    ``make_api_resource`` populates this module-level set at registration
    time; without resetting, test ordering matters and parallel execution
    would produce false failures.
    """
    _registered_uris.clear()


# ---------------------------------------------------------------------------
# Tests: _auto_derive_schema
# ---------------------------------------------------------------------------


class TestAutoDeriveSchema:
    """Tests for _auto_derive_schema."""

    def test_returns_schema_for_known_endpoint(self):
        spec = _make_mock_openapi_spec()
        schema = _auto_derive_schema(spec, "/repos/{owner}/{repo}", "get")
        assert schema is not None
        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "name" in schema["properties"]

    def test_returns_none_for_none_spec(self):
        assert _auto_derive_schema(None, "/path", "get") is None

    def test_returns_none_for_missing_path(self):
        spec = _make_mock_openapi_spec()
        schema = _auto_derive_schema(spec, "/nonexistent", "get")
        assert schema is None

    def test_schema_is_unwrapped(self):
        """The returned schema should have the {result: ...} wrapper stripped."""
        spec = _make_mock_openapi_spec()
        schema = _auto_derive_schema(spec, "/repos/{owner}/{repo}", "get")
        assert "result" not in (schema or {}).get("properties", {})


# ---------------------------------------------------------------------------
# Tests: make_api_resource -- registration
# ---------------------------------------------------------------------------


class TestMakeApiResourceRegistration:
    """Tests that make_api_resource registers resources correctly."""

    def test_registers_resource(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
            format_hint="repository",
        )

        assert handler is not None
        mcp.resource.assert_called()
        # Verify the URI was passed
        uris = [call[0][0] for call in mcp.resource.call_args_list]
        assert "gitea://repos/{owner}/{repo}" in uris

    def test_registers_concrete_uri(self):
        """Concrete URIs (no {param}) should also register correctly."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://user",
            api_path="/user",
            format_hint="user",
        )

        assert handler is not None
        uris = [call[0][0] for call in mcp.resource.call_args_list]
        assert "gitea://user" in uris

    def test_returns_none_when_scope_insufficient(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
            scope="read:admin",
            available_scopes={"read:repository"},
        )

        assert handler is None
        # Should not call mcp.resource
        assert not any(
            "gitea://repos/{owner}/{repo}" in str(c)
            for c in mcp.resource.call_args_list
        )

    def test_tracks_uri_in_registered_uris(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        assert handler is not None
        assert _registered_uris == {"gitea://repos/{owner}/{repo}"}

    def test_adds_wrapper_tag(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        for call_args in mcp.resource.call_args_list:
            if call_args[0][0] == "gitea://repos/{owner}/{repo}":
                tags = call_args[1].get("tags", set())
                assert "wrapper" in tags
                break

    def test_adds_cache_ttl_to_meta(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
            cache_ttl=300,
        )

        for call_args in mcp.resource.call_args_list:
            if call_args[0][0] == "gitea://repos/{owner}/{repo}":
                meta = call_args[1].get("meta", {})
                assert meta.get("cache_ttl") == 300
                break


# ---------------------------------------------------------------------------
# Tests: make_api_resource -- handler behavior
# ---------------------------------------------------------------------------


class TestMakeApiResourceHandler:
    """Tests that the generated handler produces correct ResourceResults."""

    @pytest.mark.asyncio
    async def test_handler_returns_json_resource_result_for_dict_response(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response={"id": 1, "name": "test-repo"})
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
            format_hint="repository",
        )

        result = await handler(owner="test-owner", repo="test-repo")

        assert isinstance(result, ResourceResult)
        assert len(result.contents) == 1
        content = result.contents[0]
        assert content.mime_type == "application/json"
        data = json.loads(content.content)
        assert data == {"id": 1, "name": "test-repo"}

    @pytest.mark.asyncio
    async def test_handler_returns_text_resource_result_for_string_response(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response="plain text error")
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        result = await handler(owner="test-owner", repo="test-repo")

        assert isinstance(result, ResourceResult)
        assert len(result.contents) == 1
        content = result.contents[0]
        assert content.mime_type == "text/plain"
        assert content.content == "plain text error"

    @pytest.mark.asyncio
    async def test_handler_includes_response_schema_in_meta(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response={"login": "dev"})
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://user",
            api_path="/user",
        )

        result = await handler()
        assert isinstance(result, ResourceResult)
        meta = result.contents[0].meta
        assert meta is not None
        assert "response_schema" in meta

    @pytest.mark.asyncio
    async def test_handler_includes_format_hint_in_meta(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response={})
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
            format_hint="repository",
        )

        result = await handler(owner="o", repo="r")
        meta = result.contents[0].meta
        assert meta is not None
        assert meta.get("format_hint") == "repository"


# ---------------------------------------------------------------------------
# Tests: make_api_resource -- error handling
# ---------------------------------------------------------------------------


class TestMakeApiResourceErrorHandling:
    """Tests for error handling in generated handlers."""

    @pytest.mark.asyncio
    async def test_404_raises_resource_error_not_found(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        class Mock404(Exception):
            def __init__(self):
                self.status_code = HTTP_STATUS_NOT_FOUND
                super().__init__("Not found")

        client.request = AsyncMock(side_effect=Mock404())
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
            error_message="Repository '{owner}/{repo}' not found.",
        )

        with pytest.raises(ResourceError) as exc:
            await handler(owner="my-org", repo="my-repo")

        error = exc.value.args[0]
        assert error["code"] == "NOT_FOUND"
        assert "Repository 'my-org/my-repo' not found." in error["message"]
        assert error["resource_type"] == "api"

    @pytest.mark.asyncio
    async def test_non_404_api_error_raises_api_error(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        class Mock500(Exception):
            def __init__(self):
                self.status_code = 500
                super().__init__("Internal error")

        client.request = AsyncMock(side_effect=Mock500())
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        with pytest.raises(ResourceError) as exc:
            await handler(owner="o", repo="r")

        error = exc.value.args[0]
        assert error["code"] == "API_ERROR"
        assert "API error 500" in error["message"]

    @pytest.mark.asyncio
    async def test_unexpected_exception_raises_internal_error(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        client.request = AsyncMock(side_effect=ValueError("boom"))
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        with pytest.raises(ResourceError) as exc:
            await handler(owner="o", repo="r")

        error = exc.value.args[0]
        assert error["code"] == "INTERNAL_ERROR"
        assert "Unexpected error" in error["message"]


# ---------------------------------------------------------------------------
# Tests: make_api_resource -- missing endpoint
# ---------------------------------------------------------------------------


class TestMakeApiResourceMissingEndpoint:
    """Tests that a missing endpoint results in a warning but still registers."""

    def test_warns_and_registers_without_schema_for_missing_endpoint(self):
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()  # has /repos/{owner}/{repo} but not /orgs/{org}

        # This endpoint is NOT in the spec -- should warn but proceed
        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://orgs/{orgname}",
            api_path="/orgs/{orgname}",
            format_hint="user",
        )

        # Handler should still be returned (registered without schema)
        assert handler is not None
        uris = [call[0][0] for call in mcp.resource.call_args_list]
        assert "gitea://orgs/{orgname}" in uris

    def test_registers_with_none_spec(self):
        """When openapi_spec is None, the resource should still register."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        handler = make_api_resource(
            mcp, client, None,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        assert handler is not None


class TestMakeApiResourceQueryParams:
    """Tests for query_params and query_param_validators in make_api_resource."""

    @pytest.mark.asyncio
    async def test_query_params_extracted_into_params_dict(self):
        """query_params kwargs are extracted into params dict, not substituted into path."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response=[{"id": 1}])
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/issues",
            api_path="/repos/{owner}/{repo}/issues",
            query_params=["state"],
        )

        result = await handler(owner="o", repo="r", state="open")
        assert isinstance(result, ResourceResult)
        # Verify the API call was made with params={"state": "open"}
        client.request.assert_called_once()
        _, kwargs = client.request.call_args
        assert kwargs.get("params") == {"state": "open"}

    @pytest.mark.asyncio
    async def test_query_params_not_substituted_into_path(self):
        """query_params kwargs are NOT substituted into the path template."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response=[{"id": 1}])
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/issues",
            api_path="/repos/{owner}/{repo}/issues",
            query_params=["state"],
        )

        await handler(owner="o", repo="r", state="open")
        # The path should remain /repos/o/r/issues (no {state} substitution)
        client.request.assert_called_once()
        args, _ = client.request.call_args
        assert args[1] == "/repos/o/r/issues"

    @pytest.mark.asyncio
    async def test_query_params_ignored_when_none(self):
        """query_params with None value should not be included in params dict."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response=[])
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/issues",
            api_path="/repos/{owner}/{repo}/issues",
            query_params=["state"],
        )

        await handler(owner="o", repo="r", state=None)
        client.request.assert_called_once()
        _, kwargs = client.request.call_args
        # params should be None (not {"state": None})
        assert kwargs.get("params") is None

    @pytest.mark.asyncio
    async def test_query_param_validation_raises_resource_error(self):
        """query_param_validators raises ResourceError for invalid values."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/issues",
            api_path="/repos/{owner}/{repo}/issues",
            query_params=["state"],
            query_param_validators={"state": ["open", "closed"]},
            resource_type="issues",
        )

        with pytest.raises(ResourceError) as exc:
            await handler(owner="o", repo="r", state="invalid")

        error = exc.value.args[0]
        assert error["code"] == "VALIDATION_ERROR"
        assert "Invalid state parameter" in error["message"]
        assert "open" in error["message"]
        assert "closed" in error["message"]

    @pytest.mark.asyncio
    async def test_valid_query_param_passes_validation(self):
        """Valid query param values pass validation and make the API call."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response=[])
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/issues",
            api_path="/repos/{owner}/{repo}/issues",
            query_params=["state"],
            query_param_validators={"state": ["open", "closed"]},
        )

        result = await handler(owner="o", repo="r", state="open")
        assert isinstance(result, ResourceResult)
        client.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_query_params_all_extracted(self):
        """Multiple query params are all extracted into the params dict."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response=[{"id": 1}])
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/releases",
            api_path="/repos/{owner}/{repo}/releases",
            query_params=["draft", "q"],
        )

        await handler(owner="o", repo="r", draft="true", q="search term")
        client.request.assert_called_once()
        _, kwargs = client.request.call_args
        assert kwargs.get("params") == {"draft": "true", "q": "search term"}

    @pytest.mark.asyncio
    async def test_some_query_params_none(self):
        """Some query params with None value should not appear in params dict."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response=[{"id": 1}])
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/releases",
            api_path="/repos/{owner}/{repo}/releases",
            query_params=["draft", "q"],
        )

        await handler(owner="o", repo="r", draft=None, q="urgent")
        client.request.assert_called_once()
        _, kwargs = client.request.call_args
        assert kwargs.get("params") == {"q": "urgent"}

    @pytest.mark.asyncio
    async def test_mixed_query_and_path_params(self):
        """Path params and query params are handled correctly together."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response=[{"id": 1}])
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/releases",
            api_path="/repos/{owner}/{repo}/releases",
            query_params=["draft", "q"],
        )

        await handler(owner="o", repo="r", draft="true", q=None)
        client.request.assert_called_once()
        args, kwargs = client.request.call_args
        assert args[1] == "/repos/o/r/releases"  # path params substituted
        assert kwargs.get("params") == {"draft": "true"}  # only non-None query params


class TestMakeApiResourceOptionalParams:
    """Tests for optional_params in make_api_resource."""

    def test_optional_params_added_to_meta(self):
        """optional_params appears in the meta dict passed to mcp.resource()."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/issues",
            api_path="/repos/{owner}/{repo}/issues",
            optional_params=[{"name": "state", "type": "string", "values": ["open", "closed"]}],
        )

        for args in mcp.resource.call_args_list:
            if args[0][0] == "gitea://repos/{owner}/{repo}/issues":
                meta = args[1].get("meta", {})
                assert "optional_params" in meta
                assert meta["optional_params"] == [
                    {"name": "state", "type": "string", "values": ["open", "closed"]},
                ]
                break

    def test_optional_params_not_set_when_none(self):
        """When optional_params is None, meta should not contain the key."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        for args in mcp.resource.call_args_list:
            if args[0][0] == "gitea://repos/{owner}/{repo}":
                meta = args[1].get("meta", {})
                assert "optional_params" not in meta
                break


# ---------------------------------------------------------------------------
# Tests: make_api_resource -- handler_hook for text/plain resources
# ---------------------------------------------------------------------------


class TestMakeApiResourceHandlerHook:
    """Tests for handler_hook parameter in make_api_resource."""

    async def _hook(self, response: Any) -> str:
        """Simple test hook that returns a processed string."""
        if isinstance(response, str):
            return response
        return f"processed:{response}"

    def test_registers_with_text_plain_mime_type(self):
        """handler_hook should register with mime_type='text/plain'."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/readme",
            api_path="/repos/{owner}/{repo}/contents/README.md",
            handler_hook=self._hook,
        )

        for args in mcp.resource.call_args_list:
            if args[0][0] == "gitea://repos/{owner}/{repo}/readme":
                mime = args[1].get("mime_type", "")
                assert mime == "text/plain"
                break

    def test_registers_without_format_hint(self):
        """handler_hook resources should not set format_hint."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        spec = _make_mock_openapi_spec()

        make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/readme",
            api_path="/repos/{owner}/{repo}/contents/README.md",
            format_hint="readme",
            handler_hook=self._hook,
        )

        for args in mcp.resource.call_args_list:
            if args[0][0] == "gitea://repos/{owner}/{repo}/readme":
                meta = args[1].get("meta", {})
                assert "format_hint" not in meta
                break

    @pytest.mark.asyncio
    async def test_handler_hook_called_with_dict_response(self):
        """handler_hook receives the API response and its result becomes the content."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response={"content": "hello", "encoding": "base64"})
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/readme",
            api_path="/repos/{owner}/{repo}/contents/README.md",
            handler_hook=self._hook,
        )

        result = await handler(owner="o", repo="r")
        assert isinstance(result, ResourceResult)
        content = result.contents[0]
        assert content.mime_type == "text/plain"
        assert content.content == "processed:{'content': 'hello', 'encoding': 'base64'}"

    @pytest.mark.asyncio
    async def test_handler_hook_called_with_string_response(self):
        """handler_hook is called even when API returns a string."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response="raw error message")
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/readme",
            api_path="/repos/{owner}/{repo}/contents/README.md",
            handler_hook=self._hook,
        )

        result = await handler(owner="o", repo="r")
        content = result.contents[0]
        assert content.mime_type == "text/plain"
        assert content.content == "raw error message"

    @pytest.mark.asyncio
    async def test_handler_hook_no_response_schema_in_meta(self):
        """handler_hook resources should have no response_schema in content meta."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response={"content": "test"})
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/readme",
            api_path="/repos/{owner}/{repo}/contents/README.md",
            handler_hook=self._hook,
        )

        result = await handler(owner="o", repo="r")
        meta = result.contents[0].meta
        assert meta is None or "response_schema" not in meta

    @pytest.mark.asyncio
    async def test_hook_resource_handles_404_error(self):
        """Error handling still works when handler_hook is set."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        class Mock404(Exception):
            def __init__(self):
                self.status_code = HTTP_STATUS_NOT_FOUND
                super().__init__("Not found")

        client.request = AsyncMock(side_effect=Mock404())
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/readme",
            api_path="/repos/{owner}/{repo}/contents/README.md",
            error_message="README not found for '{owner}/{repo}'.",
            handler_hook=self._hook,
        )

        with pytest.raises(ResourceError) as exc:
            await handler(owner="o", repo="r")

        error = exc.value.args[0]
        assert error["code"] == "NOT_FOUND"
        assert "README not found for 'o/r'." in error["message"]

    @pytest.mark.asyncio
    async def test_hook_resource_with_query_params(self):
        """handler_hook works together with query_params."""
        mcp = _make_mock_mcp()
        client = _make_mock_client(json_response={"content": "ref content"})
        spec = _make_mock_openapi_spec()

        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}/files/{path*}",
            api_path="/repos/{owner}/{repo}/contents/{path}",
            query_params=["ref"],
            handler_hook=self._hook,
        )

        result = await handler(owner="o", repo="r", path="f.py", ref="main")
        assert isinstance(result, ResourceResult)
        content = result.contents[0]
        assert content.mime_type == "text/plain"
        # Verify ref was passed as a query param
        client.request.assert_called_once()
        _, kwargs = client.request.call_args
        assert kwargs.get("params") == {"ref": "main"}


# ---------------------------------------------------------------------------
# Tests: _decode_base64_content (the real hook function)
# ---------------------------------------------------------------------------


class TestDecodeBase64Content:
    """Tests for ``_decode_base64_content`` in ``custom.py``.

    This is the real-world handler_hook used by readme and file resources
    to decode Gitea's base64-encoded ContentsResponse into plain text.
    """

    @pytest.mark.asyncio
    async def test_string_passthrough(self):
        """String responses are returned as-is."""
        result = await _decode_base64_content("plain text error")
        assert result == "plain text error"

    @pytest.mark.asyncio
    async def test_base64_dict_decoding(self):
        """Dict with encoding='base64' decodes the content field."""
        result = await _decode_base64_content(
            {"content": "SGVsbG8gV29ybGQ=", "encoding": "base64"}
        )
        assert result == "Hello World"

    @pytest.mark.asyncio
    async def test_non_base64_dict_returns_content_field(self):
        """Dict without base64 encoding returns the content field as-is."""
        result = await _decode_base64_content(
            {"content": "raw text content"}
        )
        assert result == "raw text content"

    @pytest.mark.asyncio
    async def test_base64_dict_missing_content_returns_empty_string(self):
        """Dict with encoding='base64' but no content key returns ''."""
        result = await _decode_base64_content({"encoding": "base64"})
        assert result == ""

    @pytest.mark.asyncio
    async def test_base64_dict_none_content_returns_empty_string(self):
        """Dict with encoding='base64' and content=None returns ''."""
        result = await _decode_base64_content(
            {"content": None, "encoding": "base64"}
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_non_dict_non_str_fallback(self):
        """Non-dict, non-str responses are stringified."""
        result = await _decode_base64_content(42)
        assert result == "42"

        result = await _decode_base64_content(None)
        assert result == "None"

        result = await _decode_base64_content([1, 2, 3])
        assert result == "[1, 2, 3]"
