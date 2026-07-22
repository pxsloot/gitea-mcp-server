"""Tests for the resource factory (``make_api_resource``)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.resources import ResourceContent, ResourceResult

from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND
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

        before = set(_registered_uris)
        handler = make_api_resource(
            mcp, client, spec,
            uri="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )

        assert handler is not None
        assert "gitea://repos/{owner}/{repo}" in _registered_uris
        assert _registered_uris == before  # already present before the call

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


