"""Tool permission filtering for Gitea MCP Server."""

import logging
from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)


def _extract_tool_names(tools: Any) -> list[str]:
    """Extract tool names from mcp.get_tools() return value.

    Args:
        tools: The result from mcp.get_tools(), can be dict or list

    Returns:
        List of tool names as strings
    """
    if isinstance(tools, dict):
        return list(tools.keys())

    if isinstance(tools, list):
        tool_names = []
        for tool in tools:
            if hasattr(tool, "name"):
                tool_names.append(tool.name)
            elif isinstance(tool, str):
                tool_names.append(tool)
            elif isinstance(tool, dict):
                name = tool.get("name")
                if name:
                    tool_names.append(name)
        return tool_names

    return []


async def filter_tools_by_permissions(mcp: FastMCP, gitea_client: GiteaClient) -> None:
    """Filter tools based on the current user's permissions.

    Removes tools that the user does not have permission to use.

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

    # Get all tools
    tools = await mcp.get_tools()
    tool_names = _extract_tool_names(tools)

    if not tool_names:
        logger.warning("No tools found to filter")
        return

    logger.debug(
        "Tools before filtering",
        extra={"total_tools": len(tool_names), "tools": tool_names[:20]},
    )

    # Identify tools to remove based on permission requirements
    tools_to_remove = []

    for tool_name in tool_names:
        # Check if tool requires admin privileges (admin* operationIds)
        if tool_name.startswith("admin") and not is_admin:
            tools_to_remove.append(tool_name)
            logger.debug(
                "Marking admin tool for removal",
                extra={"tool": tool_name, "reason": "non_admin_user"},
            )

        # (Optional) Wiki tools - check if wiki feature is available
        # For now, we don't filter wiki tools; can be extended later
        # elif tool_name.startswith("wiki_"):
        #     if not await _is_wiki_available(gitea_client):
        #         tools_to_remove.append(tool_name)

    # Remove filtered tools
    removed_count = 0
    for tool_name in tools_to_remove:
        try:
            mcp.remove_tool(tool_name)
            removed_count += 1
            logger.info(
                "Removed tool due to insufficient permissions",
                extra={"tool": tool_name},
            )
        except Exception as e:
            logger.exception(
                "Failed to remove tool",
                extra={"tool": tool_name, "error": str(e)},
            )

    logger.info(
        "Tool filtering completed",
        extra={
            "total_tools": len(tool_names),
            "removed_tools": removed_count,
            "remaining_tools": len(tool_names) - removed_count,
        },
    )
