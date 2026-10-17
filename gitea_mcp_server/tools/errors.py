"""HTTP error translation for tool execution.

Provides ``run_with_error_handling()`` — the runtime helper that translates
HTTP status errors, network errors, and unexpected exceptions into
agent-friendly messages, enriched with response descriptions from the OpenAPI
spec.  Called by the tool wrapping pipeline around the HTTP request.

Argument validation is **not** owned here: the contract spine
(``tools/contract.py``) validates every call once through
``validation.validate_call``, before any executor runs.
"""

import logging
from typing import Any, NoReturn, cast

import httpx
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.tools.schemas import resolve_ref

logger = logging.getLogger(__name__)


def raise_value_error(message: str) -> NoReturn:
    """Raise a ValueError with a user-friendly message."""
    raise ValueError(message) from None


def raise_value_error_from(message: str, cause: Exception) -> NoReturn:
    """Raise a ValueError with a user-friendly message, chaining the original cause."""
    raise ValueError(message) from cause


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
    "run_with_error_handling",
]
