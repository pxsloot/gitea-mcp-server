"""MCP server builder utilities.

This module provides functions to assemble the FastMCP server from OpenAPI spec,
including OpenAPI provider creation with customized component handling.
"""

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.providers.openapi import OpenAPIProvider

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    CACHE_MAX_ITEM_SIZE,
    CACHE_TTL_DEFAULT,
    CACHE_TTL_RESOURCE_LIST,
)
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.tool_annotator import customize_component

logger = logging.getLogger(__name__)


def make_customize_fn(label_manager: LabelManager):
    """Create a component customization function that captures the label_manager.

    Args:
        label_manager: LabelManager instance for label validation

    Returns:
        A function with signature (route, component) suitable for OpenAPIProvider's mcp_component_fn.
    """

    def customize(route: Any, component: Any) -> None:
        return customize_component(route, component, label_manager)

    return customize


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
    customize_fn = make_customize_fn(label_manager)
    return OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
        mcp_component_fn=customize_fn,
    )


__all__ = [
    "make_customize_fn",
    "create_openapi_provider",
]
