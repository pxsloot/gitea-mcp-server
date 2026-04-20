"""MCP server builder utilities.

This module provides functions to assemble the FastMCP server from OpenAPI spec,
including OpenAPI provider creation with customized component handling.
"""

import logging
from typing import TYPE_CHECKING, Any

from fastmcp.server.providers.openapi import OpenAPIProvider

from gitea_mcp_server.config import Config
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.tool_annotator import customize_component

if TYPE_CHECKING:
    from httpx import AsyncClient

logger = logging.getLogger(__name__)


def create_openapi_provider(
    openapi_spec: dict[str, Any],
    client: "AsyncClient",
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
    config = Config.get()
    tool_prefix = config.tool_prefix

    # Create provider without component customization function
    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
    )

    # Post-process tools: apply customizations to each OpenAPITool
    #
    # NOTE: FastMCP does not expose a public API to customize tools after provider creation.
    # We must access provider._tools directly. This is fragile as FastMCP may change
    # internal structure between versions. Monitor gofastmcp.com for official
    # customization hooks. Alternative: file feature request with FastMCP.
    for name, tool in list(provider._tools.items()):
        # Each tool is an OpenAPITool with _route attribute containing the HTTPRoute
        # NOTE: FastMCP does not expose route information through a public property.
        # We access _route directly. This may break if FastMCP changes internals.
        route = getattr(tool, "_route", None)
        if route is not None:
            new_tool = customize_component(route, tool, label_manager, openapi_spec)
            if new_tool is not None:
                provider._tools[name] = new_tool  # type: ignore[assignment]

        # Apply tool prefix for MCP best practices
        if tool_prefix and not name.startswith(tool_prefix):
            prefixed_name = f"{tool_prefix}{name}"
            provider._tools[prefixed_name] = provider._tools.pop(name)

    return provider


__all__ = [
    "create_openapi_provider",
]
