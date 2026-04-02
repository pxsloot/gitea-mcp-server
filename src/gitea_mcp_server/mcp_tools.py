"""MCP Resource Access Tools.

These tools allow agents to discover and read MCP resources that are registered
with the server. They bridge the gap between the resource protocol and the agent's
toolset.

Tool list:
- mcp_list_resources: List all available MCP resources
- mcp_read_resource: Read a resource by its URI
"""

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

logger = logging.getLogger(__name__)


async def _mcp_list_resources_impl(ctx: Context) -> dict[str, Any]:
    """Implementation of mcp_list_resources.

    Uses FastMCP Context to list registered resources and templates.

    Args:
        ctx: FastMCP Context object (injected automatically)

    Returns:
        Dictionary with 'resources' key and 'count' key
    """
    resources_list = []

    # Get all resources via Context
    mcp_resources = await ctx.list_resources()

    for resource in mcp_resources:
        resource_info = {
            "uri": str(resource.uri),
            "name": resource.name,
            "description": resource.description or "",
            "mimeType": resource.mime_type,
        }
        resources_list.append(resource_info)

    return {"resources": resources_list, "count": len(resources_list)}


async def _mcp_read_resource_impl(ctx: Context, uri: str) -> str:
    """Implementation of mcp_read_resource.

    Args:
        ctx: FastMCP Context object (injected automatically)
        uri: The resource URI to read

    Returns:
        The resource content as a string

    Raises:
        ValueError: If the resource is not found or cannot be read
    """
    try:
        # ctx.read_resource returns a list of ReadResourceContents objects
        contents = await ctx.read_resource(uri)

        if not contents:
            msg = f"Resource '{uri}' returned no content"
            raise ValueError(msg)

        # Return the first content part's text (most resources have single part)
        return contents[0].content
    except Exception as e:
        logger.exception("Failed to read resource %s", uri)
        msg = f"Error reading resource '{uri}': {type(e).__name__}: {e}"
        raise ValueError(msg) from e


def register_mcp_resource_tools(mcp: FastMCP) -> None:
    """Register MCP resource access tools with the server.

    These tools allow agents to interact with the MCP resource system directly.

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    async def mcp_list_resources(ctx: Context = CurrentContext()) -> dict[str, Any]:
        """List all available MCP resources.

        Returns a list of resource URIs and their metadata (name, description,
        MIME type if available). Agents can use this to discover what resources
        are accessible.

        Returns:
            Dictionary with 'resources' key containing a list of resource info:
            [
                {
                    "uri": "gitea://repos/{owner}/{repo}",
                    "name": "Repository",
                    "description": "Get full repository metadata",
                    "mimeType": "text/markdown"
                },
                ...
            ]
        """
        return await _mcp_list_resources_impl(ctx)

    @mcp.tool()
    async def mcp_read_resource(uri: str, ctx: Context = CurrentContext()) -> str:
        """Read the content of an MCP resource by URI.

        Fetches the resource from the server's resource registry and returns its
        content as a string. Works with both static resources and parameterized
        resource templates.

        Args:
            uri: The resource URI to read (e.g., "gitea://repos/mcp-server/gitea-mcp-server/readme")

        Returns:
            The resource content as a string. May be plain text, markdown, JSON, etc.

        Raises:
            ValueError: If the resource is not found or cannot be read
        """
        return await _mcp_read_resource_impl(ctx, uri)

    logger.info("Registered MCP resource tools: mcp_list_resources, mcp_read_resource")
