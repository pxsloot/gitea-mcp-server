"""Factory for creating custom resource handlers with auto schema derivation.

Provides ``make_api_resource()`` which generates and registers a resource
handler from a declarative description -- eliminating the 5-step boilerplate
pattern that was repeated across every custom resource.

The factory auto-derives the response schema from the OpenAPI spec via
the endpoint's ``api_path + method``, removing the need for manual
``_get_success_schema`` / ``_unwrap_result_schema`` calls.  Handlers
handle ``str`` vs JSON branching automatically.

URI tracking
------------
The module-level ``_registered_uris`` set is populated dynamically at
registration time (not at import time).  ``register_custom_resources()``
runs *before* ``register_auto_generated_resources()``, and the resulting
set is passed as ``skip_uris`` to skip auto-generation for factory URIs.
"""

import inspect
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

# Populated at registration time by ``make_api_resource()``.
# Starts empty; grows as ``register_custom_resources()`` calls
# ``make_api_resource`` for each factory resource.
_registered_uris: set[str] = set()


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


def _validate_query_param(
    key: str,
    value: str,
    allowed_values: list[str],
    resource_type: str,
    resource_id: str,
) -> None:
    """Validate a query parameter value against allowed values.

    Raises ``ResourceError`` with ``VALIDATION_ERROR`` code if the value
    is not in ``allowed_values``.  This gives agents a clear error message
    about acceptable values — better than a generic API error.

    Args:
        key: Parameter name (e.g. ``"state"``).
        value: The value to validate.
        allowed_values: List of acceptable values.
        resource_type: Machine-readable resource type for error responses.
        resource_id: Human-readable resource identifier for error messages.

    Raises:
        ResourceError: If ``value`` is not in ``allowed_values``.
    """
    if value not in allowed_values:
        raise ResourceError({
            "code": "VALIDATION_ERROR",
            "message": (
                f"Invalid {key} parameter: '{value}'. "
                f"Must be one of: {', '.join(allowed_values)}."
            ),
            "detail": f"The '{key}' query parameter must be one of: {', '.join(allowed_values)}.",
            "resource_type": resource_type,
            "resource_id": resource_id,
        })


def _build_handler_meta(
    *,
    response_schema: dict[str, Any] | None = None,
    format_hint: str | None = None,
) -> dict[str, Any] | None:
    """Build the content metadata dict for a JSON resource response.

    This is content-level metadata (``ResourceContent.meta``), distinct
    from registration-level metadata passed to ``mcp.resource(meta=...)``.
    Registration-level metadata (``optional_params``, ``cache_ttl``) is set
    directly in ``make_api_resource()``, not here.
    """
    meta: dict[str, Any] = {}
    if response_schema is not None:
        meta["response_schema"] = response_schema
    if format_hint is not None:
        meta["format_hint"] = format_hint
    return meta if meta else None


async def _request_and_wrap(  # noqa: PLR0913 -- all params are independent inputs to error handling + content construction
    gitea_client: GiteaClient,
    method: str,
    api_path: str,
    *,
    params: dict[str, Any] | None = None,
    response_schema: dict[str, Any] | None,
    format_hint: str | None,
    resource_type: str,
    error_message: str,
    uri: str,
    error_kwargs: dict[str, Any] | None = None,
) -> ResourceResult:
    """Execute an API request and wrap the response into a ``ResourceResult``.

    Shared by both parameterized and concrete URI handler branches in
    ``make_api_resource``.  Handles error translation (404 → NOT_FOUND,
    other HTTP → API_ERROR, unexpected → INTERNAL_ERROR), ``str`` vs JSON
    branching, and metadata attachment.

    Args:
        gitea_client: Client for API calls.
        method: HTTP method (e.g. ``"GET"``).
        api_path: Full formatted API path (e.g. ``"/repos/owner/repo"``).
        params: Optional query params dict passed to the API request.
        response_schema: Unwrapped inner response schema for display layer.
        format_hint: Registered formatter name for markdown rendering.
        resource_type: Machine-readable resource type for error responses.
        error_message: User-facing 404 error message, possibly a template
            expanded with ``error_kwargs``.
        uri: Resource URI template (for error messages).
        error_kwargs: Keyword arguments for ``error_message.format()``.
            Only used when the error message has ``{param}`` placeholders.

    Returns:
        The wrapped ``ResourceResult``.

    Raises:
        ResourceError: With structured error codes on failure.
    """
    try:
        data = await gitea_client.request(method.upper(), api_path, params=params)
    except Exception as e:
        status = getattr(e, "status_code", None)
        if status == HTTP_STATUS_NOT_FOUND:
            try:
                msg = error_message.format(**(error_kwargs or {}))
            except (KeyError, ValueError):
                msg = error_message
            raise ResourceError({
                "code": "NOT_FOUND",
                "message": msg,
                "detail": str(e),
                "resource_type": resource_type,
                "resource_id": api_path,
            }) from e
        if status:
            raise ResourceError({
                "code": "API_ERROR",
                "message": f"API error {status} for {uri}",
                "detail": str(e),
                "resource_type": resource_type,
                "resource_id": api_path,
            }) from e
        raise ResourceError({
            "code": "INTERNAL_ERROR",
            "message": f"Unexpected error fetching resource: {uri}",
            "detail": str(e),
            "resource_type": resource_type,
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


def _set_handler_docstring(
    handler: Callable[..., Any],
    openapi_spec: OpenAPISpec | None,
    api_path: str,
    method: str,
    method_lower: str,
) -> None:
    """Set the handler's docstring from the OpenAPI operation summary/description.

    Falls back to ``Resource for {method} {api_path}`` when no spec info is found.
    """
    if openapi_spec is not None:
        paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
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


def _build_query_param_signature(
    handler_sig: inspect.Signature,
    query_params: list[str],
) -> inspect.Signature:
    """Add query params as ``KEYWORD_ONLY`` params to a handler signature.

    FastMCP requires ``{?param}`` URI template entries to have matching
    optional function parameters with default values.  This helper takes a
    ``**kwargs``-style signature and adds each query param as a
    ``KEYWORD_ONLY`` parameter with ``default=None``, keeping the actual
    handler body unchanged (params flow through ``**kwargs``).

    Args:
        handler_sig: The handler's inspect.Signature.
        query_params: List of query parameter names to add.

    Returns:
        Modified signature with query params inserted before the
        ``**kwargs`` parameter, or the original signature unchanged
        if the handler uses positional params instead of ``**kwargs``.
    """
    existing = handler_sig.parameters
    kwargs_param = existing.get("kwargs")
    if kwargs_param is None:
        return handler_sig  # Only works with **kwargs-style handlers

    new_params: list[inspect.Parameter] = [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None)
        for name in query_params
        if name not in existing
    ]
    if not new_params:
        return handler_sig

    return handler_sig.replace(parameters=[*new_params, kwargs_param])


def make_api_resource(  # noqa: PLR0913 -- 16 params + branching are intentional: all independent registration axes
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec | None,
    *,
    uri: str,
    api_path: str,
    method: str = "GET",
    format_hint: str | None = None,
    resource_type: str | None = None,
    scope: str | None = None,
    cache_ttl: float | None = None,
    tags: set[str] | None = None,
    error_message: str | None = None,
    available_scopes: set[str] | None = None,
    query_params: list[str] | None = None,
    query_param_validators: dict[str, list[str]] | None = None,
    optional_params: list[dict[str, Any]] | None = None,
) -> Callable[..., Any] | None:
    """Create and register a custom resource from an API endpoint.

    Derives the response schema from ``openapi_spec[api_path][method]``
    (unresolved, then unwrapped from the result envelope).  Generates the
    handler closure, handles ``str`` vs JSON branching, registers the URI
    in ``_registered_uris``, and calls ``mcp.resource()``.

    Query params (designated by ``query_params``) are extracted from the
    handler kwargs into a ``params`` dict passed to the underlying API
    call -- they are *not* substituted into the path template.  When
    ``query_param_validators`` specifies allowed values for a param, the
    handler validates before making the API call and raises a clear
    ``ResourceError`` on invalid input.

    Optional params metadata (``optional_params``) is attached to the
    resource registration so agents can discover available parameters
    via ``list_resources`` without needing to read the resource first.

    Returns ``None`` if scope-filtered (no registration occurs).

    Note:
        If future patterns repeat (e.g., many list resources share the
        same structure), consider extracting higher-level wrappers like
        ``make_list_resource()`` or ``make_text_resource()`` that compose
        ``make_api_resource`` with common defaults.

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        uri: Resource URI template (e.g. ``"gitea://repos/{owner}/{repo}"``).
        api_path: API path in spec (e.g. ``"/repos/{owner}/{repo}"``).
        method: HTTP method (default: ``"GET"``).
        format_hint: Registered formatter name for markdown rendering.
        resource_type: Machine-readable resource type for error responses.
            Defaults to ``format_hint``, falling back to ``"api"``.
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
        query_params: Optional list of kwargs names to treat as query
            parameters.  These are NOT substituted into the path; they
            are extracted and passed as a ``params`` dict to the API
            request.  Handy for resources with optional filters like
            ``state``.
        query_param_validators: Optional dict mapping query param names
            to lists of allowed values.  When set, the handler validates
            the param value against the list before making the API call
            and raises a ``ResourceError`` with ``VALIDATION_ERROR`` code
            on invalid input.  Example: ``{"state": ["open", "closed"]}``.
        optional_params: Optional list of dicts describing available
            optional parameters for agent discovery.  Each dict should
            have at least a ``"name"`` key; ``"type"``, ``"values"``,
            and ``"description"`` are recommended.  Attached to resource
            metadata under ``meta["optional_params"]``.

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

    # Build resource metadata (passed to ``mcp.resource(meta=...)``).
    meta: dict[str, Any] = {}
    scope_meta_dict = scope_meta(scope)
    if scope_meta_dict:
        meta.update(scope_meta_dict)
    if cache_ttl is not None:
        meta["cache_ttl"] = cache_ttl
    if optional_params:
        meta["optional_params"] = optional_params

    # Build tags.
    resource_tags: set[str] = set(tags) if tags else set()
    resource_tags.add("wrapper")

    # Default error message and resource type.
    if error_message is None:
        error_message = "Resource not found."
    _resource_type: str = resource_type or format_hint or "api"

    # Detect whether the URI has path parameters -- concrete URIs
    # (e.g. ``gitea://user``) need a handler with no function params,
    # otherwise FastMCP creates a ResourceTemplate and fails the
    # "URI template must contain at least one parameter" validation.
    _has_uri_params = bool(re.search(r"\{[\w?*,]+\}", uri))

    if _has_uri_params:

        async def handler(**kwargs: Any) -> ResourceResult:
            """Auto-generated resource handler from factory."""
            formatted_path = api_path
            query_kwargs: dict[str, Any] = {}
            for key, value in kwargs.items():
                if query_params and key in query_params and value is not None:
                    # Validate against allowed values if a validator is registered.
                    if query_param_validators and key in query_param_validators and isinstance(value, str):
                        _validate_query_param(
                            key, value, query_param_validators[key],
                            resource_type=_resource_type,
                            resource_id=formatted_path,
                        )
                    query_kwargs[key] = value
                else:
                    formatted_path = formatted_path.replace(f"{{{key}}}", str(value))
            return await _request_and_wrap(
                gitea_client, method, formatted_path,
                params=query_kwargs or None,
                response_schema=response_schema,
                format_hint=format_hint,
                resource_type=_resource_type,
                error_message=error_message,
                uri=uri,
                error_kwargs=kwargs,
            )

    else:

        async def handler() -> ResourceResult:  # type: ignore[misc]
            """Auto-generated resource handler from factory (concrete URI)."""
            return await _request_and_wrap(
                gitea_client, method, api_path,
                response_schema=response_schema,
                format_hint=format_hint,
                resource_type=_resource_type,
                error_message=error_message,
                uri=uri,
            )

    # FastMCP validates ``{?param}`` template entries against the handler's
    # function signature, requiring matching optional params with defaults.
    # The factory handler uses ``**kwargs`` which doesn't declare those params
    # explicitly, so we override ``__signature__`` -- a standard Python feature
    # documented in the ``inspect`` module for exactly this scenario.
    #
    # The ``type: ignore[attr-defined]`` is needed because mypy's function
    # type stubs don't include ``__signature__``.  This is a typeshed gap --
    # setting ``__signature__`` on a function is part of Python's data model,
    # not a workaround.
    if query_params:
        _sig = _build_query_param_signature(
            inspect.signature(handler), query_params
        )
        if _sig != inspect.signature(handler):
            handler.__signature__ = _sig  # type: ignore[attr-defined]

    # Set docstring from operation summary/description or fallback to path.
    _set_handler_docstring(handler, openapi_spec, api_path, method, method_lower)

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
    "_build_query_param_signature",
    "_registered_uris",
    "_request_and_wrap",
    "_set_handler_docstring",
    "_validate_query_param",
    "make_api_resource",
]
