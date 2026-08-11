"""Resource registration orchestration.

Provides ``register_all_resources()`` — the single entry point that
orchestrates resource setup in registration order: MCP access tools
(list_resources, read_resource), custom wrappers (registered first so
auto-generation skips them), then auto-generated resources from every
GET endpoint.  Scope and config filtering are applied at registration
time.
"""

from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources import register_auto_generated_resources, register_custom_resources
from gitea_mcp_server.tools.mcp_tools import register_mcp_resource_tools


def register_all_resources(  # noqa: PLR0913 — mcp + client + spec + filter + scopes + pre-computed data are all independent registration axes
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec,
    filtered_tools_info: dict[str, Any] | None = None,
    available_scopes: set[str] | None = None,
    version_str: str = "Unknown",
    server_info_md: str | None = None,
) -> None:
    """Register all MCP resources (auto-generated and custom) and resource tools.

    Custom resources are registered first via ``register_custom_resources()``,
    which returns the set of URIs registered via ``make_api_resource()``.
    Auto-generated resources are then registered with that returned set as
    ``skip_uris`` — avoiding duplicate resource registrations.

    Auto-generated resources are filtered by ``filtered_tools_info`` (the same
    spec-level data used for tool filtering) — resources whose operationId is
    scope-filtered, deprecated, or config-excluded are skipped.

    Custom resources are filtered by ``available_scopes`` — they declare their
    own ``required_scope`` via ``ResourceMeta`` and are skipped when the
    token lacks that scope.

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: The OpenAPI specification dictionary.
        filtered_tools_info: Filter-prediction data from spec-level filtering.
            ``None`` means no filtering (all auto resources visible).
        available_scopes: Set of scopes the token has, or ``None`` (no filtering).
            Custom resources whose required scope is not satisfied are skipped.
        version_str: Pre-fetched server version string.
        server_info_md: Pre-built server info markdown, or ``None``.
    """
    # Custom first: populates _registered_uris at registration time
    # via make_api_resource().  Returns the set of registered URIs
    # so auto-generation can skip them.
    skip_uris = register_custom_resources(
        mcp,
        gitea_client,
        openapi_spec,
        available_scopes=available_scopes,
        version_str=version_str,
        server_info_md=server_info_md,
    )

    # Auto second: skip URIs already claimed by factory resources.
    # Pass a copy — the caller must not mutate the returned set.
    register_auto_generated_resources(
        mcp,
        gitea_client,
        openapi_spec,
        skip_uris=set(skip_uris),
        filtered_tools_info=filtered_tools_info,
    )
    register_mcp_resource_tools(mcp, openapi_spec=openapi_spec)


__all__ = [
    "register_all_resources",
]
