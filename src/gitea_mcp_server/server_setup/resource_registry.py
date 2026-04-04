"""Resource registration orchestrator."""

from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.resource_registry import ResourceRegistry
from gitea_mcp_server.resources import register_auto_generated_resources, register_custom_resources


def register_all_resources(
    mcp: FastMCP, gitea_client: GiteaClient, openapi_spec: dict[str, Any]
) -> None:
    """Register all MCP resources (auto-generated and custom) with the MCP server.

    Resources are registered via a ResourceRegistry, which allows centralized
    management and avoids direct dependency on FastMCP during registration.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for API calls
        openapi_spec: The OpenAPI specification dictionary
    """
    registry = ResourceRegistry()
    register_auto_generated_resources(registry, gitea_client, openapi_spec)
    register_custom_resources(registry, gitea_client)
    registry.apply_to(mcp)
