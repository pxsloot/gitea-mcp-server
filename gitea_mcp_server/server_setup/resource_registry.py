"""Resource registration utilities."""

from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.resource_registry import ResourceRegistry


def register_all_resources(
    mcp: FastMCP, gitea_client: GiteaClient, openapi_spec: dict[str, Any]
) -> ResourceRegistry:
    """Register all MCP resources (auto-generated and custom) and resource tools.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for API calls
        openapi_spec: The OpenAPI specification dictionary

    Returns:
        ResourceRegistry containing metadata about all registered resources
    """
    # Import here to allow mocking
    from gitea_mcp_server.mcp_tools import register_mcp_resource_tools
    from gitea_mcp_server.resources import (
        register_auto_generated_resources,
        register_custom_resources,
    )

    # Create registry catalog
    registry = ResourceRegistry()

    # Register resources and record them in the catalog
    register_auto_generated_resources(mcp, gitea_client, openapi_spec, registry)
    register_custom_resources(mcp, gitea_client, registry)
    register_mcp_resource_tools(mcp)

    return registry


__all__ = [
    "register_all_resources",
]
