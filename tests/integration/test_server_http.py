"""Integration tests for server.py HTTP transport via main_async().

Tests that main_async() correctly composes the HTTP app with CORS,
health endpoint, and custom MCP path - exercising the actual entry
point rather than manually wiring the ASGI app.

Health endpoint requests use ASGI transport (no lifespan needed for
static GET). MCP route presence is verified by inspecting the route
table.
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
            config=config,
            client=AsyncMock(),
            request=AsyncMock(return_value={}),
            close=AsyncMock(),
        ),
    )

    monkeypatch.setattr(
        "gitea_mcp_server.server.load_and_convert_spec",
        AsyncMock(
            return_value=(
                {
                    "openapi": "3.1.0",
                    "info": {"title": "Test", "version": "1"},
                    "paths": {},
                },
                {},
                {},
                set(),
            )
        ),
    )


@pytest.fixture
def captured_app(monkeypatch):
    """Capture the composed ASGI app from uvicorn.Config for route/middleware inspection.

    Yields a single-element list populated lazily by main_async().
    Tests access via captured_app[0] - the wrapper is needed because
    uvicorn.Config.__init__ hasn't been called yet at fixture yield time.
    """
    import uvicorn

    apps: list = []

    original_config_init = uvicorn.Config.__init__

    def patched_config_init(self, app, **kwargs):
        apps.append(app)
        original_config_init(self, app=app, **kwargs)

    monkeypatch.setattr("uvicorn.Config.__init__", patched_config_init)

    async def noop_serve(self):
        pass

    monkeypatch.setattr("uvicorn.Server.serve", noop_serve)

    yield apps


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, captured_app, monkeypatch):
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        app = captured_app[0]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_health_content_type(self, captured_app, monkeypatch):
        """Health endpoint should return application/json content type."""
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        app = captured_app[0]
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
    async def test_mcp_route_at_default_path(self, captured_app, monkeypatch):
        """MCP route should be registered at the default /mcp path."""
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig(http_path="/mcp"))
        await main_async()
        assert self._find_route(captured_app[0], "/mcp") is not None

    @pytest.mark.asyncio
    async def test_mcp_route_at_custom_path(self, captured_app, monkeypatch):
        """MCP route should be registered at a custom /api/mcp path when configured."""
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig(http_path="/api/mcp"))
        await main_async()
        assert self._find_route(captured_app[0], "/api/mcp") is not None

    @pytest.mark.asyncio
    async def test_health_route_exists(self, captured_app, monkeypatch):
        """Health route should be present in the app routes."""
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        assert self._find_route(captured_app[0], "/health") is not None

    @pytest.mark.asyncio
    async def test_both_health_and_mcp_routes_present(self, captured_app, monkeypatch):
        """Both health and MCP routes should be registered simultaneously."""
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig())
        await main_async()
        assert self._find_route(captured_app[0], "/health") is not None
        assert self._find_route(captured_app[0], "/mcp") is not None


class TestCORSConfiguration:
    def _get_middleware(self, app):
        """Return (user_middleware, CORSMiddleware instance) from a Starlette app."""
        user_mw = getattr(app, "user_middleware", [])
        for mw in user_mw:
            if isinstance(mw, StarletteMiddleware) and mw.cls is CORSMiddleware:
                return user_mw, mw
        return user_mw, None

    @pytest.mark.asyncio
    async def test_cors_middleware_on_mcp_app_when_configured(self, captured_app, monkeypatch):
        """CORS middleware should be present when http_cors is configured."""
        monkeypatch.setattr(
            "gitea_mcp_server.server.Config.get",
            lambda: SimpleHTTPConfig(http_cors=["https://example.com"]),
        )
        await main_async()
        _, cors = self._get_middleware(captured_app[0])
        assert cors is not None, "Expected CORSMiddleware on mcp_app"

    @pytest.mark.asyncio
    async def test_no_cors_middleware_when_not_configured(self, captured_app, monkeypatch):
        """CORS middleware should be absent when http_cors is not configured."""
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig(http_cors=None))
        await main_async()
        _, cors = self._get_middleware(captured_app[0])
        assert cors is None, "Expected no CORSMiddleware on mcp_app"

    @pytest.mark.asyncio
    async def test_cors_allowed_origins(self, captured_app, monkeypatch):
        """CORS configuration should propagate allowed origins and methods."""
        monkeypatch.setattr(
            "gitea_mcp_server.server.Config.get",
            lambda: SimpleHTTPConfig(http_cors=["https://example.com"]),
        )
        await main_async()
        user_mw, cors = self._get_middleware(captured_app[0])
        assert cors is not None, "Expected CORSMiddleware"
        origins = cors.kwargs.get("allow_origins", [])
        assert "https://example.com" in origins
        methods = cors.kwargs.get("allow_methods", [])
        assert "GET" in methods
        assert "POST" in methods

    @pytest.mark.asyncio
    async def test_health_cors_present_when_configured(self, captured_app, monkeypatch):
        """Test that /health returns CORS headers when CORS is configured."""
        monkeypatch.setattr(
            "gitea_mcp_server.server.Config.get",
            lambda: SimpleHTTPConfig(http_cors=["https://example.com"]),
        )
        await main_async()
        app = captured_app[0]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health", headers={"Origin": "https://example.com"})
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "https://example.com"

    @pytest.mark.asyncio
    async def test_health_no_cors_when_not_configured(self, captured_app, monkeypatch):
        """Test that /health has no CORS headers when CORS is not configured."""
        monkeypatch.setattr("gitea_mcp_server.server.Config.get", lambda: SimpleHTTPConfig(http_cors=None))
        await main_async()
        app = captured_app[0]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health", headers={"Origin": "https://evil.com"})
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers
