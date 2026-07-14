"""Resource registration utilities."""

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.mcp_tools import register_mcp_resource_tools
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources import register_auto_generated_resources, register_custom_resources


def register_all_resources(
    mcp: FastMCP, gitea_client: GiteaClient, openapi_spec: OpenAPISpec
) -> None:
    """Register all MCP resources (auto-generated and custom) and resource tools.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for API calls
        openapi_spec: The OpenAPI specification dictionary
    """
    register_auto_generated_resources(mcp, gitea_client, openapi_spec)
    register_custom_resources(mcp, gitea_client, openapi_spec)
    register_mcp_resource_tools(mcp, openapi_spec=openapi_spec)


__all__ = [
    "register_all_resources",
]
