"""Factory for creating custom resource handlers with auto schema derivation.

Provides ``make_api_resource()`` which generates and registers a resource
handler from a declarative description -- eliminating the 5-step boilerplate
pattern that was repeated across every custom resource.

The factory auto-derives the response schema from the OpenAPI spec via
the endpoint's ``api_path + method``, removing the need for manual
``_get_success_schema`` / ``_unwrap_result_schema`` calls.  Handlers
handle ``str`` vs JSON branching automatically.
"""

import json
import logging
import re
from collections.abc import Callable
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.resources import ResourceContent, ResourceResult

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.scope import has_sufficient_scope, scope_meta
from gitea_mcp_server.tools.schemas import _get_success_schema, _unwrap_result_schema

logger = logging.getLogger(__name__)

# Module-level set of URIs registered via make_api_resource.
# Populated at import time with all Phase 1 factory URIs so that
# ``register_auto_generated_resources()`` can read this set at call time
# (before ``make_api_resource`` actually registers the handlers) and
# skip auto-generation for these URIs.
#
# As more resources migrate to the factory in Phase 2/3, new URIs are
# added here (both at import time and at registration time via
# ``make_api_resource``'s ``_registered_uris.add(uri)``).
_registered_uris: set[str] = {
    "gitea://repos/{owner}/{repo}",
    "gitea://users/{username}",
    "gitea://user",
    "gitea://orgs/{orgname}",
    "gitea://repos/{owner}/{repo}/releases",
    "gitea://repos/{owner}/{repo}/labels",
}


def _auto_derive_schema(
    openapi_spec: OpenAPISpec | None,
    api_path: str,
    method: str,
) -> dict[str, Any] | None:
    """Derive the inner response schema for a given API path + method.

    Unwraps the ``{result: ...}`` envelope so the returned schema matches
    the raw API response shape -- exactly as ``custom.py`` did manually with
    ``_unwrap_result_schema(_get_success_schema(...))``.

    The schema is returned with ``$ref`` intact (``resolve=False``) for
    ``$ref``-aware data collapse in the display layer.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec, or ``None``.
        api_path: API path to look up (e.g. ``/repos/{owner}/{repo}``).
        method: HTTP method (e.g. ``"get"``).

    Returns:
        Unwrapped inner response schema with ``$ref`` intact, or ``None``
        if the spec is unavailable or the endpoint is not found.
    """
    if openapi_spec is None:
        return None
    schema = _get_success_schema(openapi_spec, api_path, method, resolve=False)
    return _unwrap_result_schema(schema)


def _build_handler_meta(
    *,
    response_schema: dict[str, Any] | None = None,
    format_hint: str | None = None,
) -> dict[str, Any] | None:
    """Build the content metadata dict for a JSON resource response."""
    meta: dict[str, Any] = {}
    if response_schema is not None:
        meta["response_schema"] = response_schema
    if format_hint is not None:
        meta["format_hint"] = format_hint
    return meta if meta else None


def make_api_resource(  # noqa: PLR0912, PLR0913, PLR0915 -- 12 params + branching are intentional: all independent registration axes
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec | None,
    *,
    uri: str,
    api_path: str,
    method: str = "GET",
    format_hint: str | None = None,
    scope: str | None = None,
    cache_ttl: float | None = None,
    tags: set[str] | None = None,
    error_message: str | None = None,
    available_scopes: set[str] | None = None,
) -> Callable[..., Any] | None:
    """Create and register a custom resource from an API endpoint.

    Derives the response schema from ``openapi_spec[api_path][method]``
    (unresolved, then unwrapped from the result envelope).  Generates the
    handler closure, handles ``str`` vs JSON branching, registers the URI
    in ``_registered_uris``, and calls ``mcp.resource()``.

    Returns ``None`` if scope-filtered (no registration occurs).

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        uri: Resource URI template (e.g. ``"gitea://repos/{owner}/{repo}"``).
        api_path: API path in spec (e.g. ``"/repos/{owner}/{repo}"``).
        method: HTTP method (default: ``"GET"``).
        format_hint: Registered formatter name for markdown rendering.
        scope: Required token scope (e.g. ``"read:repository"``).
        cache_ttl: Cache TTL in seconds (passed via resource meta).
        tags: Set of resource tags (e.g. ``{"repository"}``).  The
            ``"wrapper"`` tag is always added automatically.
        error_message: User-facing 404 error message template using
            ``{param}`` placeholders from the handler kwargs.
            Default: ``"Resource not found."``.
        available_scopes: Set of scopes the token has, or ``None``
            (no scope filtering).  When set and ``scope`` is not
            satisfied, the resource is silently skipped.

    Returns:
        The registered handler callable, or ``None`` if scope-filtered.

    Raises:
        ValueError: If ``api_path`` or ``method`` not found in
            ``openapi_spec`` (when spec is available).
    """
    # Scope check -- same logic as ``@_register`` in ``custom.py``.
    if scope is not None and available_scopes is not None and not has_sufficient_scope(scope, available_scopes):
        logger.debug(
            "Skipping resource %s: requires scope %s",
            uri, scope,
        )
        return None

    # Auto-derive schema from the spec.
    # When the endpoint is missing from the spec (e.g. test subsets that
    # don't include all production paths), warn and proceed without schema
    # -- the resource is still registered so that scope filtering and
    # registration count tests pass.
    method_lower = method.lower()
    response_schema = _auto_derive_schema(openapi_spec, api_path, method_lower)
    if response_schema is None and openapi_spec is not None:
        paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
        if paths:
            path_item = paths.get(api_path, {})
            if not isinstance(path_item, dict) or method_lower not in path_item:
                logger.warning(
                    "make_api_resource: %s %s not found in OpenAPI spec -- "
                    "registering without schema derivation",
                    method,
                    api_path,
                )

    # Build resource metadata.
    meta: dict[str, Any] = {}
    scope_meta_dict = scope_meta(scope)
    if scope_meta_dict:
        meta.update(scope_meta_dict)
    if cache_ttl is not None:
        meta["cache_ttl"] = cache_ttl

    # Build tags.
    resource_tags: set[str] = set(tags) if tags else set()
    resource_tags.add("wrapper")

    # Default error message.
    if error_message is None:
        error_message = "Resource not found."

    # Detect whether the URI has path parameters -- concrete URIs
    # (e.g. ``gitea://user``) need a handler with no function params,
    # otherwise FastMCP creates a ResourceTemplate and fails the
    # "URI template must contain at least one parameter" validation.
    _has_uri_params = bool(re.search(r"\{[\w?]+\}", uri))

    if _has_uri_params:

        async def handler(**kwargs: Any) -> ResourceResult:
            """Auto-generated resource handler from factory."""
            # Build formatted API path for error reporting.
            formatted_path = api_path
            for key, value in kwargs.items():
                formatted_path = formatted_path.replace(f"{{{key}}}", str(value))

            try:
                data = await gitea_client.request(method.upper(), formatted_path)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status == HTTP_STATUS_NOT_FOUND:
                    # Format error message with kwargs.
                    try:
                        msg = error_message.format(**kwargs)
                    except (KeyError, ValueError):
                        msg = error_message
                    raise ResourceError({
                        "code": "NOT_FOUND",
                        "message": msg,
                        "detail": str(e),
                        "resource_type": format_hint or "api",
                        "resource_id": formatted_path,
                    }) from e
                if status:
                    raise ResourceError({
                        "code": "API_ERROR",
                        "message": f"API error {status} for {uri}",
                        "detail": str(e),
                        "resource_type": format_hint or "api",
                        "resource_id": formatted_path,
                    }) from e
                raise ResourceError({
                    "code": "INTERNAL_ERROR",
                    "message": f"Unexpected error fetching resource: {uri}",
                    "detail": str(e),
                    "resource_type": format_hint or "api",
                    "resource_id": formatted_path,
                }) from e

            # String vs JSON branching.
            if isinstance(data, str):
                return ResourceResult(contents=[
                    ResourceContent(content=data, mime_type="text/plain"),
                ])

            return ResourceResult(contents=[
                ResourceContent(
                    content=json.dumps(data),
                    mime_type="application/json",
                    meta=_build_handler_meta(
                        response_schema=response_schema,
                        format_hint=format_hint,
                    ),
                ),
            ])

    else:

        async def handler() -> ResourceResult:  # type: ignore[misc]
            """Auto-generated resource handler from factory (concrete URI)."""
            try:
                data = await gitea_client.request(method.upper(), api_path)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status == HTTP_STATUS_NOT_FOUND:
                    raise ResourceError({
                        "code": "NOT_FOUND",
                        "message": error_message,
                        "detail": str(e),
                        "resource_type": format_hint or "api",
                        "resource_id": api_path,
                    }) from e
                if status:
                    raise ResourceError({
                        "code": "API_ERROR",
                        "message": f"API error {status} for {uri}",
                        "detail": str(e),
                        "resource_type": format_hint or "api",
                        "resource_id": api_path,
                    }) from e
                raise ResourceError({
                    "code": "INTERNAL_ERROR",
                    "message": f"Unexpected error fetching resource: {uri}",
                    "detail": str(e),
                    "resource_type": format_hint or "api",
                    "resource_id": api_path,
                }) from e

            if isinstance(data, str):
                return ResourceResult(contents=[
                    ResourceContent(content=data, mime_type="text/plain"),
                ])

            return ResourceResult(contents=[
                ResourceContent(
                    content=json.dumps(data),
                    mime_type="application/json",
                    meta=_build_handler_meta(
                        response_schema=response_schema,
                        format_hint=format_hint,
                    ),
                ),
            ])

    # Set docstring from operation summary/description.
    if openapi_spec is not None:
        paths = cast("dict[str, Any]", openapi_spec.get("paths", {}))
        path_item = paths.get(api_path, {})
        if isinstance(path_item, dict):
            operation = path_item.get(method_lower, {})
            if isinstance(operation, dict):
                summary = operation.get("summary", "")
                description = operation.get("description", "")
                docstring = summary
                if description:
                    docstring += "\n\n" + description
                if docstring:
                    handler.__doc__ = docstring

    if handler.__doc__ is None:
        handler.__doc__ = f"Resource for {method} {api_path}"

    # Register with FastMCP.
    mcp.resource(
        uri,
        mime_type="application/json",
        tags=resource_tags,
        meta=meta if meta else None,
    )(handler)

    # Track URI for auto-generation skip.
    _registered_uris.add(uri)

    logger.debug("Registered factory resource: %s", uri)
    return handler


__all__ = [
    "_auto_derive_schema",
    "_registered_uris",
    "make_api_resource",
]
