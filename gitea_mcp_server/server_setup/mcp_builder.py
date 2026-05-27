"""MCP server builder utilities.

This module provides functions to assemble the FastMCP server from OpenAPI spec,
including OpenAPI provider creation with customized component handling.
"""

import logging
from typing import TYPE_CHECKING, Any, cast

from fastmcp.server.providers.openapi import OpenAPIProvider, OpenAPITool

from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.tool_annotator import customize_component

if TYPE_CHECKING:
    from httpx import AsyncClient

    from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)


def create_openapi_provider(
    openapi_spec: dict[str, Any],
    client: "AsyncClient",
    label_manager: LabelManager,
    gitea_client: "GiteaClient | None" = None,
) -> OpenAPIProvider:
    """Create OpenAPIProvider with customized component handling.

    Args:
        openapi_spec: The OpenAPI v3 specification dictionary
        client: httpx.AsyncClient instance for making API calls
        label_manager: LabelManager for label validation
        gitea_client: Optional GiteaClient for label name resolution

    Returns:
        Configured OpenAPIProvider
    """
    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
    )

    for name, tool in list(provider._tools.items()):
        route = getattr(tool, "_route", None)
        if route is not None:
            new_tool = customize_component(
                route, tool, label_manager, openapi_spec, gitea_client=gitea_client
            )
            if new_tool is not None:
                provider._tools[name] = cast("OpenAPITool", new_tool)

    return provider


__all__ = [
    "create_openapi_provider",
]
