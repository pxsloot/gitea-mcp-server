"""Tool permission filtering for Gitea MCP Server."""

import logging
from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)


def _is_admin_tool(tool: Any) -> bool:
    """Check if a tool requires admin privileges.

    Args:
        tool: Tool object (expected to have 'tags' attribute)

    Returns:
        True if tool has 'admin' tag, False otherwise
    """
    return bool(hasattr(tool, "tags") and tool.tags and "admin" in tool.tags)


async def filter_tools_by_permissions(mcp: FastMCP, gitea_client: GiteaClient) -> None:
    """Filter tools based on the current user's permissions.

    Removes tools that the user does not have permission to use.
    This function should be called before any list_tools request to avoid
    caching of unfiltered tools.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for making API calls
    """
    logger.info("Starting tool permission filtering")

    # Fetch current user info to check admin status
    try:
        user_data = await gitea_client.request("GET", "/user")
        # gitea_client.request returns parsed JSON directly (dict)
        if not isinstance(user_data, dict):
            raise ValueError(f"Unexpected user data type: {type(user_data)}")
        is_admin = user_data.get("admin", False)
        username = user_data.get("login", "unknown")
        logger.info(
            "User info retrieved",
            extra={"username": username, "is_admin": is_admin},
        )
    except Exception as e:  # noqa: BLE001
        # On error, log and keep all tools (optimistic)
        logger.warning(
            "Failed to fetch user info for filtering, keeping all tools",
            extra={"error": str(e)},
        )
        return

    # Gather tools directly from each provider without going through the server's
    # list_tools (which may be cached or include middleware). This ensures we
    # modify the original tool objects before any caching occurs.
    all_tools = []
    for provider in getattr(mcp, "providers", []):
        try:
            # Use provider's list_tools to get the tools (returns actual tool objects)
            provider_tools = await provider.list_tools()
            all_tools.extend(provider_tools)
        except Exception as e:
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

    # Identify tools to disable based on permission requirements
    tools_to_disable = []

    for tool in all_tools:
        # Check if tool requires admin privileges via tags (set by _categorize_tool)
        if _is_admin_tool(tool) and not is_admin:
            tools_to_disable.append(tool)
            logger.debug(
                "Marking admin tool for disabling",
                extra={"tool": tool.name, "reason": "non_admin_user", "key": tool.key},
            )

        # (Optional) Wiki tools - check if wiki feature is available
        # For now, we don't filter wiki tools; can be extended later
        # elif tool.name.startswith("wiki_"):
        #     if not await _is_wiki_available(gitea_client):
        #         tools_to_disable.append(tool)

    # Directly mark tools as disabled by setting metadata
    # This ensures that when tools are later listed, they appear disabled
    disabled_count = 0
    for tool in tools_to_disable:
        try:
            # Ensure tool metadata has visibility=False
            if tool.meta is None:
                tool.meta = {}
            if "fastmcp" not in tool.meta:
                tool.meta["fastmcp"] = {}
            if "_internal" not in tool.meta["fastmcp"]:
                tool.meta["fastmcp"]["_internal"] = {}
            tool.meta["fastmcp"]["_internal"]["visibility"] = False
            disabled_count += 1
            logger.info(
                "Disabled tool due to insufficient permissions",
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
