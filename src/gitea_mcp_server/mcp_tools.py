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

logger = logging.getLogger(__name__)


async def _mcp_list_resources_impl(mcp: FastMCP) -> dict[str, Any]:
    """Implementation of mcp_list_resources.

    This is separated to allow testing without the FastMCP decorator.

    Args:
        mcp: The FastMCP server instance

    Returns:
        Dictionary with 'resources' key and 'count' key
    """
    resources_list = []

    # Access the internal resource registry
    if hasattr(mcp, "_resources"):
        for uri_template, resource in mcp._resources.items():
            resource_info = {
                "uri": uri_template,
                "name": getattr(resource, "name", uri_template),
                "description": getattr(resource, "description", ""),
                "mimeType": getattr(resource, "mime_type", None),
            }
            resources_list.append(resource_info)

    # Also check resource templates (parameterized URIs)
    if hasattr(mcp, "_resource_templates"):
        for uri_template, template in mcp._resource_templates.items():
            template_info = {
                "uri": uri_template,
                "name": getattr(template, "name", uri_template),
                "description": getattr(template, "description", ""),
                "mimeType": getattr(template, "mime_type", None),
                "isTemplate": True,
            }
            resources_list.append(template_info)

    return {"resources": resources_list, "count": len(resources_list)}


async def _mcp_read_resource_impl(mcp: FastMCP, uri: str) -> str:
    """Implementation of mcp_read_resource.

    Args:
        mcp: The FastMCP server instance
        uri: The resource URI to read

    Returns:
        The resource content as a string

    Raises:
        ValueError: If the resource is not found or cannot be read
    """
    # If FastMCP's read_resource is available, use it (preferred path)
    if hasattr(mcp, "read_resource"):
        try:
            content, mime_type = await mcp.read_resource(uri)
        except Exception as e:
            logger.exception("Failed to read resource %s via FastMCP", uri)
            msg = f"Error reading resource '{uri}': {type(e).__name__}: {e}"
            raise ValueError(msg) from e
        else:
            logger.debug("Read resource %s (mime: %s)", uri, mime_type)
            return content  # type: ignore[no-any-return]

    # Fallback: direct lookup in registries (for testing without FastMCP)
    logger.warning("FastMCP.read_resource not available, attempting direct lookup")

    # Note: _resources and _resource_templates are internal FastMCP attributes
    if uri in mcp._resources:  # type: ignore[attr-defined]
        try:
            resource_func = mcp._resources[uri]  # type: ignore[attr-defined]
            result = await resource_func()
        except Exception as e:
            logger.exception("Failed to execute resource function for %s", uri)
            msg = f"Error reading resource '{uri}': {type(e).__name__}: {e}"
            raise ValueError(msg) from e
        else:
            return str(result)

    if uri in mcp._resource_templates:  # type: ignore[attr-defined]
        # Templates need to be matched and called with parsed parameters
        msg = "Resource template requires parameter parsing; use read_resource() directly"
        raise ValueError(msg)

    msg = f"Resource not found: {uri}"
    raise ValueError(msg)


def register_mcp_resource_tools(mcp: FastMCP) -> None:
    """Register MCP resource access tools with the server.

    These tools allow agents to interact with the MCP resource system directly.

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    async def mcp_list_resources() -> dict[str, Any]:
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
        return await _mcp_list_resources_impl(mcp)

    @mcp.tool()
    async def mcp_read_resource(uri: str) -> str:
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
        return await _mcp_read_resource_impl(mcp, uri)

    logger.info("Registered MCP resource tools: mcp_list_resources, mcp_read_resource")
