"""Integration tests for server.py HTTP transport via main_async().

Tests that main_async() correctly composes the HTTP app with CORS,
health endpoint, and custom MCP path — exercising the actual entry
point rather than manually wiring the ASGI app.

Health endpoint requests use ASGI transport (no lifespan needed for
static GET). MCP route presence is verified by inspecting the route
table. CORS is verified on the inner mcp_app (the outer Starlette
composition in main_async does not propagate middleware to the
health route — this is a known limitation tracked separately).
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.middleware.cors import CORSMiddleware

from gitea_mcp_server.server import main_async


class SimpleHTTPConfig:
    def __init__(self, **overrides):
        self.url = "https://git.example.com"
        self.token = "test_token"
        self.verify_ssl = False
        self.ssl_cert_file = None
        self.log_level = "ERROR"
        self.log_format = "text"
        self.tool_filtering_enabled = False
        self.enable_lazy_loading = False
        self.tool_prefix = "gitea_"
        self.transport_type = "http"
        self.http_host = "127.0.0.1"
        self.http_port = 0
        self.http_path = "/mcp"
        self.http_cors = None
        for k, v in overrides.items():
            setattr(self, k, v)

    @property
    def base_url(self):
        return f"{self.url}/api/v1"


@pytest.fixture(autouse=True)
def common_patches(monkeypatch):
    """Patch Config.get, GiteaClient, and spec loading for all tests."""

    monkeypatch.setattr(
        "gitea_mcp_server.server.Config.get",
        lambda: SimpleHTTPConfig(),
    )

    monkeypatch.setattr(
        "gitea_mcp_server.server.GiteaClient",
        lambda config: AsyncMock(
            _config=config,
            client=AsyncMock(),
            request=AsyncMock(return_value={}),
            close=AsyncMock(),
        ),
    )

    monkeypatch.setattr(
        "gitea_mcp_server.server.load_and_convert_spec",
        AsyncMock(
            return_value={
                "openapi": "3.1.0",
                "info": {"title": "Test", "version": "1"},
                "paths": {},
            }
        ),
    )


@pytest.fixture
def captured_apps(monkeypatch):
    """Capture both the outer composed app and the inner mcp_app."""
    import uvicorn

    containers = {}

    original_config_init = uvicorn.Config.__init__

    def patched_config_init(self, app, **kwargs):
        containers["outer_app"] = app
        original_config_init(self, app=app, **kwargs)

    monkeypatch.setattr("uvicorn.Config.__init__", patched_config_init)

    import gitea_mcp_server.server as server_mod

    original_http_app = getattr(server_mod.FastMCP, "http_app", None)
    if original_http_app is not None and not isinstance(original_http_app, property):

        def capturing_http_app(self, *args, **kwargs):
            result = original_http_app(self, *args, **kwargs)
            containers["mcp_app"] = result
            return result

        monkeypatch.setattr(
            "gitea_mcp_server.server.FastMCP.http_app",
            capturing_http_app,
        )

    async def noop_serve(self):
        pass

    monkeypatch.setattr("uvicorn.Server.serve", noop_serve)

    yield containers


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, captured_apps, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        app = captured_apps["outer_app"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_health_content_type(self, captured_apps, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        app = captured_apps["outer_app"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
            assert resp.headers["content-type"] == "application/json"


class TestRouteConfiguration:
    def _find_route(self, app, path):
        for route in app.routes:
            if route.path == path:
                return route
        return None

    @pytest.mark.asyncio
    async def test_mcp_route_at_default_path(self, captured_apps, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig(http_path="/mcp"))
        await main_async()
        assert self._find_route(captured_apps["outer_app"], "/mcp") is not None

    @pytest.mark.asyncio
    async def test_mcp_route_at_custom_path(self, captured_apps, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig(http_path="/api/mcp"))
        await main_async()
        assert self._find_route(captured_apps["outer_app"], "/api/mcp") is not None

    @pytest.mark.asyncio
    async def test_health_route_exists(self, captured_apps, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        assert self._find_route(captured_apps["outer_app"], "/health") is not None

    @pytest.mark.asyncio
    async def test_both_health_and_mcp_routes_present(self, captured_apps, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        assert self._find_route(captured_apps["outer_app"], "/health") is not None
        assert self._find_route(captured_apps["outer_app"], "/mcp") is not None


class TestCORSConfiguration:
    def _get_middleware(self, app):
        """Return (user_middleware, CORSMiddleware instance) from a Starlette app."""
        user_mw = getattr(app, "user_middleware", [])
        for mw in user_mw:
            if isinstance(mw, StarletteMiddleware) and mw.cls is CORSMiddleware:
                return user_mw, mw
        return user_mw, None

    @pytest.mark.asyncio
    async def test_cors_middleware_on_mcp_app_when_configured(self, captured_apps, monkeypatch):
        monkeypatch.setattr(
            "gitea_mcp_server.server.Config.get",
            lambda: SimpleHTTPConfig(http_cors=["https://example.com"]),
        )
        await main_async()
        mcp_app = captured_apps.get("mcp_app")
        if mcp_app is None:
            pytest.skip("mcp_app not captured")
        _, cors = self._get_middleware(mcp_app)
        assert cors is not None, "Expected CORSMiddleware on mcp_app"

    @pytest.mark.asyncio
    async def test_no_cors_middleware_when_not_configured(self, captured_apps, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig(http_cors=None))
        await main_async()
        mcp_app = captured_apps.get("mcp_app")
        if mcp_app is None:
            pytest.skip("mcp_app not captured")
        _, cors = self._get_middleware(mcp_app)
        assert cors is None, "Expected no CORSMiddleware on mcp_app"

    @pytest.mark.asyncio
    async def test_cors_allowed_origins(self, captured_apps, monkeypatch):
        monkeypatch.setattr(
            "gitea_mcp_server.server.Config.get",
            lambda: SimpleHTTPConfig(http_cors=["https://example.com"]),
        )
        await main_async()
        mcp_app = captured_apps.get("mcp_app")
        if mcp_app is None:
            pytest.skip("mcp_app not captured")
        user_mw, cors = self._get_middleware(mcp_app)
        if cors is None:
            pytest.skip("CORSMiddleware not found on mcp_app")
        origins = cors.kwargs.get("allow_origins", [])
        assert "https://example.com" in origins
        methods = cors.kwargs.get("allow_methods", [])
        assert "GET" in methods
        assert "POST" in methods
