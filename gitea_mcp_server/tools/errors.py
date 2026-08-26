"""Error translation and runtime validation for tool execution.

Provides ``run_with_error_handling()`` — the runtime validation runner
that translates HTTP errors to agent-friendly messages — plus validation
error message formatting.  Called by the tool wrapping pipeline before
the HTTP request.
"""

import logging
from typing import Any, NoReturn, cast

import httpx
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.constants import PAGE_SIZE_MAX
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.tools.schemas import resolve_ref
from gitea_mcp_server.validation import (
    SINGLE_VALIDATORS,
    ValidationError,
    _collect_enum_values,
    _validate_enum_from_schema,
    validate_pagination,
)

logger = logging.getLogger(__name__)


def raise_value_error(message: str) -> NoReturn:
    """Raise a ValueError with a user-friendly message."""
    raise ValueError(message) from None


def raise_value_error_from(message: str, cause: Exception) -> NoReturn:
    """Raise a ValueError with a user-friendly message, chaining the original cause."""
    raise ValueError(message) from cause


def _raise_validation_error(message: str, field: str, cause: Exception) -> NoReturn:
    """Raise a ValidationError for a specific field, chaining the original cause."""
    raise ValidationError(message, field=field) from cause


def _lookup_response_description(
    openapi_spec: OpenAPISpec,
    path: str,
    method: str,
    status_code: int,
) -> str:
    """Look up the response description from the OpenAPI spec for error formatting.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``).
        path: The request path template (e.g. /repos/{owner}/{repo}).
        method: HTTP method (case-insensitive; normalized to lowercase internally).
        status_code: The HTTP status code returned.

    Returns:
        The description string from the spec, or a fallback ``"HTTP error {code}"``.
    """
    fallback = f"HTTP error {status_code}"
    result = fallback
    try:
        paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
        path_item = paths.get(path)
        if not path_item:
            result = fallback
        else:
            method_lower = method.lower()
            operation = path_item.get(method_lower) if method_lower else None
            if not operation:
                result = fallback
            else:
                responses = operation.get("responses", {})
                response_def = responses.get(str(status_code))
                if not response_def or not isinstance(response_def, dict):
                    result = fallback
                elif "description" in response_def:
                    result = str(response_def["description"])
                elif "$ref" in response_def:
                    resolved = resolve_ref(openapi_spec, response_def["$ref"])
                    if isinstance(resolved, dict):
                        desc = resolved.get("description")
                        result = str(desc) if desc else fallback
    except (KeyError, TypeError, AttributeError, ValueError):
        result = fallback
    return result


def _param_is_boolean(properties: dict[str, Any] | None, name: str) -> bool:
    """Check whether a parameter's JSON schema declares it as boolean type.

    Args:
        properties: The tool's parameter properties dict, or None.
        name: The parameter name to check.

    Returns:
        True if the parameter schema has type 'boolean' or ['boolean', ...].
    """
    if not properties:
        return False
    schema = properties.get(name)
    if not isinstance(schema, dict):
        return False
    t = schema.get("type")
    if isinstance(t, str):
        return t == "boolean"
    if isinstance(t, list):
        return "boolean" in t
    return False


def _format_missing_params(
    missing: list[str],
    param_properties: dict[str, Any] | None,
) -> str:
    """Build a user-friendly error fragment for missing required parameters.

    When a param's schema has an ``enum``, the message includes the
    expected values so agents know what to provide.

    Returns a string like ``"owner, state (expected one of: open, closed)"``.
    """
    parts: list[str] = []
    for p in missing:
        if param_properties and isinstance(param_properties.get(p), dict):
            enum_vals = _collect_enum_values(param_properties[p])
            if enum_vals:
                parts.append(f"{p} (expected one of: {', '.join(str(v) for v in enum_vals)})")
            else:
                parts.append(p)
        else:
            parts.append(p)
    return ", ".join(parts)


def run_validation(
    kwargs: dict[str, Any],
    required_params: list[str] | None = None,
    param_properties: dict[str, Any] | None = None,
) -> None:
    """Validate tool arguments against registered validators and schema enums.

    Validation is performed in three ordered stages:

    1. **Missing required** — every param in *required_params* must be
       present in *kwargs*.
    2. **Unknown parameters** — when *param_properties* is non-empty,
       every key in *kwargs* must be declared in *param_properties*.
       Virtual params (format, detail, etc.) have already been extracted
       by the caller, so any remaining key not in the schema is an agent
       typo and is rejected.  When *param_properties* is ``None`` or
       ``{}`` the check is skipped (insufficient schema information).
    3. **Per-argument dispatch** — for each argument:
       1. Schema-driven enum validation (``enum`` / ``anyOf`` / ``oneOf``).
       2. Registered validator in
          :data:`~gitea_mcp_server.validation.SINGLE_VALIDATORS`.
       3. Boolean type skip (FastMCP handles type coercion).

    Step 3.1 enables tools like ``gitea_repo_create_status`` whose
    ``state`` parameter's valid values are resolved from the spec
    rather than hardcoded to issue-state values.

    Args:
        kwargs: The tool arguments from the agent.
        required_params: List of required parameter names, or ``None``.
        param_properties: The tool's ``parameters.properties`` dict, or
            ``None``.  Used for enum checks and unknown-arg rejection.
    """
    missing = [p for p in (required_params or []) if p not in kwargs]
    if missing:
        parts = _format_missing_params(missing, param_properties)
        msg = f"Missing required parameter(s): {parts}"
        _raise_validation_error(msg, missing[0], ValueError(msg))
    # Reject unknown parameters when the parameter schema is available.
    # Virtual params (format, detail, sudo, etc.) have already been
    # extracted by _ToolWrappingTransform.transform_fn via extract_from()
    # before run_validation is called, so any remaining key not in the
    # schema is an agent typo that must be rejected rather than silently
    # ignored (which would succeed on the wire: Gitea drops unknown query
    # params and FastMCP drops unknown kwargs).
    if param_properties:
        unknown = [k for k in kwargs if k not in param_properties]
        if unknown:
            quoted = ", ".join(repr(k) for k in unknown)
            msg = f"Unknown parameter(s): {quoted}"
            _raise_validation_error(msg, unknown[0], ValueError(msg))
    for name, value in kwargs.items():
        # Schema-driven enum validation: if the param's own schema defines
        # an enum (resolved from the spec or inferred from description),
        # validate against it.  This handles all tools, even those without
        # a hardcoded validator entry (e.g. ``state`` on the commit status
        # tool, whose enum comes from description inference).
        if param_properties and isinstance(param_properties.get(name), dict):
            enum_values = _collect_enum_values(param_properties[name])
            if enum_values is not None:
                _validate_enum_from_schema(value, field=name, enum_values=enum_values)
                continue
        if name in SINGLE_VALIDATORS:
            if _param_is_boolean(param_properties, name):
                continue
            try:
                SINGLE_VALIDATORS[name](value, field=name)
            except ValidationError:
                raise
            except (TypeError, ValueError, KeyError) as e:
                msg = f"Validation error for {name}: {e}"
                _raise_validation_error(msg, name, e)
    if "page" in kwargs or "limit" in kwargs:
        # The cap comes from the parameter schema — both tool families
        # declare it there (autogen via SCHEMA_CONSTRAINTS, synthetic via
        # the per-tool limit_max bound), so the per-tool limit is respected
        # without hardcoding a family-specific default here.
        limit_schema = (param_properties or {}).get("limit")
        limit_max = (
            limit_schema.get("maximum", PAGE_SIZE_MAX)
            if isinstance(limit_schema, dict)
            else PAGE_SIZE_MAX
        )
        validate_pagination(
            kwargs.get("page"),
            kwargs.get("limit"),
            page_size_name="limit",
            page_size_max=limit_max,
        )


async def run_with_error_handling(
    kwargs: dict[str, Any],
    component: Any,
    openapi_spec: OpenAPISpec | None,
    route_path: str,
    route_method: str,
) -> ToolResult:
    """Execute a tool run with comprehensive error translation.

    Catches HTTP status errors, network errors, and unexpected exceptions,
    translating them into agent-friendly ``ValueError`` messages enriched
    with response descriptions from the OpenAPI spec.

    Args:
        kwargs: The validated tool arguments.
        component: The tool component (must have a ``.run()`` method).
        openapi_spec: Post-conversion OpenAPI 3.1 spec (typed as
            ``OpenAPISpec``), or ``None`` to skip spec-based enrichment.
        route_path: Request path template for error message context
            (e.g. ``/repos/{owner}/{repo}``).
        route_method: HTTP method for error message context (e.g. ``GET``, ``POST``).
    """
    try:
        return cast("ToolResult", await component.run(kwargs))
    except ValueError as e:
        cause = e.__cause__
        if isinstance(cause, httpx.HTTPStatusError) and openapi_spec is not None:
            status_code = cause.response.status_code
            description = _lookup_response_description(
                openapi_spec,
                route_path,
                route_method,
                status_code,
            )
            try:
                error_body = cause.response.json()
                message = error_body.get("message", "")
                formatted = f"{description}\n\nDetails: {message}" if message else description
            except (ValueError, AttributeError):
                formatted = f"{description}\n\nDetails: {cause.response.text[:200]}"
            raise ValueError(formatted) from e
        raise
    except httpx.HTTPError as e:
        formatted = f"Network error: Could not reach the Gitea server.\n\nDetails: {e!s}"
        raise_value_error_from(formatted, e)
    except (KeyError, TypeError, AttributeError, RuntimeError):
        tool_name = getattr(component, "name", "unknown")
        logger.exception(
            "Unexpected error in tool %s (%s %s) with args: %s",
            tool_name,
            route_method,
            route_path,
            sorted(kwargs.keys()),
        )
        raise_value_error("An unexpected error occurred. Please check the server logs for details.")


__all__ = [
    "raise_value_error",
    "raise_value_error_from",
    "run_validation",
    "run_with_error_handling",
]
