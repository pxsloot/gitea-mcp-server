"""Auto-generated resources from OpenAPI GET endpoints.

Creates resources for all GET operations, returning raw JSON with schema
metadata.  These can be overridden by custom resources with the same URI.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.resources import ResourceContent, ResourceResult

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    AUTO_GENERATED_RESOURCE_SKIP_URIS,
    HTTP_STATUS_NOT_FOUND,
)
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.scope import derive_required_scope, scope_meta
from gitea_mcp_server.tools.schemas import _get_success_schema, _unwrap_result_schema

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


def _make_resource_func(  # noqa: PLR0913 - 6 params: path, method, operation, client, name, schema — all independently needed at registration time
    path: str,
    method: str,
    operation: dict[str, Any],
    gitea_client: GiteaClient,
    resource_name: str | None = None,
    response_schema: dict[str, Any] | None = None,
) -> Callable[..., Awaitable[ResourceResult]]:
    """Create a resource function for a given OpenAPI operation.

    The returned handler fetches data from the Gitea API and returns it as
    raw JSON with the response schema attached in content metadata.  No
    formatting is applied — that is the responsibility of the display layer
    (``_format_resource_content`` in ``mcp_tools.py``).

    Args:
        path: The API path template (e.g. ``/repos/{owner}/{repo}``).
        method: The HTTP method (``"GET"``).
        operation: The OpenAPI operation dict.
        gitea_client: Client for API calls.
        resource_name: Optional override for the resource function name.
        response_schema: The unresolved inner response schema (with ``$ref``
            intact, ``{result: ...}`` wrapper stripped) for ``$ref``-aware
            data collapse in the display layer.
    """
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

    async def resource_func(**kwargs: Any) -> ResourceResult:
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
            content = json.dumps(response, indent=2)
            meta: dict[str, Any] = {}
            if response_schema is not None:
                meta["response_schema"] = response_schema
            return ResourceResult(
                contents=[ResourceContent(
                    content=content,
                    mime_type="application/json",
                    meta=meta if meta else None,
                )]
            )
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
        docstring = f"Resource for {method} {path}"
    resource_func.__doc__ = docstring

    if resource_name:
        resource_func.__name__ = resource_name

    return resource_func


def register_auto_generated_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec,
    skip_uris: set[str] | None = None,
    filtered_tools_info: dict[str, Any] | None = None,
) -> None:
    """Auto-generate resources from GET endpoints in OpenAPI spec.

    Each resource returns raw JSON data with its response schema attached
    in ``ResourceContent.meta["response_schema"]`` for use by the display
    layer (``_format_resource_content``).

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: The OpenAPI specification dictionary.
        skip_uris: Set of URI templates to skip (custom resource overrides).
        filtered_tools_info: Filter-prediction data from spec-level filtering.
            When provided, resources whose operationId appears in the ``filtered``
            dict are skipped — they are scope-filtered, deprecated, or excluded by
            config.  ``None`` means no filtering is applied (all resources visible).
    """
    if skip_uris is None:
        skip_uris = AUTO_GENERATED_RESOURCE_SKIP_URIS

    filtered: dict[str, Any] = {}
    if filtered_tools_info:
        filtered = filtered_tools_info.get("filtered", {})

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

                # Spec-level filtering: skip if operationId is filtered
                # (scope-restricted, deprecated, or config-excluded).
                op_id: str = operation.get("operationId", "")
                if op_id and op_id in filtered:
                    reason = filtered[op_id].get("reason", "unknown")
                    logger.debug(
                        "Skipping auto-generated resource %s: filtered (%s)",
                        uri_template,
                        reason,
                    )
                    continue

                resource_name = _derive_resource_name(operation, path)

                # Derive the unresolved response schema for $ref-aware collapse.
                # Unwrap the result envelope ({result: inner}) so the stored
                # schema matches the raw API response shape — consumers of
                # meta["response_schema"] (like _format_resource_content) need
                # the inner schema for $ref-aware data collapse.
                response_schema = _unwrap_result_schema(
                    _get_success_schema(openapi_spec, path, "get", resolve=False),
                )

                swagger_tags = set(operation.get("tags", [])) or None
                required_scope = derive_required_scope(swagger_tags, "GET")

                resource_func = _make_resource_func(
                    path,
                    method.upper(),
                    operation,
                    gitea_client,
                    resource_name=resource_name,
                    response_schema=response_schema,
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
