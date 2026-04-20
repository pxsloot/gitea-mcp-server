"""Tool annotation and customization utilities.

This module contains functions for customizing FastMCP tools generated from OpenAPI,
including title generation, categorization, hint inference, label handling, and
cache invalidation pattern computation.
"""

import json
import logging
from collections.abc import Sequence
from typing import Annotated, Any

import httpx
from fastmcp.server.context import Context
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.cache_invalidation import register_tool_invalidation
from gitea_mcp_server.constants import (
    HTTP_METHODS_DESTRUCTIVE,
    HTTP_METHODS_IDEMPOTENT,
    HTTP_METHODS_SAFE,
    LABEL_GUIDANCE,
    PATTERN_FILES,
    PATTERN_ISSUES_CLOSED,
    PATTERN_ISSUES_LIST,
    PATTERN_ISSUES_OPEN,
    PATTERN_PULLS_CLOSED,
    PATTERN_PULLS_LIST,
    PATTERN_PULLS_OPEN,
    PATTERN_REPO,
    RESOURCE_NOTE,
    TITLE_TRUNCATE_LIMIT,
)
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.validation import (
    SINGLE_VALIDATORS,
    ValidationError,
    augment_schema_with_validation,
    validate_pagination,
)

logger = logging.getLogger(__name__)


def _raise_value_error(message: str) -> None:
    """Raise ValueError with pre-computed message."""
    raise ValueError(message) from None


def _raise_value_error_from(message: str, cause: Exception) -> None:
    """Raise ValueError with message and cause."""
    raise ValueError(message) from cause


def _raise_validation_error(message: str, field: str, cause: Exception) -> None:
    """Raise ValidationError with pre-computed message."""
    raise ValidationError(message, field=field) from cause


def generate_tool_title(route: Any) -> str:
    """Generate a human-readable title for a tool from its OpenAPI route metadata.

    Args:
        route: FastMCP route object with summary and operation_id attributes

    Returns:
        Title string (max TITLE_TRUNCATE_LIMIT chars, truncated with "..." if needed)
    """
    summary = getattr(route, "summary", None)
    operation_id = getattr(route, "operation_id", None)

    title: str

    # Prefer summary if available and non-empty
    if summary and summary.strip():
        title = str(summary).strip()
    elif operation_id:
        # Convert snake_case to Title Case
        words = str(operation_id).replace("_", " ").title()
        title = words
    else:
        return "Unnamed Tool"

    # Truncate to TITLE_TRUNCATE_LIMIT characters
    if len(title) > TITLE_TRUNCATE_LIMIT:
        title = title[: TITLE_TRUNCATE_LIMIT - 3] + "..."

    return title


_CATEGORY_PREFIXES: list[tuple[str, str]] = [
    ("/admin", "admin"),
    ("/orgs", "organization"),
    ("/org/", "organization"),
    ("/user", "user"),
    ("/users/", "user"),
    ("/issues", "issue"),
    ("/pulls", "pull_request"),
    ("/repos", "repository"),
]


def categorize_tool(path: str) -> str:
    """Categorize a tool based on its OpenAPI path.

    Args:
        path: The OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")

    Returns:
        Category string: "repository", "issue", "pull_request", "user", "organization", "admin", or "misc"
    """
    for prefix, category in _CATEGORY_PREFIXES:
        if path.startswith(prefix):
            return category
    return "misc"


def add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
    """Infer and add annotation hints from HTTP route properties.

    Hints are based on HTTP method semantics:
    - readOnlyHint: True for safe methods (GET, HEAD, OPTIONS)
    - destructiveHint: True for DELETE (and any method that destroys data)
    - idempotentHint: True for idempotent methods (GET, PUT, DELETE, HEAD, OPTIONS)
    - openWorldHint: Always True for Gitea tools (they interact with external server)

    Existing annotation values are preserved; only None values are set.

    Args:
        route: HTTPRoute object with method attribute
        annotations: ToolAnnotations instance to update
    """
    method = getattr(route, "method", None)

    # Only add hints if they are currently None (preserve existing manual settings)
    if annotations.readOnlyHint is None:
        annotations.readOnlyHint = method in HTTP_METHODS_SAFE

    if annotations.destructiveHint is None:
        annotations.destructiveHint = method in HTTP_METHODS_DESTRUCTIVE

    if annotations.idempotentHint is None:
        annotations.idempotentHint = method in HTTP_METHODS_IDEMPOTENT

    if annotations.openWorldHint is None:
        # All Gitea MCP tools interact with external Gitea server
        annotations.openWorldHint = True


_INVALIDATION_PATTERNS: list[tuple[str, str | None, list[str]]] = [
    (
        "/repos/{owner}/{repo}/issues",
        None,
        [PATTERN_ISSUES_LIST, PATTERN_ISSUES_OPEN, PATTERN_ISSUES_CLOSED],
    ),
    (
        "/repos/{owner}/{repo}/pulls",
        None,
        [PATTERN_PULLS_LIST, PATTERN_PULLS_OPEN, PATTERN_PULLS_CLOSED],
    ),
    ("/repos/{owner}/{repo}", "exact", [PATTERN_REPO]),
    ("/repos/{owner}/{repo}/contents", None, [PATTERN_FILES]),
    ("/repos/{owner}/{repo}/labels", None, [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]),
    ("/repos/{owner}/{repo}/milestones", None, [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]),
    ("/repos/{owner}/{repo}/releases", None, [PATTERN_REPO]),
    ("/repos/{owner}/{repo}/topics", None, [PATTERN_REPO]),
]


def compute_invalidation_patterns(path: str, method: str) -> list[str]:
    """Compute resource invalidation patterns for a tool based on its path and method.

    This function analyzes the OpenAPI path and HTTP method to determine which
    MCP resource patterns should be invalidated when this tool is called.

    Args:
        path: OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)

    Returns:
        List of pattern names (keys in RESOURCE_PATTERNS) to invalidate.
        Empty list if no invalidation needed.
    """
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return []

    for prefix, match_type, patterns in _INVALIDATION_PATTERNS:
        if match_type == "exact":
            if path == prefix:
                return patterns
        elif path.startswith(prefix):
            return patterns
    return []


def update_labels_schema(component: OpenAPITool) -> None:
    """Update the tool's schema to show that labels accept both string and integer types.

    The OpenAPI spec defines labels as array of integers, but the runtime accepts strings
    (with auto-conversion). This function updates the JSON schema to reflect the actual
    accepted types: ["string", "integer"].

    Args:
        component: OpenAPITool to update
    """
    params = getattr(component, "parameters", None)
    if not params:
        return

    props = params.get("properties", {})
    if "labels" not in props:
        return

    labels_schema = props["labels"]
    if labels_schema.get("type") != "array":
        return

    # Get or create items schema
    items_schema = labels_schema.get("items", {})

    # Update items.type to accept both string and integer
    current_type = items_schema.get("type")
    if current_type == "integer":
        # Change from "integer" to ["string", "integer"]
        items_schema["type"] = ["string", "integer"]
    elif current_type == "string":
        # Already accepts string; might as well add integer for completeness
        items_schema["type"] = ["string", "integer"]
    # If already a list, don't modify (could be already set)


def _resolve_ref(openapi_spec: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Resolve a $ref pointer in an OpenAPI spec.

    Args:
        openapi_spec: The OpenAPI specification dictionary
        ref: The $ref path (e.g., "#/components/responses/NotFound")

    Returns:
        The resolved component, or None if not found.
    """
    parts = ref.lstrip("#/").split("/")
    current: Any = openapi_spec
    for part in parts:
        current = current.get(part) if isinstance(current, dict) else None
        if current is None:
            return None
    return current


def _lookup_response_description(
    openapi_spec: dict[str, Any],
    route: Any,
    status_code: int,
) -> str:
    """Look up the response description for a given route and HTTP status code.

    Args:
        openapi_spec: The OpenAPI specification dictionary
        route: The OpenAPI route object (with path and method attributes)
        status_code: HTTP status code (e.g., 404, 422)

    Returns:
        The response description string, or a generic fallback if not found.
    """
    fallback = f"HTTP error {status_code}"
    result = fallback
    try:
        paths = openapi_spec.get("paths", {})
        path_item = paths.get(route.path)
        if not path_item:
            result = fallback
        else:
            method = getattr(route, "method", "").lower()
            operation = path_item.get(method) if method else None
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
                    resolved = _resolve_ref(openapi_spec, response_def["$ref"])
                    if isinstance(resolved, dict):
                        desc = resolved.get("description")
                        result = str(desc) if desc else fallback
    except (KeyError, TypeError, AttributeError, ValueError):
        result = fallback
    return result


def customize_component(  # noqa PLR0915
    route: Any,
    component: Any,
    label_manager: LabelManager,
    openapi_spec: dict[str, Any] | None = None,
) -> Tool | None:
    """Customize FastMCP components with tool annotations.

    This function is called by FastMCP's from_openapi for each generated component.
    It adds title, category, hints, invalidation patterns, and label handling.

     Args:
         route: The OpenAPI route object
         component: The generated FastMCP component (tool, resource, etc.)
         label_manager: LabelManager instance for label validation
         openapi_spec: Optional OpenAPI spec dictionary for enhanced error handling.
                      If provided, HTTP errors from component.run will be formatted
                      using the spec's response descriptions.

     Returns:
        A new Tool instance with customizations applied, or None if the component is not an OpenAPITool.
    """
    # Only customize OpenAPITool instances
    if not isinstance(component, OpenAPITool):
        return None

    # Generate title and category
    title = generate_tool_title(route)
    category = categorize_tool(route.path)

    # Prepare tags: original tags + category
    original_tags = set(component.tags) if component.tags else set()
    new_tags = original_tags | {category}

    # Prepare annotations: copy existing or create new
    if component.annotations is None:
        new_annotations = ToolAnnotations()
    elif isinstance(component.annotations, ToolAnnotations):
        new_annotations = component.annotations.model_copy()
    else:
        # Handle dict case - either it's a dict (unlikely) or unexpected type
        try:
            new_annotations = ToolAnnotations(**component.annotations)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            new_annotations = ToolAnnotations()

    # Set title in annotations
    new_annotations.title = title

    # Add inferred hints based on HTTP method
    add_inferred_hints(route, new_annotations)

    # Register cache invalidation patterns for write tools
    method = getattr(route, "method", None)
    if method:
        patterns = compute_invalidation_patterns(route.path, method)
        if patterns:
            register_tool_invalidation(component.name, patterns)

    # Prepare description
    description = getattr(component, "description", "") or ""
    # Add resource note for read-only tools
    if new_annotations.readOnlyHint and RESOURCE_NOTE not in description:
        description += RESOURCE_NOTE

    # Check if tool has labels parameter
    params = getattr(component, "parameters", None) or {}
    props = params.get("properties", {})
    has_labels = "labels" in props and props["labels"].get("type") == "array"
    if has_labels and LABEL_GUIDANCE.strip() not in description:
        description += LABEL_GUIDANCE

    # Mutate the component's parameters to augment schema and update labels schema.
    # This mutation is acceptable because the component will be wrapped and not used directly.
    augment_schema_with_validation(component)
    if has_labels:
        update_labels_schema(component)

    # Build transform function that combines validation and label conversion
    async def transform_fn(**kwargs: Any) -> Any:  # noqa PLR0912
        # Runtime validation
        for name, value in kwargs.items():
            if name in SINGLE_VALIDATORS:
                try:
                    SINGLE_VALIDATORS[name](value, field=name)
                except ValidationError:
                    raise
                except (TypeError, ValueError, KeyError) as e:
                    msg = f"Validation error for {name}: {e}"
                    _raise_validation_error(msg, name, e)
        if "page" in kwargs or "per_page" in kwargs:
            validate_pagination(kwargs.get("page"), kwargs.get("per_page"))

        # Label conversion
        if has_labels:
            labels = kwargs.get("labels", [])
            if labels and not all(isinstance(label, int) for label in labels):
                owner = kwargs.get("owner") or kwargs.get("org")
                repo = kwargs.get("repo")
                if owner and repo:
                    client = getattr(component, "_client", None)
                    if client is not None:
                        label_map = await label_manager.get_label_map(owner, repo, client)
                        converted = []
                        unknown = []
                        for label in labels:
                            if isinstance(label, str):
                                label_lower = label.lower()
                                if label_lower in label_map:
                                    converted.append(label_map[label_lower]["id"])
                                else:
                                    unknown.append(label)
                            else:
                                converted.append(label)
                        if unknown:
                            available = sorted(label_map.keys())
                            msg = (
                                f"Unknown label(s): {unknown}. "
                                f"Available labels: {', '.join(available)}. "
                                f"Use list_labels(owner, repo) or read gitea://repos/{owner}/{repo}/labels to see details."
                            )
                            _raise_value_error(msg)
                        kwargs = dict(kwargs)
                        kwargs["labels"] = converted
        # Enhanced error handling: format HTTP errors using OpenAPI response descriptions
        try:
            return await component.run(kwargs)
        except ValueError as e:
            # Check if this is an HTTP error from FastMCP's OpenAPITool
            cause = e.__cause__
            if isinstance(cause, httpx.HTTPStatusError) and openapi_spec is not None:
                status_code = cause.response.status_code
                # Look up response description from OpenAPI spec
                description = _lookup_response_description(openapi_spec, route, status_code)
                # Extract message from response body if available
                try:
                    error_body = cause.response.json()
                    message = error_body.get("message", "")
                    formatted = f"{description}\n\nDetails: {message}" if message else description
                except (ValueError, AttributeError):
                    # Fallback to text response
                    formatted = f"{description}\n\nDetails: {cause.response.text[:200]}"

                # Raise a new ValueError with formatted message, preserving the cause chain
                raise ValueError(formatted) from e
            # Not an HTTP error or no spec provided - re-raise unchanged
            raise
        except httpx.HTTPError as e:
            # Network errors, timeouts - these are NOT wrapped in ValueError by FastMCP
            formatted = f"Network error: Could not reach the Gitea server.\n\nDetails: {e!s}"
            _raise_value_error_from(formatted, e)
        except (KeyError, TypeError, AttributeError, RuntimeError):
            # Unexpected errors - log full traceback for debugging, but give user a clean message
            logger.exception("Unexpected error during tool execution")
            _raise_value_error(
                "An unexpected error occurred. Please check the server logs for details."
            )

    # Create transformed tool
    return Tool.from_tool(
        component,
        title=title,
        tags=new_tags,
        annotations=new_annotations,
        description=description,
        transform_fn=transform_fn,
    )


def _compact_search_serializer(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Return minimal tool info for search results to avoid massive payloads.

    Only includes name, description, and a simplified parameters schema.
    """
    result = []
    for tool in tools:
        # Simplify parameters: keep property names and basic types, drop detailed descriptions
        params = tool.parameters or {}
        if "properties" in params:
            simple_props = {}
            for name, info in params["properties"].items():
                if isinstance(info, dict):
                    simple_props[name] = {"type": info.get("type", "any")}
                else:
                    simple_props[name] = {"type": "any"}
            simple_params = {
                "properties": simple_props,
                "required": params.get("required", []),
            }
        else:
            simple_params = params

        result.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": simple_params,
            }
        )
    return result


class TolerantBM25SearchTransform(BM25SearchTransform):
    """BM25SearchTransform with tolerant argument handling for OpenCode compatibility.

    Override the synthetic call_tool to accept any arguments (including JSON strings).
    Also ensure internal catalog fetch bypasses middleware (like caching) to avoid stale results.
    Uses a compact result serializer to avoid massive payloads.
    """

    def __init__(self, **kwargs: Any) -> None:
        # Force our compact serializer if not provided
        if "search_result_serializer" not in kwargs:
            kwargs["search_result_serializer"] = _compact_search_serializer
        super().__init__(**kwargs)

    async def get_tool_catalog(
        self,
        ctx: Context,
        *,
        run_middleware: bool = True,  # noqa ARG002
    ) -> Sequence[Tool]:
        """Override to always bypass middleware when fetching the tool catalog."""
        # Force run_middleware=False to avoid cached synthetic results
        return await super().get_tool_catalog(ctx, run_middleware=False)

    def _make_call_tool(self) -> Tool:
        """Create the call_tool proxy that executes discovered tools."""
        transform = self

        async def call_tool(
            name: Annotated[str, "The name of the tool to call"],
            arguments: Annotated[Any, "Arguments to pass to the tool (dict or JSON string)"] = None,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Call a tool by name with the given arguments.

            Use this to execute tools discovered via search_tools.
            """
            if name in {transform._call_tool_name, transform._search_tool_name}:
                msg = f"'{name}' is a synthetic search tool and cannot be called via the call_tool proxy"
                _raise_value_error(msg)
            # If arguments is a string, attempt to parse as JSON
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as e:
                    msg = f"Invalid JSON in arguments: {e}"
                    _raise_value_error_from(msg, e)
            # Ensure arguments is a dict (or None)
            if arguments is not None and not isinstance(arguments, dict):
                msg = f"Arguments must be a dict or JSON string, got {type(arguments).__name__}"
                _raise_value_error(msg)
            return await ctx.fastmcp.call_tool(name, arguments)

        return Tool.from_function(fn=call_tool, name=self._call_tool_name)


__all__ = [
    "TolerantBM25SearchTransform",
    "add_inferred_hints",
    "categorize_tool",
    "compute_invalidation_patterns",
    "customize_component",
    "generate_tool_title",
    "update_labels_schema",
]
