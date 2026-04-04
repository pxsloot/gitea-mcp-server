"""Resource registration utilities."""

from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient


def register_all_resources(
    mcp: FastMCP, gitea_client: GiteaClient, openapi_spec: dict[str, Any]
) -> None:
    """Register all MCP resources (auto-generated and custom) and resource tools.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for API calls
        openapi_spec: The OpenAPI specification dictionary
    """
    # Import here to allow mocking
    from gitea_mcp_server.resources import (
        register_auto_generated_resources,
        register_custom_resources,
    )
    from gitea_mcp_server.mcp_tools import register_mcp_resource_tools

    register_auto_generated_resources(mcp, gitea_client, openapi_spec)
    register_custom_resources(mcp, gitea_client)
    register_mcp_resource_tools(mcp)


__all__ = [
    "register_all_resources",
]
