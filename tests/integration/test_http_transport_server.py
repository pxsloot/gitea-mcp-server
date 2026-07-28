"""Integration tests for real uvicorn HTTP transport.

These tests verify the full stack: uvicorn → ASGI → MCP responds to real HTTP.
Each test starts its own short-lived uvicorn server.  The ~0.5s startup cost
is acceptable because there are only 2 tests and xdist runs them in parallel
with other modules.

Endpoint-level behaviour (CORS, custom path, route registration) is tested
at the ASGI level in ``test_server_http.py`` with near-zero startup cost.
This file covers what only a real uvicorn server can: the full transport
stack and graceful shutdown.
"""

import asyncio
import contextlib
import socket
from collections.abc import Generator

import httpx
import pytest

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig


@pytest.fixture(scope="module", autouse=True)
def _patch_spec_loader() -> Generator[None, None, None]:
    """Patch the OpenAPI spec loader once for this module.

    Module-scoped to avoid leaking the patch to other test modules.
    The user / version fetch calls during ``create_mcp_server()`` are
    **not** patched — they fail harmlessly (handled gracefully by
    server.py), and we accept the log noise to keep the fixture simple.
    """
    mp = pytest.MonkeyPatch()

    async def mock_load_and_convert_spec(gitea_client, config=None):
        return (
            {
                "swagger": "2.0",
                "info": {"title": "Gitea API", "version": "1.0"},
                "paths": {},
                "definitions": {},
            },
            {},
            {},
            set(),
        )

    mp.setattr(
        "gitea_mcp_server.server.load_and_convert_spec",
        mock_load_and_convert_spec,
    )
    yield
    mp.undo()


async def _start_server(config, *, health_path="/health"):
    """Start a uvicorn server, yield ``(base_url, cleanup_coro)``.

    The caller is responsible for calling ``cleanup()`` after the test.
    Health endpoint is inserted at ``{base_url}{health_path}``.
    """
    gitea_client = GiteaClient(config)
    mcp = await create_mcp_server(gitea_client)

    # Find free port
    if config.http_port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((config.http_host, 0))
            s.listen(1)
            port = s.getsockname()[1]
        config.http_port = port

    app = mcp.http_app(path=config.http_path)
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health_check(_):
        return JSONResponse({"status": "ok"})

    app.routes.insert(0, Route(health_path, endpoint=health_check, methods=["GET"]))

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app=app, host=config.http_host, port=config.http_port, log_level="error",
        )
    )
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)

    base_url = f"http://{config.http_host}:{config.http_port}"

    async def _cleanup() -> None:
        await server.shutdown()
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        await gitea_client.close()

    return base_url, _cleanup


@pytest.mark.slow
class TestRealHttpServer:
    """Real uvicorn HTTP smoke tests.

    Each test starts its own uvicorn server (the ~0.5s startup cost is
    acceptable since these are few and xdist runs them in parallel with
    other modules).
    """

    @pytest.mark.asyncio
    async def test_health_endpoint(self) -> None:
        """Real uvicorn serves the /health endpoint correctly (full-stack smoke test)."""
        config = SimpleConfig(transport_type="http", http_port=0)
        base_url, cleanup = await _start_server(config)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{base_url}/health")
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
                assert response.headers["Content-Type"] == "application/json"
        finally:
            await cleanup()

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self) -> None:
        """Server can be shut down cleanly, becoming unreachable after shutdown."""
        config = SimpleConfig(transport_type="http", http_port=0)
        base_url, cleanup = await _start_server(config)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{base_url}/health")
                assert response.status_code == 200
        finally:
            await cleanup()

        # After cleanup, server should be unreachable
        with pytest.raises((httpx.RequestError, ConnectionRefusedError)):
            async with httpx.AsyncClient() as client:
                await client.get(f"{base_url}/health", timeout=1.0)
