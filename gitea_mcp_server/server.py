"""Gitea MCP Server implementation."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.resources as pkg_resources
import logging
import sys
from typing import TYPE_CHECKING, Any

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
    SEARCH_MAX_RESULTS,
)
from gitea_mcp_server.docs_tools import DocManager, register_doc_tools
from gitea_mcp_server.exceptions import GiteaAPIError, SpecError
from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.logging_config import setup_logging
from gitea_mcp_server.server_setup.http_server import run_http_server

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.resource_setup import register_all_resources
from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec
from gitea_mcp_server.tools.extensions_metadata import ExtensionMetadataTransform
from gitea_mcp_server.tools.namespace import GiteaNamespace
from gitea_mcp_server.tools.search import TolerantSearchTransform, register_synthetic_tools
from gitea_mcp_server.tools.type_info import register_type_tools
from gitea_mcp_server.tools.virtual_params import apply_scope_filter
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


def substitute_placeholders(text: str, values: dict[str, str]) -> str:
    """Replace ``{{PLACEHOLDER}}`` tokens with runtime values.

    Unknown placeholders pass through unchanged so the raw doc still reads
    sanely if substitution is skipped or a value is unavailable.

    Args:
        text: Text containing ``{{PLACEHOLDER}}`` tokens.
        values: Mapping of placeholder name (without braces) to replacement
            string.  All replacements are plain markdown strings.

    Returns:
        Text with known placeholders replaced.
    """
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _build_server_instructions(
    placeholder_values: dict[str, str] | None = None,
) -> str:
    """Build server instructions by substituting placeholders.

    All dynamic content — tool prefix, user identity, token scopes, server
    type, guide list — flows through ``{{PLACEHOLDER}}`` substitution rather
    than hardcoded appends.  This keeps the doc structure in the markdown
    file and the dynamic values in a single substitution pass.

    Args:
        placeholder_values: Mapping of placeholder name to markdown value.
            ``None`` or empty skips substitution (placeholders pass through).

    Returns:
        Fully resolved server instructions markdown.
    """
    instructions = load_instructions()
    if placeholder_values:
        instructions = substitute_placeholders(instructions, placeholder_values)
    return instructions


def _setup_caching_middleware(
    mcp: FastMCP,
    label_service: LabelService | None = None,
) -> None:
    """Add response caching and cache invalidation middleware.

    Invalidation middleware must be added after caching middleware.

    Args:
        mcp: The FastMCP server instance.
        label_service: Optional LabelService for label cache invalidation.
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
    invalidation_middleware = CacheInvalidationMiddleware(
        caching_middleware,
        label_service=label_service,
    )
    mcp.add_middleware(invalidation_middleware)


def _setup_tool_discovery(  # noqa: PLR0913 - six params are acceptable for this orchestration function
    mcp: FastMCP,
    config: Config,
    doc_manager: DocManager,
    extensions: dict[str, Any] | None = None,
    openapi_spec: OpenAPISpec | None = None,
    filtered_tools_info: dict[str, Any] | None = None,
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
        )
        mcp.add_transform(search_transform)
        logger.info("Registering synthetic tools (call_tool, search_tools, tool_info)...")
        register_synthetic_tools(
            mcp, search_transform,
            tool_prefix=config.tool_prefix,
            openapi_spec=openapi_spec,
            filtered_tools_info=filtered_tools_info,
        )
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


async def _apply_virtual_param_scope_filter(
    available_scopes: set[str] | None,
) -> None:
    """Apply token scopes to scope-gated virtual params (e.g. ``sudo``).

    Tool/resource *visibility* filtering now happens at spec-prep time via
    ``route_map_fn`` (see ``spec_loader.load_and_convert_spec``).  This helper
    only governs virtual-parameter visibility, which is a separate concern that
    still requires the token's scopes at startup.

    Args:
        available_scopes: Set of scopes the token has, or None if scope data
            could not be fetched (in which case no gating is applied).
    """
    if available_scopes is None:
        logger.info("No token scopes available, skipping virtual param scope gating")
        return
    try:
        apply_scope_filter(available_scopes)
        logger.info(
            "Virtual param scope filter applied",
            extra={"scopes": sorted(available_scopes)},
        )
    except Exception as e:
        logger.exception(
            "Failed to apply virtual param scope filter",
            extra={"error": str(e)},
        )


async def create_mcp_server(
    gitea_client: GiteaClient,
    config: Config | None = None,
    lifespan: Any = None,
) -> FastMCP:
    """Create the Gitea MCP server from OpenAPI spec.

    Args:
        gitea_client: Initialized GiteaClient to use for API calls
        config: Application configuration (defaults to gitea_client.config)
        lifespan: FastMCP lifespan context manager (optional)

    Returns:
        Configured FastMCP server instance

    Raises:
        SpecError: If spec loading or conversion fails
    """
    if config is None:
        config = gitea_client.config

    logger.info("Starting Gitea MCP Server initialization")

    try:
        openapi_spec, extensions, filtered_tools_info, excluded_routes = (
            await load_and_convert_spec(gitea_client, config)
        )
    except SpecError:
        raise
    except Exception as e:
        msg = f"Failed to load or convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    # ``excluded_routes`` already honours ``config.tool_filtering_enabled``
    # (scope filtering is skipped when disabled, but deprecated-endpoint and
    # config-exclusion filtering always apply — matching the old behaviour).
    # The available scopes are only needed to gate scope-sensitive virtual
    # params (e.g. ``sudo``); when filtering is disabled we leave them ungated.
    available_scopes = (
        set(filtered_tools_info.get("available_scopes", []))
        if filtered_tools_info and config.tool_filtering_enabled
        else None
    )

    label_service = LabelService()
    provider = create_openapi_provider(
        openapi_spec=openapi_spec,
        gitea_client=gitea_client,
        label_service=label_service,
        excluded_routes=excluded_routes,
        response_format=config.response_format,
    )
    doc_manager = DocManager()

    # ── Build placeholder values for agent instructions ────────────────
    # We use raw HTTP calls (gitea_client.request) rather than the
    # generated tools because the instructions must be ready at server
    # construction time — before FastMCP is initialised and tools exist.
    # The gitea_client already has retry, error wrapping, and rate-limit
    # handling built in, matching the pattern already established by
    # fetch_token_scopes() in spec_loader.py.
    placeholder_values: dict[str, str] = {
        "TOOL_PREFIX": config.tool_prefix.rstrip("_"),
    }

    # User login — lightweight GET /user, same endpoint fetch_token_scopes
    # already calls for scope filtering.
    try:
        user_data = await gitea_client.request("GET", "/user")
        if isinstance(user_data, dict):
            placeholder_values["USER_LOGIN"] = str(user_data.get("login", "unknown"))
    except (OSError, GiteaAPIError):
        logger.info("Could not fetch user login for instructions placeholder")

    # Token scopes — already computed in filtered_tools_info.
    scopes: list[str] = filtered_tools_info.get("available_scopes", [])
    if scopes:
        placeholder_values["TOKEN_SCOPES"] = ", ".join(
            f"`{s}`" for s in scopes
        )

    # Server type (Gitea vs Forgejo) — detectable from the already-loaded
    # OpenAPI spec info block; no extra API call needed.
    server_title: str = (
        str(openapi_spec.get("info", {}).get("title", ""))
        if openapi_spec
        else ""
    )
    if "forgejo" in server_title.lower():
        placeholder_values["SERVER_TYPE"] = "Forgejo"
    elif "gitea" in server_title.lower():
        placeholder_values["SERVER_TYPE"] = "Gitea"
    # else leave placeholder unresolved (passes through unchanged)

    # Guide manifest — DocManager builds the markdown table.
    guide_manifest = doc_manager.get_manifest_markdown()
    if guide_manifest:
        placeholder_values["GUIDES_LIST"] = guide_manifest

    instructions = _build_server_instructions(placeholder_values)

    mcp = FastMCP(
        name="Gitea MCP Server",
        providers=[provider],
        instructions=instructions,
        lifespan=lifespan,
    )

    register_doc_tools(mcp, doc_manager)
    _setup_caching_middleware(mcp, label_service=label_service)
    _setup_tool_discovery(
        mcp, config, doc_manager, extensions,
        openapi_spec=openapi_spec,
        filtered_tools_info=filtered_tools_info,
    )
    register_all_resources(
        mcp,
        gitea_client,
        openapi_spec,
        filtered_tools_info=filtered_tools_info,
        available_scopes=available_scopes,
    )
    register_type_tools(mcp, openapi_spec=openapi_spec)
    await _apply_virtual_param_scope_filter(available_scopes)

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

    @contextlib.asynccontextmanager
    async def app_lifespan(_server: Any) -> Any:
        """FastMCP lifespan: provides GiteaClient to tools via lifespan context."""
        yield {"gitea_client": gitea_client}
        await gitea_client.close()

    try:
        mcp = await create_mcp_server(gitea_client, lifespan=app_lifespan)
    except Exception:
        logger.exception("Failed to initialize server")
        with contextlib.suppress(Exception):
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
        logging.shutdown()


def main() -> None:
    """Synchronous entry point that runs the async main."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
