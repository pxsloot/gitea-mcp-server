"""HTTP server startup for MCP server.

Extracted from ``server.py`` to reduce its ``main_async`` function size.
"""

import logging

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from gitea_mcp_server.config import Config

logger = logging.getLogger(__name__)


async def run_http_server(mcp: FastMCP, config: Config) -> None:
    """Run the MCP server over HTTP transport.

    Configures CORS middleware (if origins specified), adds a health check
    endpoint, and starts the uvicorn server.

    Args:
        mcp: The configured FastMCP server instance.
        config: Application configuration with HTTP settings.
    """
    # Configure CORS middleware if origins specified
    cors_origins = config.http_cors or []
    middleware = []
    if cors_origins:
        middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=cors_origins,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=[
                    "mcp-protocol-version",
                    "mcp-session-id",
                    "Authorization",
                    "Content-Type",
                ],
                expose_headers=["mcp-session-id"],
            )
        ]

    # Create health check endpoint function
    async def health_check(_: object) -> JSONResponse:
        """Health check endpoint for container orchestration."""
        return JSONResponse({"status": "ok"})

    # Create HTTP app with middleware and custom path
    mcp_app = mcp.http_app(
        path=config.http_path,
        middleware=middleware,
    )
    # Insert health check route into mcp_app so it inherits CORS middleware
    mcp_app.routes.insert(0, Route("/health", endpoint=health_check, methods=["GET"]))
    app = mcp_app

    logger.info(
        "Starting MCP server (HTTP transport) on http://%s:%s with MCP path %s",
        config.http_host,
        config.http_port,
        config.http_path,
    )
    logger.info(
        "Health check available at http://%s:%s/health",
        config.http_host,
        config.http_port,
    )

    # Run with uvicorn
    uvicorn_config = uvicorn.Config(
        app=app,
        host=config.http_host,
        port=config.http_port,
    )
    server = uvicorn.Server(uvicorn_config)
    await server.serve()
