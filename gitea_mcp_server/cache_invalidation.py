"""Cache invalidation for write operations in Gitea MCP Server.

This module provides functionality to invalidate cached MCP resources when
data is modified via tool calls. It addresses the issue where resources
remain cached even after the underlying data changes.

The system works by:
1. Defining cache invalidation patterns (resource URI templates) that can be affected
2. During tool customization, each write tool is registered with the patterns it invalidates
3. After tool execution, the middleware computes concrete URIs from tool arguments
   and clears them from the cache.

The invalidation map is populated at server startup by analyzing each tool's
HTTP path and method, ensuring automatic coverage of all write operations.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import mcp
    from fastmcp.server.middleware.caching import ResponseCachingMiddleware
    from fastmcp.tools.tool import ToolResult

from fastmcp.server.middleware.caching import (
    _get_auth_partition_key,
)
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)

from gitea_mcp_server.constants import (
    PATTERN_FILES,
    PATTERN_ISSUES_LIST,
    PATTERN_PULLS_LIST,
    PATTERN_REPO,
    RESOURCE_PATTERN_FILES,
    RESOURCE_PATTERN_ISSUES_LIST,
    RESOURCE_PATTERN_PULLS_LIST,
    RESOURCE_PATTERN_REPO,
)

logger = logging.getLogger(__name__)

# Resource URI templates used to construct concrete URIs for invalidation.
# These are imported from constants to ensure consistency across the codebase.
# The dictionary keys are pattern names (used by server to reference patterns).
RESOURCE_URI_PATTERNS: dict[str, str] = {
    # Issues
    PATTERN_ISSUES_LIST: RESOURCE_PATTERN_ISSUES_LIST,
    # Pull requests
    PATTERN_PULLS_LIST: RESOURCE_PATTERN_PULLS_LIST,
    # Repository
    PATTERN_REPO: RESOURCE_PATTERN_REPO,
    # File contents (using filepath as parameter name to match Gitea API)
    PATTERN_FILES: RESOURCE_PATTERN_FILES,
}

# Global invalidation map populated at server startup.
# Maps tool name (bare operationId, not namespaced) -> list of pattern names
# (keys in RESOURCE_URI_PATTERNS).
TOOL_INVALIDATION_MAP: dict[str, list[str]] = {}

# Namespace prefix applied at query time by GiteaNamespace (must match
# config.tool_prefix minus the trailing underscore).
_GITEA_TOOL_PREFIX = "gitea_"


def register_tool_invalidation(tool_name: str, patterns: list[str]) -> None:
    """Register cache invalidation patterns for a tool.

    This is called during server initialization for each write tool.

    Args:
        tool_name: Name of the tool as registered with FastMCP
        patterns: List of pattern names (from RESOURCE_URI_PATTERNS) to invalidate
    """
    if patterns:
        TOOL_INVALIDATION_MAP[tool_name] = patterns
        logger.debug(
            "Registered cache invalidation for tool %s: patterns=%s",
            tool_name,
            patterns,
        )


def _compute_cache_key(uri: str, auth_key: str | None = None) -> str:
    """Compute the cache key for a resource URI using SHA256.

    This mirrors FastMCP's ``_make_read_resource_cache_key`` to ensure we
    compute the exact same key that the caching middleware uses.  The cache
    key is ``sha256(f"{auth_key}:{uri}")`` — the auth partition prevents
    per-token response filtering from leaking across users.

    Args:
        uri: The resource URI
        auth_key: Auth partition key.  If ``None``, the current request's
            auth key is fetched via ``_get_auth_partition_key()`` (which
            returns ``__anonymous__`` for STDIO / unauthenticated callers).

    Returns:
        Hex digest of SHA256 hash
    """
    if auth_key is None:
        auth_key = _get_auth_partition_key()
    return hashlib.sha256(f"{auth_key}:{uri}".encode()).hexdigest()


def _substitute_template(template: str, params: dict[str, Any]) -> str:
    """Substitute parameters into a URI template.

    Args:
        template: URI template with {placeholders}
        params: Dictionary of parameter values

    Returns:
        URI with placeholders replaced

    Raises:
        ValueError: If required parameters are missing
    """
    # Find all parameter names in the template (handle {param} and {param*})
    param_names = re.findall(r"\{(\w+)(?:\*)?\}", template)

    # Check for missing required parameters
    missing = [p for p in param_names if p not in params]
    if missing:
        msg = f"Missing parameters for URI template: {missing}"
        raise ValueError(msg)

    # Replace each parameter
    result = template
    for param in param_names:
        # Check if the template uses wildcard syntax {param*}
        placeholder_with_asterisk = f"{{{param}*}}"
        placeholder_standard = f"{{{param}}}"

        if placeholder_with_asterisk in template:
            value = str(params.get(param, ""))
            result = result.replace(placeholder_with_asterisk, value)
        elif placeholder_standard in template:
            value = str(params.get(param, ""))
            result = result.replace(placeholder_standard, value)

    return result


def compute_uris_to_invalidate(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    """Compute the list of concrete resource URIs to invalidate for a tool call.

    Lookup tries the exact name first, then strips the ``gitea_`` namespace
    prefix if present — the map is keyed by bare ``operationId`` while the
    middleware receives the namespaced name at runtime.

    Args:
        tool_name: Name of the tool being called
        arguments: Arguments passed to the tool

    Returns:
        List of concrete resource URIs to invalidate
    """
    if tool_name not in TOOL_INVALIDATION_MAP:
        if tool_name.startswith(_GITEA_TOOL_PREFIX):
            stripped = tool_name[len(_GITEA_TOOL_PREFIX):]
            if stripped in TOOL_INVALIDATION_MAP:
                tool_name = stripped
            else:
                return []
        else:
            return []

    pattern_names = TOOL_INVALIDATION_MAP[tool_name]
    uris = []

    for pattern_name in pattern_names:
        if pattern_name not in RESOURCE_URI_PATTERNS:
            logger.warning(
                "Unknown resource pattern: %s (referenced by tool %s)",
                pattern_name,
                tool_name,
            )
            continue

        template = RESOURCE_URI_PATTERNS[pattern_name]
        try:
            uri = _substitute_template(template, arguments)
            uris.append(uri)
        except ValueError as e:
            logger.debug(
                "Skipping invalidation for pattern %s: %s",
                pattern_name,
                e,
            )

    return uris


async def invalidate_cached_resources(
    caching_middleware: ResponseCachingMiddleware, uris: list[str], tool_name: str = ""
) -> None:
    """Invalidate cached resource responses for the given URIs.

    Args:
        caching_middleware: The ResponseCachingMiddleware instance
        uris: List of resource URIs to invalidate
        tool_name: Optional tool name for logging
    """
    if not uris:
        return

    # FastMCP does not expose a public API for cache invalidation yet.
    # Access _read_resource_cache with graceful degradation in case the
    # private attribute changes in a future FastMCP version.
    cache_adapter = getattr(caching_middleware, "_read_resource_cache", None)
    if cache_adapter is None:
        logger.warning(
            "Cache invalidation unavailable: "
            "ResponseCachingMiddleware._read_resource_cache not found. "
            "This may be caused by a FastMCP version upgrade."
        )
        return

    deleted_count = 0

    for uri in uris:
        cache_key = _compute_cache_key(uri)
        try:
            existing = await cache_adapter.get(key=cache_key)
            if existing is not None:
                await cache_adapter.delete(key=cache_key)
                deleted_count += 1
                logger.debug(
                    "Invalidated cached resource: uri=%s, cache_key=%s, tool=%s",
                    uri,
                    cache_key[:16],
                    tool_name,
                )
        except (KeyError, ValueError) as e:
            logger.warning(
                "Failed to invalidate cache for URI %s: %s",
                uri,
                e,
            )

    if deleted_count > 0:
        logger.info(
            "Cache invalidation: removed %d cached resource(s) for tool %s",
            deleted_count,
            tool_name,
        )


class CacheInvalidationMiddleware(Middleware):
    """Middleware that invalidates cached resources after write operations.

    This middleware intercepts tool calls and, after successful execution,
    invalidates any cached resources that may have been affected by the write.
    It uses the global TOOL_INVALIDATION_MAP to determine which resources
    to clear based on the tool name and arguments.

    The middleware must be added AFTER the ResponseCachingMiddleware so that
    it can access and modify the cache.
    """

    def __init__(self, caching_middleware: ResponseCachingMiddleware):
        """Initialize with a reference to the caching middleware.

        Args:
            caching_middleware: The ResponseCachingMiddleware instance whose
                               cache should be invalidated
        """
        self.caching_middleware = caching_middleware

    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
        call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls to invalidate cache after successful writes.

        Args:
            context: The call context with tool name and arguments
            call_next: The next middleware/tool in the chain

        Returns:
            The tool result
        """
        tool_name = context.message.name
        arguments = context.message.arguments or {}

        # Execute the tool
        result = await call_next(context)

        # NOTE: use getattr for backward compat with fastmcp <3.4.0
        # where ToolResult does not have an is_error attribute.
        if result and not getattr(result, "is_error", False):
            uris_to_invalidate = compute_uris_to_invalidate(tool_name, arguments)
            if uris_to_invalidate:
                await invalidate_cached_resources(
                    self.caching_middleware, uris_to_invalidate, tool_name
                )

        return result
