"""Factory for creating custom resource handlers with auto schema derivation.

Provides ``make_api_resource()`` which generates and registers a resource
handler from a declarative description.

The factory auto-derives the response schema from the OpenAPI spec via
the endpoint's ``api_path + method``. Handlers handle ``str`` vs JSON branching
automatically.

URI tracking
------------
``make_api_resource()`` accepts an optional ``tracking_set`` parameter.
When provided, the registered URI is added to the caller-owned set --
no module-level mutable state.  ``register_custom_resources()`` creates
a local set, passes it to every factory call, and returns it so the
orchestrator can pass it as ``skip_uris`` to
``register_auto_generated_resources()``.

Parameter reference
-------------------
The table below summarises every parameter of ``make_api_resource()``.
See the function's docstring for detailed prose descriptions of each.

================================  =============  ==========================================================
Parameter                         Default        Purpose
================================  =============  ==========================================================
``uri``                           (required)     MCP resource URI template with ``{param}`` path segments
                                                 and optional ``{?a,b}`` query suffix.
``api_path``                      (required)     API path in spec (e.g. ``/repos/{owner}/{repo}/issues``).
``method``                        ``"GET"``      HTTP method.
``name``                          ``None``       Resource name passed to ``mcp.resource(name=...)``.
                                                 When ``None``, FastMCP auto-generates one.
``format_hint``                   ``None``       Registered formatter name in ``tools/display.py``.
                                                 Ignored when ``handler_hook`` is set.
``handler_hook``                  ``None``       Async callback returning a string from the raw API
                                                 response.  Skips schema derivation, registers as
                                                 ``text/plain``.
``resource_type``                 ``format_hint`` Machine-readable type for error responses.  Falls
                                                  back to ``"api"``.
``scope``                         ``None``       Required token scope.  Resource silently skipped when
                                                 absent from ``available_scopes``.
``cache_ttl``                     ``None``       Cache TTL in seconds.
``tags``                          ``set()``      Tags for discovery.  Caller-owned — ``"wrapper"``
                                                 must be included explicitly when the resource has
                                                 a ``format_hint`` (i.e., a markdown formatter).
``error_message``                 ``"Resource    User-facing 404 message with optional ``{param}``
                                                 placeholders.
``param_config``                  ``None``       A ``ResourceParamConfig`` dataclass grouping the 6
                                                 parameter-routing fields (query_params,
                                                 query_param_validators, context_params,
                                                 context_param_validators, context_meta_keys,
                                                 optional_params).  All fields optional.
``size_hint``                     auto-derived   ``"tiny"`` / ``"small"`` / ``"medium"`` / ``"large"``.
``default_detail``                auto-derived   ``"full"`` or ``"concise"``.  ``large`` → ``concise``.
``available_scopes``              ``None``       Token's available scopes.  When set and the token lacks
                                                 ``scope``, resource is silently skipped.
================================  =============  ==========================================================
"""

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.resources import ResourceContent, ResourceResult

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.meta import ResourceMeta
from gitea_mcp_server.scope import has_sufficient_scope
from gitea_mcp_server.tools.schemas import get_success_schema, unwrap_result_schema
from gitea_mcp_server.uri_utils import clean_resource_uri

logger = logging.getLogger(__name__)


@dataclass
class ResourceParamConfig:
    """Groups parameter-routing configuration for ``make_api_resource``.

    All fields are optional — create only what you need::

        ResourceParamConfig(query_params=["state"], optional_params=[...])

    Note: ``context_params`` and ``context_param_validators`` are currently
    unused by any production resource — no Gitea/Forgejo API parameter is
    genuinely display-only.  The mechanism is kept as a clean abstraction
    for future use should a genuine display-only param ever appear.
    """
    query_params: list[str] | None = None
    query_param_validators: dict[str, list[str]] | None = None
    context_params: list[str] | None = None
    # context_param_validators is unused in practice -- no Gitea/Forgejo API
    # parameter is genuinely display-only (all params in the spec are real
    # API query params).  The mechanism is kept as a clean abstraction for
    # future use should a genuine display-only param ever appear.
    context_param_validators: dict[str, list[str]] | None = None
    context_meta_keys: list[str] | None = None
    optional_params: list[dict[str, Any]] | None = None


def _auto_derive_schema(
    openapi_spec: OpenAPISpec | None,
    api_path: str,
    method: str,
) -> dict[str, Any] | None:
    """Derive the inner response schema for a given API path + method.

    Unwraps the ``{result: ...}`` envelope so the returned schema matches
    the raw API response shape -- exactly as ``custom.py`` did manually with
    ``unwrap_result_schema(get_success_schema(...))``.

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
    schema = get_success_schema(openapi_spec, api_path, method, resolve=False)
    return unwrap_result_schema(schema)


def _validate_optional_param(
    key: str,
    value: str,
    allowed_values: list[str],
    resource_type: str,
    resource_id: str,
) -> None:
    """Validate an optional (query or context) parameter value.

    Shared by ``query_param_validators`` and ``context_param_validators``
    in the handler loop.  Raises ``ResourceError`` with ``VALIDATION_ERROR``
    code if the value is not in ``allowed_values``.

    Args:
        key: Parameter name (e.g. ``"state"``, ``"type"``).
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
            "detail": f"The '{key}' parameter must be one of: {', '.join(allowed_values)}.",
            "resource_type": resource_type,
            "resource_id": resource_id,
        })


def _build_handler_meta(
    *,
    response_schema: dict[str, Any] | None = None,
    format_hint: str | None = None,
    **extra: Any,
) -> dict[str, Any] | None:
    """Build the content metadata dict for a JSON resource response.

    This is content-level metadata (``ResourceContent.meta``), distinct
    from registration-level metadata passed to ``mcp.resource(meta=...)``.
    Registration-level metadata (``optional_params``, ``cache_ttl``) is set
    directly in ``make_api_resource()``, not here.

    Extra keyword arguments are merged on top of the standard keys.  The
    display pipeline (``_mcp_read_resource_impl``) strips ``response_schema``
    and ``format_hint`` and surfaces everything else as the ``extra`` dict
    passed to domain formatters — useful for forwarding handler context
    like path params (``owner``, ``repo``) or query params (``type``).
    """
    meta: dict[str, Any] = {}
    if response_schema is not None:
        meta["response_schema"] = response_schema
    if format_hint is not None:
        meta["format_hint"] = format_hint
    meta.update(extra)
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
    handler_hook: Callable[[Any], Awaitable[str]] | None = None,
    handler_extra_meta: dict[str, Any] | None = None,
) -> ResourceResult:
    """Execute an API request and wrap the response into a ``ResourceResult``.

    Shared by both parameterized and concrete URI handler branches in
    ``make_api_resource``.  Handles error translation (404 → NOT_FOUND,
    other HTTP → API_ERROR, unexpected → INTERNAL_ERROR), ``str`` vs JSON
    branching, and metadata attachment.

    When ``handler_hook`` is provided, the API response is passed through
    the hook for post-processing (e.g., base64 decoding), and the result
    is returned as ``text/plain``.  Schema derivation and JSON wrapping
    are skipped in this case — the hook's return value is the final content.

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
        handler_hook: Optional async callback for post-processing the API
            response.  Receives the raw response data and returns a string.
        handler_extra_meta: Optional additional metadata to merge into
            ``ResourceContent.meta``.  The display pipeline surfaces these
            as the ``extra`` dict passed to domain formatters — use this
            to forward handler context like path params (``owner``,
            ``repo``) or query params (``type``).

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

    # When a handler_hook is provided, pass the response through the hook
    # and return as text/plain.  This is used for resources that need
    # post-processing (e.g., base64 decoding of Gitea ContentsResponse).
    if handler_hook is not None:
        content = await handler_hook(data)
        return ResourceResult(contents=[
            ResourceContent(content=content, mime_type="text/plain"),
        ])

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
                **(handler_extra_meta or {}),
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
                else:
                    handler.__doc__ = f"Resource for {method} {api_path}"

    if handler.__doc__ is None:  # pragma: no cover — handler closures in make_api_resource always have docstrings; kept as safety net for manual handler creation
        handler.__doc__ = f"Resource for {method} {api_path}"


def _build_optional_param_signature(
    handler_sig: inspect.Signature,
    optional_params_names: list[str],
) -> inspect.Signature:
    """Add optional URI template params as ``KEYWORD_ONLY`` params.

    FastMCP requires ``{?param}`` URI template entries to have matching
    optional function parameters with default values.  This helper takes a
    ``**kwargs``-style signature and adds each param as a ``KEYWORD_ONLY``
    parameter with ``default=None``, keeping the handler body unchanged
    (params flow through ``**kwargs``).

    Args:
        handler_sig: The handler's inspect.Signature.
        optional_params_names: List of optional param names to add (from
            ``query_params`` and/or ``context_params``).

    Returns:
        Modified signature with optional params inserted before the
        ``**kwargs`` parameter, or the original unchanged if the handler
        uses positional params instead of ``**kwargs``.
    """
    existing = handler_sig.parameters
    kwargs_param = existing.get("kwargs")
    if kwargs_param is None:
        return handler_sig  # Only works with **kwargs-style handlers

    new_params: list[inspect.Parameter] = [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None)
        for name in optional_params_names
        if name not in existing
    ]
    if not new_params:
        return handler_sig

    return handler_sig.replace(parameters=[*new_params, kwargs_param])


def make_api_resource(  # noqa: PLR0913,PLR0912,PLR0915 -- params are all independent registration axes; three-branch handler loop increases branch/statement count
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec | None,
    *,
    uri: str,
    api_path: str,
    method: str = "GET",
    name: str | None = None,
    format_hint: str | None = None,
    handler_hook: Callable[[Any], Awaitable[str]] | None = None,
    resource_type: str | None = None,
    scope: str | None = None,
    cache_ttl: float | None = None,
    tags: set[str] | None = None,
    error_message: str | None = None,
    available_scopes: set[str] | None = None,
    param_config: ResourceParamConfig | None = None,
    size_hint: str | None = None,
    default_detail: str | None = None,
    tracking_set: set[str] | None = None,
) -> Callable[..., Any] | None:
    """Create and register a custom resource from an API endpoint.

    Derives the response schema from ``openapi_spec[api_path][method]``
    (unresolved, then unwrapped from the result envelope).  Generates the
    handler closure, handles ``str`` vs JSON branching, and calls
    ``mcp.resource()``.  When ``tracking_set`` is provided, the registered
    URI is added to it.

    When ``handler_hook`` is provided, schema derivation and JSON wrapping
    are skipped.  The API response is passed through the hook for post-
    processing (e.g., base64 decoding), and the result is registered as
    ``text/plain``.  Use this for resources whose output is derived from
    but different from the API response (e.g., base64-decoded file content).

    Parameter routing (query vs context, validators, discovery metadata,
    formatter forwarding) is configured via ``param_config``, a
    ``ResourceParamConfig`` instance.  See that dataclass for details.

    Returns ``None`` if scope-filtered (no registration occurs).

    Note:
        This is a candidate for extracting higher-level wrappers
        (``make_list_resource()``, ``make_text_resource()``, etc.) that
        compose ``make_api_resource`` with common defaults — see
        ``docs/DEVELOPMENT.md`` → "Custom resource via factory" for
        the project-level perspective on when and how to decide.

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        uri: Resource URI template (e.g. ``"gitea://repos/{owner}/{repo}"``).
        api_path: API path in spec (e.g. ``"/repos/{owner}/{repo}"``).
        method: HTTP method (default: ``"GET"``).
        format_hint: Registered formatter name for markdown rendering.
            Not used when ``handler_hook`` is provided.
        handler_hook: Optional async callback for post-processing the API
            response.  When set, schema derivation is skipped and the
            result is registered as ``text/plain``.  The hook receives the
            raw API response data and returns a string.
        resource_type: Machine-readable resource type for error responses.
            Defaults to ``format_hint``, falling back to ``"api"``.
        scope: Required token scope (e.g. ``"read:repository"``).
        cache_ttl: Cache TTL in seconds (passed via resource meta).
        tags: Caller-owned set of resource tags (e.g. ``{"wrapper", "repository"}``).
            ``"wrapper"`` is NOT auto-added — include it explicitly when the
            resource has a ``format_hint`` (i.e., a markdown formatter).
        error_message: User-facing 404 error message template using
            ``{param}`` placeholders from the handler kwargs.
            Default: ``"Resource not found."``.
        available_scopes: Set of scopes the token has, or ``None``
            (no scope filtering).  When set and ``scope`` is not
            satisfied, the resource is silently skipped.
        param_config: A ``ResourceParamConfig`` instance grouping the
            parameter-routing configuration (``query_params``,
            ``query_param_validators``, ``context_params``,
            ``context_param_validators``, ``context_meta_keys``,
            ``optional_params``).  See the dataclass docstring for
            details on each field.  When ``None`` (default), no
            parameter routing is applied — the handler treats all
            kwargs as path parameters.
        size_hint: Estimated token cost of the resource content.
            One of ``"tiny"``, ``"small"``, ``"medium"``, ``"large"``.
            When not set, auto-derived from the response schema.
        default_detail: Recommended detail level for this resource.
            One of ``"full"`` or ``"concise"``.
            When not set, auto-derived from ``size_hint`` (``large``
            resources default to ``concise``; everything else to
            ``full``).
        tracking_set: Optional caller-owned set to which the registered
            URI is added.  When ``None`` (default), no tracking occurs.

            **When to pass a tracking set**: callers whose resources must
            be skipped by ``register_auto_generated_resources()`` (e.g.,
            ``register_custom_resources()``).  Pass a local ``set()``,
            collect URIs across all factory calls, and return it so the
            orchestrator can hand it to ``auto.py`` as ``skip_uris``.

            **When to omit**: callers that are *consumers* of skip_uris
            (e.g., ``auto.py`` itself) or don't participate in the custom-
            vs-auto override pattern.  Omitting is the right default —
            it keeps the two-sentence call site clean and the intent
            explicit at the point where tracking *does* matter.

    Returns:
        The registered handler callable, or ``None`` if scope-filtered.

    Raises:
        ValueError: If ``api_path`` or ``method`` not found in
            ``openapi_spec`` (when spec is available).
    """
    # Scope check.
    if scope is not None and available_scopes is not None and not has_sufficient_scope(scope, available_scopes):
        logger.debug(
            "Skipping resource %s: requires scope %s",
            uri, scope,
        )
        return None

    # Unwrap the param_config dataclass into local variables so the handler
    # closure can capture them normally (avoids changing all internal refs).
    _pc = param_config or ResourceParamConfig()
    query_params = _pc.query_params
    query_param_validators = _pc.query_param_validators
    context_params = _pc.context_params
    context_param_validators = _pc.context_param_validators
    context_meta_keys = _pc.context_meta_keys
    optional_params = _pc.optional_params

    # Cross-list invariant: query_params and context_params must not overlap.
    if query_params and context_params:
        overlap = set(query_params) & set(context_params)
        if overlap:
            msg = (
                f"make_api_resource: params {sorted(overlap)} appear in both "
                f"query_params and context_params for {uri}. "
                "A param cannot be both an API parameter and a context-only parameter."
            )
            raise ValueError(msg)

    # Auto-derive schema from the spec.
    # When the endpoint is missing from the spec (e.g. test subsets that
    # don't include all production paths), warn and proceed without schema
    # -- the resource is still registered so that scope filtering and
    # registration count tests pass.
    #
    # When handler_hook is provided, skip schema derivation entirely --
    # text/plain resources have no JSON schema to derive.
    if handler_hook and format_hint:
        logger.warning(
            "make_api_resource: handler_hook set with format_hint=%r for %s -- "
            "format_hint is ignored when handler_hook is provided",
            format_hint, uri,
        )
    method_lower = method.lower()
    response_schema = None if handler_hook else _auto_derive_schema(openapi_spec, api_path, method_lower)
    if response_schema is None and openapi_spec is not None and handler_hook is None:
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
    # Use ResourceMeta.for_schema for typed construction with auto-derivation
    # of size_hint from the response schema when not explicitly provided.
    meta = ResourceMeta.for_schema(
        response_schema,
        required_scope=scope,
        cache_ttl=cache_ttl,
        optional_params=optional_params or None,
        size_hint=size_hint,
        default_detail=default_detail,
    ).to_dict()

    # Build tags (caller-owned; "wrapper" added by callers that have format_hint).
    resource_tags: set[str] = set(tags) if tags else set()

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
                        _validate_optional_param(
                            key, value, query_param_validators[key],
                            resource_type=_resource_type,
                            resource_id=formatted_path,
                        )
                    query_kwargs[key] = value
                elif context_params and key in context_params and value is not None:
                    # Context-only param: validate but do NOT forward to API.
                    if context_param_validators and key in context_param_validators and isinstance(value, str):
                        _validate_optional_param(
                            key, value, context_param_validators[key],
                            resource_type=_resource_type,
                            resource_id=formatted_path,
                        )
                else:
                    # Assume any remaining kwarg is a path parameter and
                    # substitute into the API path.  If the key isn't a
                    # valid path placeholder, the replace is a no-op --
                    # warn so misconfigured callers (tests, future code)
                    # don't silently get the wrong behavior.
                    placeholder = f"{{{key}}}"
                    if placeholder in formatted_path:
                        formatted_path = formatted_path.replace(placeholder, str(value))
                    else:
                        logger.warning(
                            "make_api_resource %s: unknown kwarg %r=%r "
                            "-- not a path, query, or context param; ignored",
                            uri, key, value,
                        )

            # Forward requested context keys as display metadata for
            # formatters that need extra context (e.g. ``type`` for
            # the issues title, ``owner``/``repo`` for the labels
            # heading).  Path params, query params, and context params
            # are all eligible -- the ``is not None`` guard excludes
            # absent optional params.
            handler_extra_meta: dict[str, Any] | None = None
            if context_meta_keys:
                extra = {
                    k: kwargs[k]
                    for k in context_meta_keys
                    if k in kwargs and kwargs[k] is not None
                }
                if extra:
                    handler_extra_meta = extra

            # Resolve resource_type dynamically based on ``type`` query
            # param so that error responses reflect what the agent was
            # querying.  The ``type`` value is a raw API entity name
            # ("issues" / "pulls"); display formatting (e.g. "pull requests")
            # belongs in the read_resource display layer.
            effective_resource_type: str = (
                "pulls" if kwargs.get("type") == "pulls" else _resource_type
            )

            return await _request_and_wrap(
                gitea_client, method, formatted_path,
                params=query_kwargs or None,
                response_schema=response_schema,
                format_hint=format_hint,
                resource_type=effective_resource_type,
                error_message=error_message,
                uri=uri,
                error_kwargs=kwargs,
                handler_hook=handler_hook,
                handler_extra_meta=handler_extra_meta,
            )

    else:

        async def handler() -> ResourceResult:  # type: ignore[misc]
            """Auto-generated resource handler from factory (concrete URI)."""
            # Concrete URIs have no path/query args, so context_meta_keys
            # cannot forward anything — warn the dev early.
            if context_meta_keys:
                logger.warning(
                    "make_api_resource: context_meta_keys=%r ignored for %s "
                    "(concrete URI — no handler kwargs to forward from)",
                    context_meta_keys, uri,
                )
            return await _request_and_wrap(
                gitea_client, method, api_path,
                response_schema=response_schema,
                format_hint=format_hint,
                resource_type=_resource_type,
                error_message=error_message,
                uri=uri,
                handler_hook=handler_hook,
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
    # Build the combined list of optional param names for the handler signature.
    # Both query_params and context_params need ``{?param}`` entries in the URI
    # template, which FastMCP validates against the handler's function signature.
    _optional_param_names: list[str] = []
    if query_params:
        _optional_param_names.extend(query_params)
    if context_params:
        _optional_param_names.extend(context_params)
    if _optional_param_names:
        _sig = _build_optional_param_signature(
            inspect.signature(handler), _optional_param_names,
        )
        if _sig != inspect.signature(handler):
            handler.__signature__ = _sig  # type: ignore[attr-defined]

    # Set docstring from operation summary/description or fallback to path.
    _set_handler_docstring(handler, openapi_spec, api_path, method, method_lower)

    # Register with FastMCP.
    # When handler_hook is provided, the resource returns text/plain --
    # the hook transforms the API response into human-readable text.
    # Otherwise, the resource returns application/json with schema metadata.
    mime_type = "text/plain" if handler_hook else "application/json"
    mcp.resource(
        uri,
        name=name,
        mime_type=mime_type,
        tags=resource_tags,
        meta=meta if meta else None,
    )(handler)

    # Track URI when the caller provides a tracking set
    # (e.g., register_custom_resources collects factory URIs for skip_uris).
    if tracking_set is not None:
        # Strip RFC 6570 {?query} suffix so the base URI matches
        # auto-generated resources.  Auto resources never carry query
        # suffixes (they build URIs from spec paths alone), so
        # "gitea://repos/{owner}/{repo}/issues" won't match
        # "gitea://repos/{owner}/{repo}/issues{?state}" without
        # this normalization.
        base_uri = clean_resource_uri(uri)
        tracking_set.add(base_uri)

    logger.debug("Registered factory resource: %s", uri)
    return handler


__all__ = [
    "ResourceParamConfig",
    "make_api_resource",
]
