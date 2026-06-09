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
from gitea_mcp_server.docs_tools import DocManager, register_doc_tools
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.logging_config import setup_logging
from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.permissions import (
    filter_resources_by_permissions,
    filter_tools_by_permissions,
)
from gitea_mcp_server.server_setup.resource_setup import register_all_resources
from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec
from gitea_mcp_server.tools.exclusion import ExclusionTransform, load_exclusion_config
from gitea_mcp_server.tools.extensions_metadata import ExtensionMetadataTransform
from gitea_mcp_server.tools.namespace import GiteaNamespace
from gitea_mcp_server.tools.search import TolerantSearchTransform, register_synthetic_tools
from gitea_mcp_server.unified_search import register_unified_search

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


def _build_server_instructions(doc_manager: DocManager) -> str:
    """Build server instructions by combining base instructions with doc manifest."""
    instructions = load_instructions()
    manifest = doc_manager.get_manifest_markdown()
    if manifest:
        instructions += "\n" + manifest
    return instructions


def _setup_caching_middleware(mcp: FastMCP) -> None:
    """Add response caching and cache invalidation middleware.

    Invalidation middleware must be added after caching middleware.
    """
    logger.info("Adding response caching middleware...")
    caching_middleware = ResponseCachingMiddleware(
        cache_storage=None,
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

    logger.info("Adding cache invalidation middleware...")
    invalidation_middleware = CacheInvalidationMiddleware(caching_middleware)
    mcp.add_middleware(invalidation_middleware)


def _setup_tool_exclusions(mcp: FastMCP, config: Config) -> None:
    """Apply server-level exclusion transform for tools and resources."""
    exclusion_config = load_exclusion_config(
        getattr(config, "exclude_config_path", None)
    )
    if not exclusion_config["exclude"] and not exclusion_config["include"]:
        return
    tool_prefix = config.tool_prefix.rstrip("_") if config.tool_prefix else ""
    logger.info(
        "Adding server-level exclusion transform: %d exclude, %d include patterns",
        len(exclusion_config["exclude"]),
        len(exclusion_config["include"]),
    )
    mcp.add_transform(
        ExclusionTransform(
            exclude=exclusion_config["exclude"],
            include=exclusion_config["include"],
            tool_prefix=tool_prefix,
        )
    )


def _setup_tool_discovery(
    mcp: FastMCP,
    config: Config,
    doc_manager: DocManager,
    extensions: dict[str, Any] | None = None,
) -> None:
    """Setup lazy loading search transform, unified search, namespace, and extensions.

    Search transform must be added BEFORE namespace so namespace can prefix
    the synthetic tools (search_tools, tool_info, call_tool).

    Extension metadata transform must come AFTER namespace so it sees
    consistent prefixed tool names in both ``list_tools`` and ``get_tool``.
    """
    search_transform: TolerantSearchTransform | None = None
    if config.enable_lazy_loading:
        logger.info("Adding search transform for lazy loading...")
        search_transform = TolerantSearchTransform(
            max_results=SEARCH_MAX_RESULTS,
            always_visible=SEARCH_ALWAYS_VISIBLE_TOOLS,
        )
        mcp.add_transform(search_transform)
        logger.info("Registering synthetic tools (call_tool, search_tools, tool_info)...")
        register_synthetic_tools(mcp, search_transform)
    else:
        logger.info("Lazy loading disabled via config; all tools will be listed directly")

    if search_transform is not None:
        logger.info("Registering unified search tool...")
        register_unified_search(mcp, doc_manager, search_transform)

    if config.tool_prefix:
        logger.info("Adding namespace transform with prefix %s", config.tool_prefix)
        mcp.add_transform(GiteaNamespace(config.tool_prefix.rstrip("_")))

    tool_names = (extensions or {}).get("tool_names", {})
    if tool_names:
        prefix = config.tool_prefix or ""
        logger.info(
            "Adding extension metadata transform with %d overrides",
            len(tool_names),
        )
        mcp.add_transform(ExtensionMetadataTransform(tool_names, prefix=prefix))


async def _apply_tool_filtering(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    config: Config,
) -> None:
    """Filter tools and resources based on user permissions if enabled."""
    if not config.tool_filtering_enabled:
        logger.info("Tool filtering is disabled")
        return

    try:
        logger.info("Applying tool permission filtering")
        await filter_tools_by_permissions(mcp, gitea_client, config.token)
    except Exception as e:
        logger.exception(
            "Tool filtering failed, proceeding without filtering",
            extra={"error": str(e)},
        )
    try:
        logger.info("Applying resource permission filtering")
        await filter_resources_by_permissions(mcp, gitea_client, config.token)
    except Exception as e:
        logger.exception(
            "Resource filtering failed, proceeding without filtering",
            extra={"error": str(e)},
        )


async def create_mcp_server(gitea_client: GiteaClient, config: Config | None = None) -> FastMCP:
    """Create the Gitea MCP server from OpenAPI spec.

    Args:
        gitea_client: Initialized GiteaClient to use for API calls
        config: Application configuration (defaults to gitea_client.config)

    Returns:
        Configured FastMCP server instance

    Raises:
        SpecError: If spec loading or conversion fails
    """
    if config is None:
        config = gitea_client.config

    setup_logging(level=config.log_level, log_format=config.log_format)
    logger.info("Starting Gitea MCP Server initialization")

    try:
        openapi_spec, extensions = await load_and_convert_spec(gitea_client, config)
    except SpecError:
        raise
    except Exception as e:
        msg = f"Failed to load or convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    label_manager = LabelManager()
    provider = create_openapi_provider(
        openapi_spec=openapi_spec,
        client=gitea_client.client,
        label_manager=label_manager,
        gitea_client=gitea_client,
    )
    doc_manager = DocManager()

    instructions = _build_server_instructions(doc_manager)

    mcp = FastMCP(
        name="Gitea MCP Server",
        providers=[provider],
        instructions=instructions,
    )

    register_doc_tools(mcp, doc_manager)
    _setup_caching_middleware(mcp)
    _setup_tool_discovery(mcp, config, doc_manager, extensions)
    register_all_resources(mcp, gitea_client, openapi_spec)
    _setup_tool_exclusions(mcp, config)
    await _apply_tool_filtering(mcp, gitea_client, config)

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
