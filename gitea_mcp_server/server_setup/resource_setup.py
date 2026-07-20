"""Resource registration utilities."""

from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.mcp_tools import register_mcp_resource_tools
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources import register_auto_generated_resources, register_custom_resources


def register_all_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec,
    filtered_tools_info: dict[str, Any] | None = None,
    available_scopes: set[str] | None = None,
) -> None:
    """Register all MCP resources (auto-generated and custom) and resource tools.

    Auto-generated resources are filtered by ``filtered_tools_info`` (the same
    spec-level data used for tool filtering) — resources whose operationId is
    scope-filtered, deprecated, or config-excluded are skipped.

    Custom resources are filtered by ``available_scopes`` — they declare their
    own ``required_scope`` via ``scope_meta()`` and are skipped when the token
    lacks that scope.

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: The OpenAPI specification dictionary.
        filtered_tools_info: Filter-prediction data from spec-level filtering.
            ``None`` means no filtering (all auto resources visible).
        available_scopes: Set of scopes the token has, or ``None`` (no filtering).
            Custom resources whose required scope is not satisfied are skipped.
    """
    register_auto_generated_resources(
        mcp,
        gitea_client,
        openapi_spec,
        filtered_tools_info=filtered_tools_info,
    )
    register_custom_resources(
        mcp,
        gitea_client,
        openapi_spec,
        available_scopes=available_scopes,
    )
    register_mcp_resource_tools(mcp, openapi_spec=openapi_spec)


__all__ = [
    "register_all_resources",
]
