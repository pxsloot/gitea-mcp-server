"""Auto-generated resources from OpenAPI GET endpoints.

Creates resources for all GET operations, returning raw JSON.
These can be overridden by custom resources with the same URI.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    AUTO_GENERATED_RESOURCE_SKIP_URIS,
    HTTP_STATUS_NOT_FOUND,
)
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.scope import derive_required_scope, scope_meta

logger = logging.getLogger(__name__)


def _derive_resource_name(operation: dict[str, Any], path: str) -> str:
    """Derive a meaningful resource name from an OpenAPI operation."""
    operation_id = operation.get("operationId")
    if operation_id and operation_id.strip():
        name = operation_id.strip()
        result = ""
        for i, char in enumerate(name):
            if char.isupper():
                if i > 0 and (
                    name[i - 1].islower() or (i + 1 < len(name) and name[i + 1].islower())
                ):
                    result += "_"
                result += char.lower()
            else:
                result += char
        return result

    clean_path = path.strip("/")
    segments = [s for s in clean_path.split("/") if not (s.startswith("{") and s.endswith("}"))]
    if not segments:
        segments = [s.strip("{}") for s in clean_path.split("/") if s]
    return "_".join(segments) if segments else "resource"


def _make_resource_func(
    path: str,
    method: str,
    operation: dict[str, Any],
    gitea_client: GiteaClient,
    resource_name: str | None = None,
) -> Callable[..., Awaitable[str]]:
    """Create a resource function for a given OpenAPI operation."""
    path_params = []
    if "parameters" in operation:
        for param in operation["parameters"]:
            if param["in"] == "path":
                path_params.append(param["name"])

    query_params = []
    if "parameters" in operation:
        for param in operation["parameters"]:
            if param["in"] == "query":
                query_params.append(param["name"])

    async def resource_func(**kwargs: Any) -> str:
        """Auto-generated resource from OpenAPI spec."""
        formatted_path = path
        missing_params = [p for p in path_params if p not in kwargs]
        if missing_params:
            raise ResourceError(
                {
                    "code": "VALIDATION_ERROR",
                    "message": f"Missing required path parameter(s): {', '.join(missing_params)}",
                    "detail": "The resource requires path parameters that were not provided.",
                    "resource_type": "api",
                    "resource_id": formatted_path,
                }
            )
        for param in path_params:
            formatted_path = formatted_path.replace(f"{{{param}}}", str(kwargs[param]))

        query = {p: kwargs[p] for p in query_params if p in kwargs}

        try:
            response = await gitea_client.request(
                method, formatted_path, params=query if query else None
            )
            return json.dumps(response, indent=2)
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status == HTTP_STATUS_NOT_FOUND:
                raise ResourceError(
                    {
                        "code": "NOT_FOUND",
                        "message": f"Resource not found: {formatted_path}",
                        "detail": str(e),
                        "resource_type": "api",
                        "resource_id": formatted_path,
                    }
                ) from e
            if status:
                raise ResourceError(
                    {
                        "code": "API_ERROR",
                        "message": f"API error {status} for {formatted_path}",
                        "detail": str(e),
                        "resource_type": "api",
                        "resource_id": formatted_path,
                    }
                ) from e
            raise ResourceError(
                {
                    "code": "INTERNAL_ERROR",
                    "message": f"Unexpected error fetching resource: {formatted_path}",
                    "detail": str(e),
                    "resource_type": "api",
                    "resource_id": formatted_path,
                }
            ) from e

    summary = operation.get("summary", "")
    description = operation.get("description", "")
    docstring = summary
    if description:
        docstring += "\n\n" + description
    if not docstring:
        docstring = f"Resource for {method.upper()} {path}"
    resource_func.__doc__ = docstring

    if resource_name:
        resource_func.__name__ = resource_name

    return resource_func


def register_auto_generated_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec,
    skip_uris: set[str] | None = None,
) -> None:
    """Auto-generate resources from GET endpoints in OpenAPI spec."""
    if skip_uris is None:
        skip_uris = AUTO_GENERATED_RESOURCE_SKIP_URIS

    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    count = 0
    for path, path_item in paths.items():
        for method in ["get", "GET"]:
            if method in path_item:
                operation = cast("dict[str, Any]", path_item[method])

                if "{" not in path:
                    logger.debug(
                        "Skipping auto-generated resource for %s: no path parameters in template",
                        path,
                    )
                    continue

                uri_template = f"gitea://{path.lstrip('/')}"

                if uri_template in skip_uris:
                    logger.debug(
                        "Skipping auto-generated resource %s: will be provided by custom resource",
                        uri_template,
                    )
                    continue

                resource_name = _derive_resource_name(operation, path)

                swagger_tags = set(operation.get("tags", [])) or None
                required_scope = derive_required_scope(swagger_tags, "GET")

                resource_func = _make_resource_func(
                    path,
                    method.upper(),
                    operation,
                    gitea_client,
                    resource_name=resource_name,
                )

                resource_meta = scope_meta(required_scope)

                try:
                    mcp.resource(
                        uri_template,
                        name=resource_name,
                        mime_type="application/json",
                        tags={"api", "raw", "auto"},
                        meta=resource_meta,
                    )(resource_func)
                    count += 1
                    logger.debug("Registered auto-generated resource: %s", uri_template)
                except ValueError as e:
                    logger.warning(
                        "Skipping auto-generated resource %s: %s",
                        uri_template,
                        e,
                    )
                    continue

    logger.info("Auto-generated %d resources from OpenAPI spec", count)


__all__ = [
    "_derive_resource_name",
    "_make_resource_func",
    "register_auto_generated_resources",
]
