"""Error handling utilities for tool execution."""

import logging
from typing import Any, NoReturn, cast

import httpx

from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.tools.schemas import _resolve_ref
from gitea_mcp_server.validation import (
    SINGLE_VALIDATORS,
    ValidationError,
    validate_pagination,
)

logger = logging.getLogger(__name__)


def _raise_value_error(message: str) -> NoReturn:
    """Raise a ValueError with a user-friendly message."""
    raise ValueError(message) from None


def _raise_value_error_from(message: str, cause: Exception) -> NoReturn:
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
        openapi_spec: The full OpenAPI 3.1 spec dict.
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
                    resolved = _resolve_ref(cast("dict[str, Any]", openapi_spec), response_def["$ref"])
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


def _run_validation(
    kwargs: dict[str, Any],
    required_params: list[str] | None = None,
    param_properties: dict[str, Any] | None = None,
) -> None:
    missing = [p for p in (required_params or []) if p not in kwargs]
    if missing:
        msg = f"Missing required parameter(s): {', '.join(missing)}"
        _raise_validation_error(msg, missing[0], ValueError(msg))
    for name, value in kwargs.items():
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
    if "page" in kwargs or "per_page" in kwargs:
        validate_pagination(kwargs.get("page"), kwargs.get("per_page"))


async def _run_with_error_handling(
    kwargs: dict[str, Any],
    component: Any,
    openapi_spec: OpenAPISpec | None,
    route_path: str,
    route_method: str,
) -> Any:
    """Execute a tool run with comprehensive error translation.

    Catches HTTP status errors, network errors, and unexpected exceptions,
    translating them into agent-friendly ``ValueError`` messages enriched
    with response descriptions from the OpenAPI spec.

    Args:
        kwargs: The validated tool arguments.
        component: The tool component (must have a ``.run()`` method).
        openapi_spec: OpenAPI spec for response description lookups, or ``None``
            to skip spec-based enrichment.
        route_path: Request path template for error message context
            (e.g. ``/repos/{owner}/{repo}``).
        route_method: HTTP method for error message context (e.g. ``GET``, ``POST``).
    """
    try:
        return await component.run(kwargs)
    except ValueError as e:
        cause = e.__cause__
        if isinstance(cause, httpx.HTTPStatusError) and openapi_spec is not None:
            status_code = cause.response.status_code
            description = _lookup_response_description(
                openapi_spec, route_path, route_method, status_code,
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
        _raise_value_error_from(formatted, e)
    except (KeyError, TypeError, AttributeError, RuntimeError):
        logger.exception("Unexpected error during tool execution")
        _raise_value_error(
            "An unexpected error occurred. Please check the server logs for details."
        )


__all__ = [
    "_lookup_response_description",
    "_raise_validation_error",
    "_raise_value_error",
    "_raise_value_error_from",
    "_run_validation",
    "_run_with_error_handling",
]
