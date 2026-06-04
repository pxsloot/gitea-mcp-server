"""Tests for gitea_mcp_server/server_setup/spec_loader.py.

Covers load_openapi_spec and load_and_convert_spec.
"""

import json

import httpx
import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.server_setup.spec_loader import (
    load_and_convert_spec,
    load_openapi_spec,
)


class SimpleConfig:
    def __init__(self):
        self.url = "https://git.example.com"
        self.token = "test_token"
        self.verify_ssl = False
        self.ssl_cert_file = None
        self.log_level = "ERROR"
        self.log_format = "text"
        self.tool_filtering_enabled = False
        self.enable_lazy_loading = False
        self.tool_prefix = "gitea_"
        self.transport_type = "stdio"
        self.http_host = "127.0.0.1"
        self.http_port = 8080
        self.http_path = "/mcp"
        self.http_cors = None

    @property
    def base_url(self):
        return f"{self.url}/api/v1"


@pytest.fixture
def gitea_client():
    config = SimpleConfig()
    return GiteaClient(config)


@pytest.fixture
def spec_url():
    return "https://git.example.com/swagger.v1.json"


@pytest.fixture
def valid_spec():
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {},
        "definitions": {},
    }


class TestLoadOpenAPISpec:
    @pytest.mark.asyncio
    async def test_success_returns_json_dict(self, gitea_client, spec_url, valid_spec):
        async with respx.mock:
            respx.get(spec_url).respond(200, json=valid_spec)
            result = await load_openapi_spec(gitea_client)
            assert result == valid_spec
            assert result["swagger"] == "2.0"

    @pytest.mark.asyncio
    async def test_string_response_parsed_as_json(self, gitea_client, spec_url, valid_spec):
        async with respx.mock:
            text_body = json.dumps(valid_spec)
            respx.get(spec_url).respond(200, text=text_body)
            result = await load_openapi_spec(gitea_client)
            assert result == valid_spec

    @pytest.mark.asyncio
    async def test_json_decode_error_raises_spec_error(self, gitea_client, spec_url):
        async with respx.mock:
            respx.get(spec_url).respond(200, text="not valid json{")
            with pytest.raises(SpecError, match="Invalid JSON"):
                await load_openapi_spec(gitea_client)

    @pytest.mark.asyncio
    async def test_network_error_raises_spec_error(self, gitea_client, spec_url):
        async with respx.mock:
            respx.get(spec_url).mock(side_effect=httpx.RequestError("connection refused"))
            with pytest.raises(SpecError, match="Failed to fetch or parse"):
                await load_openapi_spec(gitea_client)

    @pytest.mark.asyncio
    async def test_http_error_raises_spec_error(self, gitea_client, spec_url):
        async with respx.mock:
            respx.get(spec_url).respond(500)
            with pytest.raises(SpecError, match="Failed to fetch or parse"):
                await load_openapi_spec(gitea_client)


class TestLoadAndConvertSpec:
    @pytest.mark.asyncio
    async def test_success_path(self, gitea_client, spec_url, valid_spec, monkeypatch):
        async with respx.mock:
            respx.get(spec_url).respond(200, json=valid_spec)

            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.convert_swagger_to_openapi_v3",
                lambda spec: {"openapi": "3.1.0", "info": spec["info"], "paths": spec["paths"]},
            )
            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.load_mcp_extensions",
                lambda: {"tool_names": {}},
            )
            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.apply_mcp_extensions",
                lambda spec, ext: None,
            )

            result = await load_and_convert_spec(gitea_client)
            assert result["openapi"] == "3.1.0"

    @pytest.mark.asyncio
    async def test_conversion_error_raises_spec_error(self, gitea_client, spec_url, valid_spec, monkeypatch):
        async with respx.mock:
            respx.get(spec_url).respond(200, json=valid_spec)

            def failing_convert(spec):
                raise ValueError("conversion failed")

            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.convert_swagger_to_openapi_v3",
                failing_convert,
            )

            with pytest.raises(SpecError, match="Failed to convert"):
                await load_and_convert_spec(gitea_client)

    @pytest.mark.asyncio
    async def test_extension_apply_error_logged_and_ignored(self, gitea_client, spec_url, valid_spec, monkeypatch):
        async with respx.mock:
            respx.get(spec_url).respond(200, json=valid_spec)

            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.convert_swagger_to_openapi_v3",
                lambda spec: {"openapi": "3.1.0", "info": spec["info"], "paths": spec["paths"]},
            )
            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.load_mcp_extensions",
                lambda: {"tool_names": {}},
            )

            def failing_apply(spec, ext):
                raise RuntimeError("extension error")

            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.apply_mcp_extensions",
                failing_apply,
            )

            result = await load_and_convert_spec(gitea_client)
            assert result["openapi"] == "3.1.0"

    @pytest.mark.asyncio
    async def test_spec_error_from_load_passthrough(self, gitea_client, spec_url):
        async with respx.mock:
            respx.get(spec_url).respond(200, text="bad json{")
            with pytest.raises(SpecError, match="Invalid JSON"):
                await load_and_convert_spec(gitea_client)

    @pytest.mark.asyncio
    async def test_no_extensions_loaded(self, gitea_client, spec_url, valid_spec, monkeypatch):
        async with respx.mock:
            respx.get(spec_url).respond(200, json=valid_spec)

            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.convert_swagger_to_openapi_v3",
                lambda spec: {"openapi": "3.1.0", "info": spec["info"], "paths": spec["paths"]},
            )
            monkeypatch.setattr(
                "gitea_mcp_server.server_setup.spec_loader.load_mcp_extensions",
                lambda: None,
            )

            result = await load_and_convert_spec(gitea_client)
            assert result["openapi"] == "3.1.0"

    @pytest.mark.asyncio
    async def test_http_error_during_load_propagates(self, gitea_client, spec_url):
        async with respx.mock:
            respx.get(spec_url).mock(side_effect=httpx.RequestError("connection refused"))
            with pytest.raises(SpecError, match="Failed to fetch or parse"):
                await load_and_convert_spec(gitea_client)
