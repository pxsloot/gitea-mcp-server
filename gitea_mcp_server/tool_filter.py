"""Tool permission filtering for Gitea MCP Server."""

import logging
from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)


def _validate_user_data(data: Any) -> None:
    """Validate user data is a dict."""
    if not isinstance(data, dict):
        msg = f"Unexpected user data type: {type(data)}"
        raise TypeError(msg) from None


def _get_required_scope(tool: Any) -> str | None:
    """Get the required Gitea token scope from a tool's metadata.

    Args:
        tool: Tool object with meta containing 'required_scope'.

    Returns:
        Scope string (e.g. "read:repository", "sudo"), or None if no scope needed.
    """
    try:
        return tool.meta["fastmcp"]["_internal"]["required_scope"]
    except (KeyError, TypeError, AttributeError):
        return None


def _has_sufficient_scope(required: str | None, available: set[str]) -> bool:
    """Check if available Gitea token scopes satisfy a required scope.

    Rules:
    - None required (no scope needed) always passes.
    - ``sudo`` in available grants everything.
    - Exact match passes.
    - ``write:xxx`` implies ``read:xxx``.

    Args:
        required: Required scope string or None.
        available: Set of scope strings the user's token possesses.

    Returns:
        True if the required scope is covered by available scopes.
    """
    if required is None:
        return True
    if "sudo" in available:
        return True
    if required in available:
        return True
    if required.startswith("read:"):
        resource = required.split(":", 1)[1]
        if f"write:{resource}" in available:
            return True
    return False


async def filter_tools_by_permissions(mcp: FastMCP, gitea_client: GiteaClient) -> None:
    """Filter tools based on the current user's Gitea token scopes.

    Removes tools that require a scope not present in the user's token(s).
    This function should be called before any list_tools request to avoid
    caching of unfiltered tools.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for making API calls
    """
    logger.info("Starting tool permission filtering")

    # Fetch current user info
    try:
        user_data = await gitea_client.request("GET", "/user")
        _validate_user_data(user_data)
        username = user_data.get("login", "unknown")
        logger.info("User info retrieved", extra={"username": username})
    except Exception as e:
        logger.exception(
            "Failed to fetch user info for filtering, keeping all tools",
            extra={"error": str(e)},
        )
        return

    # Fetch user's token scopes
    try:
        tokens_data = await gitea_client.request("GET", f"/users/{username}/tokens")
        if not isinstance(tokens_data, list):
            logger.warning(
                "Unexpected tokens response type, keeping all tools",
                extra={"type": type(tokens_data).__name__},
            )
            return
    except Exception as e:
        logger.exception(
            "Failed to fetch tokens for filtering, keeping all tools",
            extra={"error": str(e)},
        )
        return

    # Build set of available scopes (union across all tokens)
    available_scopes: set[str] = set()
    for token in tokens_data:
        scopes = token.get("scopes") if isinstance(token, dict) else None
        if scopes and isinstance(scopes, list):
            available_scopes.update(scopes)

    logger.info(
        "Token scopes retrieved",
        extra={"scopes": sorted(available_scopes)},
    )

    # Gather tools directly from each provider
    all_tools = []
    for provider in getattr(mcp, "providers", []):
        try:
            provider_tools = await provider.list_tools()
            all_tools.extend(provider_tools)
        except (AttributeError, TypeError) as e:
            logger.warning(
                "Failed to list tools from provider, skipping",
                extra={"provider": type(provider).__name__, "error": str(e)},
            )

    if not all_tools:
        logger.warning("No tools found in providers to filter")
        return

    logger.debug(
        "Tools before filtering",
        extra={"total_tools": len(all_tools), "tools": [t.name for t in all_tools][:20]},
    )

    # Identify tools to disable based on scope requirements
    tools_to_disable = []

    for tool in all_tools:
        required = _get_required_scope(tool)
        if not _has_sufficient_scope(required, available_scopes):
            tools_to_disable.append(tool)
            logger.debug(
                "Marking tool for disabling due to insufficient scope",
                extra={
                    "tool": tool.name,
                    "required_scope": required,
                    "available_scopes": sorted(available_scopes),
                    "key": tool.key,
                },
            )

    disabled_count = 0
    for tool in tools_to_disable:
        try:
            if tool.meta is None:
                tool.meta = {}
            if "fastmcp" not in tool.meta:
                tool.meta["fastmcp"] = {}
            if "_internal" not in tool.meta["fastmcp"]:
                tool.meta["fastmcp"]["_internal"] = {}
            tool.meta["fastmcp"]["_internal"]["visibility"] = False
            disabled_count += 1
            logger.info(
                "Disabled tool due to insufficient scope",
                extra={"tool": tool.name, "key": tool.key},
            )
        except Exception as e:
            logger.exception(
                "Failed to disable tool",
                extra={"tool": tool.name, "key": tool.key, "error": str(e)},
            )

    logger.info(
        "Tool filtering completed",
        extra={
            "total_tools": len(all_tools),
            "disabled_tools": disabled_count,
            "remaining_tools": len(all_tools) - disabled_count,
        },
    )
