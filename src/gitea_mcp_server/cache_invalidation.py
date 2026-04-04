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
from typing import Any

import mcp
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools.tool import ToolResult

logger = logging.getLogger(__name__)

# Resource URI templates used to construct concrete URIs for invalidation.
# Keys are pattern names (arbitrary labels), values are URI templates with
# placeholders that match the tool argument names.
RESOURCE_URI_PATTERNS: dict[str, str] = {
    # Issues
    "issues_list": "gitea://repos/{owner}/{repo}/issues",
    "issues_open": "gitea://repos/{owner}/{repo}/issues/open",
    "issues_closed": "gitea://repos/{owner}/{repo}/issues/closed",
    # Pull requests
    "pulls_list": "gitea://repos/{owner}/{repo}/pulls",
    "pulls_open": "gitea://repos/{owner}/{repo}/pulls/open",
    "pulls_closed": "gitea://repos/{owner}/{repo}/pulls/closed",
    # Repository
    "repo": "gitea://repos/{owner}/{repo}",
    # File contents (using filepath as parameter name to match Gitea API)
    "files": "gitea://repos/{owner}/{repo}/files/{filepath}",
}

# Global invalidation map populated at server startup.
# Maps tool name -> list of pattern names (keys in RESOURCE_URI_PATTERNS)
TOOL_INVALIDATION_MAP: dict[str, list[str]] = {}


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


def _compute_cache_key(uri: str) -> str:
    """Compute the cache key for a resource URI using SHA256.

    This mirrors FastMCP's _hash_cache_key function to ensure we compute
    the exact same key that the caching middleware uses.

    Args:
        uri: The resource URI

    Returns:
        Hex digest of SHA256 hash
    """
    return hashlib.sha256(uri.encode()).hexdigest()


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
        raise ValueError(f"Missing parameters for URI template: {missing}")

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

    Args:
        tool_name: Name of the tool being called
        arguments: Arguments passed to the tool

    Returns:
        List of concrete resource URIs to invalidate
    """
    if tool_name not in TOOL_INVALIDATION_MAP:
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

    cache_adapter = caching_middleware._read_resource_cache
    deleted_count = 0

    for uri in uris:
        cache_key = _compute_cache_key(uri)
        try:
            # Check if key exists before deleting (to be safe)
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
        except Exception as e:
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

        # If successful, invalidate affected resources
        # ToolResult with no error means success
        if result and not getattr(result, "is_error", False):
            uris_to_invalidate = compute_uris_to_invalidate(tool_name, arguments)
            if uris_to_invalidate:
                await invalidate_cached_resources(
                    self.caching_middleware, uris_to_invalidate, tool_name
                )

        return result
