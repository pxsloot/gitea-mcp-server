"""Gitea MCP Server implementation."""

import asyncio
import contextlib
import importlib.resources as pkg_resources
import logging
import sys

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    GetPromptSettings,
    ListResourcesSettings,
    ListToolsSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from gitea_mcp_server.cache_invalidation import CacheInvalidationMiddleware
from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.config import Config
from gitea_mcp_server.constants import (
    CACHE_MAX_ITEM_SIZE,
    CACHE_TTL_DEFAULT,
    CACHE_TTL_RESOURCE_LIST,
    SEARCH_ALWAYS_VISIBLE_TOOLS,
    SEARCH_MAX_RESULTS,
)
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.logging import setup_logging
from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.namespace import GiteaNamespace
from gitea_mcp_server.server_setup.permissions import (
    filter_resources_by_permissions,
    filter_tools_by_permissions,
)
from gitea_mcp_server.server_setup.resource_registry import register_all_resources
from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec
from gitea_mcp_server.server_setup.tool_annotator import TolerantSearchTransform

logger = logging.getLogger(__name__)


def load_instructions() -> str:
    """Load agent instructions from package resource or fallback."""
    try:
        resource_path = pkg_resources.files("gitea_mcp_server").joinpath(
            "docs/agent_instructions.md"
        )
        return resource_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Fallback for editable installs or missing package data
        return (
            "# Gitea MCP Server\n\n"
            "This server provides tools and resources to interact with Gitea.\n\n"
            "## Authentication\n"
            "Auth is configured via environment variables. Verify identity with user_get_current.\n\n"
            "## Lazy Loading\n"
            "This server uses lazy loading. Use search_tools to discover available tools, "
            "tool_info to inspect full tool schemas, and call_tool to execute them.\n\n"
            "See full documentation for detailed usage."
        )


async def create_mcp_server(gitea_client: GiteaClient) -> FastMCP:
    """Create the Gitea MCP server from OpenAPI spec.

    Args:
        gitea_client: Initialized GiteaClient to use for API calls

    Returns:
        Configured FastMCP server instance

    Raises:
        SpecError: If spec loading or conversion fails
    """
    config = gitea_client._config

    # Setup logging as early as possible
    setup_logging(level=config.log_level, log_format=config.log_format)

    logger.info("Starting Gitea MCP Server initialization")

    # Load and convert OpenAPI spec
    try:
        openapi_spec = await load_and_convert_spec(gitea_client)
    except SpecError:
        raise
    except Exception as e:
        msg = f"Failed to load or convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    # Initialize label manager
    label_manager = LabelManager()

    # Create OpenAPI provider
    provider = create_openapi_provider(
        openapi_spec=openapi_spec,
        client=gitea_client.client,
        label_manager=label_manager,
    )

    # Create FastMCP server
    mcp = FastMCP(
        name="Gitea MCP Server",
        providers=[provider],
        instructions=load_instructions(),
    )

    # Add response caching middleware
    logger.info("Adding response caching middleware...")
    caching_middleware = ResponseCachingMiddleware(
        cache_storage=None,  # In-memory cache
        read_resource_settings=ReadResourceSettings(enabled=True, ttl=int(CACHE_TTL_DEFAULT)),
        list_resources_settings=ListResourcesSettings(
            enabled=True, ttl=int(CACHE_TTL_RESOURCE_LIST)
        ),
        list_tools_settings=ListToolsSettings(enabled=False),
        call_tool_settings=CallToolSettings(enabled=False),
        get_prompt_settings=GetPromptSettings(enabled=False),
        max_item_size=CACHE_MAX_ITEM_SIZE,
    )
    mcp.add_middleware(caching_middleware)

    # Add cache invalidation middleware (must come after caching middleware)
    logger.info("Adding cache invalidation middleware...")
    invalidation_middleware = CacheInvalidationMiddleware(caching_middleware)
    mcp.add_middleware(invalidation_middleware)

    # Add search transform for lazy loading (FastMCP 3.x)
    # Must add this BEFORE namespace transform so namespace can prefix the synthetic tools too
    if getattr(config, "enable_lazy_loading", True):
        logger.info("Adding search transform for lazy loading...")
        mcp.add_transform(
            TolerantSearchTransform(
                max_results=SEARCH_MAX_RESULTS,
                always_visible=SEARCH_ALWAYS_VISIBLE_TOOLS,
            )
        )
    else:
        logger.info("Lazy loading disabled via config; all tools will be listed directly")

    # Add namespace transform for tool prefix (FastMCP 3.x built-in)
    # Must be added AFTER search transform so it can prefix the synthetic tools
    if config.tool_prefix:
        logger.info("Adding namespace transform with prefix %s", config.tool_prefix)
        mcp.add_transform(GiteaNamespace(config.tool_prefix.rstrip("_")))

    # Register resources
    logger.info("Registering MCP resources...")
    register_all_resources(mcp, gitea_client, openapi_spec)

    # Apply tool filtering based on user permissions if enabled
    if config.tool_filtering_enabled:
        try:
            logger.info("Applying tool permission filtering")
            await filter_tools_by_permissions(mcp, gitea_client)
        except Exception as e:
            logger.exception(
                "Tool filtering failed, proceeding without filtering",
                extra={"error": str(e)},
            )
        try:
            logger.info("Applying resource permission filtering")
            await filter_resources_by_permissions(mcp, gitea_client)
        except Exception as e:
            logger.exception(
                "Resource filtering failed, proceeding without filtering",
                extra={"error": str(e)},
            )
    else:
        logger.info("Tool filtering is disabled")

    logger.info("Gitea MCP Server initialized successfully")
    return mcp


async def main_async() -> None:
    """Async main entry point."""
    try:
        config = Config.get()
        setup_logging(level=config.log_level, log_format=config.log_format)
    except Exception as e:
        logger.exception("Failed to initialize config")
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    gitea_client = GiteaClient(config)

    try:
        mcp = await create_mcp_server(gitea_client)
    except Exception:
        logger.exception("Failed to initialize server")
        await gitea_client.close()
        sys.exit(1)

    try:
        if config.transport_type == "http":
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
            app = mcp.http_app(
                path=config.http_path,
                middleware=middleware,
            )
            # Add health route to the app's routes (workaround for FastMCP custom_route issue)
            app.routes.insert(0, Route("/health", endpoint=health_check, methods=["GET"]))

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
        else:
            logger.info("Starting MCP server (stdio transport)")
            await mcp.run_stdio_async()
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
    except Exception:
        logger.exception("Server crashed")
        sys.exit(1)
    finally:
        with contextlib.suppress(Exception):
            await gitea_client.close()
        logging.shutdown()


def main() -> None:
    """Synchronous entry point that runs the async main."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
