"""MCP server builder utilities.

This module provides functions to assemble the FastMCP server from OpenAPI spec,
including OpenAPI provider creation with customized component handling.
"""

import logging
from typing import Any

from fastmcp.server.providers.openapi import OpenAPIProvider

from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.tool_annotator import customize_component

logger = logging.getLogger(__name__)


def create_openapi_provider(
    openapi_spec: dict[str, Any],
    client,
    label_manager: LabelManager,
) -> OpenAPIProvider:
    """Create OpenAPIProvider with customized component handling.

    Args:
        openapi_spec: The OpenAPI v3 specification dictionary
        client: httpx.AsyncClient instance for making API calls
        label_manager: LabelManager for label validation

    Returns:
        Configured OpenAPIProvider
    """
    # Create provider without component customization function
    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
    )

    # Post-process tools: apply customizations to each OpenAPITool
    for name, tool in list(provider._tools.items()):
        # Each tool is an OpenAPITool with _route attribute containing the HTTPRoute
        route = getattr(tool, "_route", None)
        if route is not None:
            new_tool = customize_component(route, tool, label_manager)
            if new_tool is not None:
                provider._tools[name] = new_tool

    return provider


__all__ = [
    "create_openapi_provider",
]
