"""Gitea MCP Server implementation."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.resources as pkg_resources
import logging
import sys
from typing import Any

import fastmcp.server.server as _fastmcp_server_mod
from fastmcp import FastMCP
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    GetPromptSettings,
    ListResourcesSettings,
    ListToolsSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)

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
from gitea_mcp_server.server_setup.http_server import run_http_server
from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.resource_setup import register_all_resources
from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec
from gitea_mcp_server.tool_filter import PermissionFilterTransform, fetch_token_scopes
from gitea_mcp_server.tools.exclusion import ExclusionTransform, load_exclusion_config
from gitea_mcp_server.tools.extensions_metadata import ExtensionMetadataTransform
from gitea_mcp_server.tools.namespace import GiteaNamespace
from gitea_mcp_server.tools.search import TolerantSearchTransform, register_synthetic_tools
from gitea_mcp_server.unified_search import register_unified_search

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastMCP compat: fix _run_middleware 'ctx' → 'context' param name regression
#
# fastmcp 3.4.0+ renamed the parameter in _run_middleware's `wrapped` closure
# from `context` to `ctx`, but their own ResponseCachingMiddleware still calls
# `call_next(context=context)` with a keyword argument. This causes:
#   TypeError: wrapped() got an unexpected keyword argument 'context'
#
# Patch _run_middleware to use the original parameter name `context` so that
# keyword calls from fastmcp's built-in middleware work correctly.
# Remove this block when fastmcp fixes the regression upstream.
# Tracked in https://git.home.lan/mcp-server/gitea-mcp-server/issues/374
# ---------------------------------------------------------------------------
_fastmcp_run_mw = _fastmcp_server_mod.FastMCP._run_middleware


async def _compat_run_middleware(
    self: FastMCP,
    context: Any,
    call_next: Any,
) -> Any:
    """Patched _run_middleware using 'context' not 'ctx' (fastmcp regression fix)."""
    chain = call_next
    for mw in reversed(self.middleware):
        next_chain: Any = chain

        async def wrapped(
            context: Any = None,
            mw: Any = mw,
            call_next: Any = next_chain,
        ) -> Any:
            return await mw(context, call_next)

        chain = wrapped
    return await chain(context)


_fastmcp_server_mod.FastMCP._run_middleware = _compat_run_middleware  # type: ignore[method-assign]
# ---------------------------------------------------------------------------


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
    exclusion_config = load_exclusion_config(getattr(config, "exclude_config_path", None))
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
        register_synthetic_tools(mcp, search_transform, tool_prefix=config.tool_prefix)
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


async def _apply_permission_filter(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    config: Config,
) -> None:
    """Add a PermissionFilterTransform if user permission filtering is enabled.

    Fetches the available token scopes once at startup and attaches them to a
    ``Transform`` that filters tools/resources at query time.  If the token
    scopes cannot be fetched (auth failure, network error, etc.), the transform
    is simply not added and all tools remain visible.
    """
    if not config.tool_filtering_enabled:
        logger.info("Permission filtering is disabled")
        return

    try:
        logger.info("Fetching token scopes for permission filtering")
        available_scopes = await fetch_token_scopes(gitea_client, config.token)
    except Exception as e:
        logger.exception(
            "Failed to fetch token scopes, proceeding without filtering",
            extra={"error": str(e)},
        )
        return

    if available_scopes is None:
        logger.warning("No token scopes available, proceeding without filtering")
        return

    try:
        logger.info(
            "Adding PermissionFilterTransform",
            extra={"scopes": sorted(available_scopes)},
        )
        mcp.add_transform(PermissionFilterTransform(available_scopes))
    except Exception as e:
        logger.exception(
            "Failed to add PermissionFilterTransform, proceeding without filtering",
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
    await _apply_permission_filter(mcp, gitea_client, config)

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
            await run_http_server(mcp, config)
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
