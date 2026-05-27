"""Tool annotation and customization utilities.

This module contains functions for customizing FastMCP tools generated from OpenAPI,
including title generation, categorization, hint inference, label handling, and
cache invalidation pattern computation.
"""

import json
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

if TYPE_CHECKING:
    from gitea_mcp_server.client import GiteaClient

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
    PATTERN_ISSUES_LIST,
    PATTERN_PULLS_LIST,
    PATTERN_REPO,
    TITLE_TRUNCATE_LIMIT,
)
from gitea_mcp_server.server_setup.bm25_search import TolerantBM25Search
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.validation import (
    SINGLE_VALIDATORS,
    ValidationError,
    augment_schema_with_validation,
    validate_pagination,
)

logger = logging.getLogger(__name__)


def _raise_value_error(message: str) -> NoReturn:
    """Raise ValueError with pre-computed message."""
    raise ValueError(message) from None


def _raise_value_error_from(message: str, cause: Exception) -> NoReturn:
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


TAG_TO_SCOPE: dict[str, str] = {
    "admin": "sudo",
    "repository": "repository",
    "issue": "issue",
    "organization": "organization",
    "user": "user",
    "notification": "notification",
    "package": "package",
    "activitypub": "activitypub",
    "miscellaneous": "misc",
    "settings": "repository",
}


def derive_required_scope(swagger_tags: set[str] | None, method: str | None) -> str | None:
    """Derive the required Gitea token scope from Swagger tags and HTTP method.

    Args:
        swagger_tags: Set of Swagger tag names from the OpenAPI spec
        method: HTTP method (GET, POST, PUT, DELETE, etc.)

    Returns:
        Scope string like "read:repository" or "write:issue",
        "sudo" for admin tools, or None if no scope is needed.
    """
    if not swagger_tags:
        return None

    scope_name = None
    for tag in swagger_tags:
        s = TAG_TO_SCOPE.get(tag)
        if s is not None:
            scope_name = s
            break

    if scope_name is None:
        return None

    if scope_name == "sudo":
        return "sudo"

    if method and method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return f"read:{scope_name}"
    return f"write:{scope_name}"


_CATEGORY_PREFIXES: list[tuple[str, str, bool]] = [
    ("/admin", "admin", False),
    ("/orgs", "organization", False),
    ("/org/", "organization", False),
    ("/user", "user", False),
    ("/users/", "user", False),
    ("/repos/{owner}/{repo}/issues", "issue", False),
    ("/repos/{owner}/{repo}/pulls", "pull_request", False),
    ("/issues", "issue", True),
    ("/pulls", "pull_request", True),
    ("/repos", "repository", False),
]


def categorize_tool(path: str) -> str:
    """Categorize a tool based on its OpenAPI path.

    Args:
        path: The OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")

    Returns:
        Category string: "repository", "issue", "pull_request", "user", "organization", "admin", or "misc"
    """
    for prefix, category, contains in _CATEGORY_PREFIXES:
        if contains:
            if prefix in path:
                return category
        elif path.startswith(prefix):
            return category
    return "misc"


def add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
    """Infer and add annotation hints from HTTP route properties.

    Hints are based on HTTP method semantics. The mapping follows the
    constants in ``gitea_mcp_server.constants``:

    +------------------+-----------------------------------+--------------------+
    | Annotation       | True when method in               | Constants          |
    +------------------+-----------------------------------+--------------------+
    | readOnlyHint     | HTTP_METHODS_SAFE                 | GET, HEAD, OPTIONS |
    | destructiveHint  | HTTP_METHODS_DESTRUCTIVE          | DELETE             |
    | idempotentHint   | HTTP_METHODS_IDEMPOTENT           | GET, PUT, DELETE,  |
    |                  |                                   | HEAD, OPTIONS      |
    | openWorldHint    | Always True                       | —                  |
    +------------------+-----------------------------------+--------------------+

    - ``readOnlyHint`` — tool only reads data, no side effects.
    - ``destructiveHint`` — tool can destroy or delete data.
    - ``idempotentHint`` — calling the tool multiple times with the same
      parameters has the same effect as calling it once.
    - ``openWorldHint`` — tool interacts with an external Gitea server.

    **Override behavior**: Existing annotation values (set via
    ``mcp_extensions.yaml`` or manual ``ToolAnnotations``) are preserved.
    Inference only sets a hint if its current value is ``None``.

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
        [PATTERN_ISSUES_LIST],
    ),
    (
        "/repos/{owner}/{repo}/pulls",
        None,
        [PATTERN_PULLS_LIST],
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
    if not _schema_type_is_array(labels_schema):
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
    current: dict[str, Any] | None = openapi_spec
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _deep_resolve_schema(
    schema: Any,
    openapi_spec: dict[str, Any],
    _seen: set[str] | None = None,
) -> dict[str, Any]:
    """Recursively resolve all ``$ref`` pointers in a schema.

    Walks the schema tree resolving ``$ref`` at every level:
    - Top-level ``$ref``
    - ``$ref`` inside ``properties`` values
    - ``$ref`` in ``items`` (array types)
    - ``$ref`` inside ``allOf``, ``oneOf``, ``anyOf`` entries
    - ``$ref`` in ``additionalProperties``

    Circular references are preserved as ``$ref`` strings to avoid
    infinite recursion.

    Args:
        schema: The JSON schema to resolve (will be deep-copied)
        openapi_spec: The full OpenAPI spec for lookups
        _seen: Internal set of already-visited ``$ref`` paths (for cycle detection)

    Returns:
        A new schema dict with all ``$ref`` pointers resolved.
    """
    if not isinstance(schema, dict):
        return {}
    result: dict[str, Any] = {}
    _seen = _seen or set()

    for key, value in schema.items():
        if key == "$ref" and isinstance(value, str):
            if value in _seen:
                result[key] = value
                continue
            _seen.add(value)
            resolved = _resolve_ref(openapi_spec, value)
            if isinstance(resolved, dict):
                deep = _deep_resolve_schema(resolved, openapi_spec, _seen)
                result.update(deep)
            else:
                result[key] = value
        elif key in ("properties",):
            result[key] = {
                k: _deep_resolve_schema(v, openapi_spec, _seen) if isinstance(v, dict) else v
                for k, v in value.items()
            }
        elif key in ("items", "additionalProperties"):
            result[key] = _deep_resolve_schema(value, openapi_spec, _seen) if isinstance(value, dict) else value
        elif key in ("allOf", "oneOf", "anyOf"):
            result[key] = [
                _deep_resolve_schema(item, openapi_spec, _seen) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, dict):
            result[key] = _deep_resolve_schema(value, openapi_spec, _seen)
        else:
            result[key] = value

    return result


def _get_success_schema(
    openapi_spec: dict[str, Any],
    path: str,
    method: str,
) -> dict[str, Any] | None:
    """Extract the success response schema for a path/method from the OpenAPI spec.

    Tries ``200`` then ``201`` status codes and resolves ``$ref`` chains.
    """
    paths = openapi_spec.get("paths", {})
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return None
    operation = path_item.get(method)
    if not isinstance(operation, dict):
        return None
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return None

    for code in ("200", "201"):
        response = responses.get(code)
        if not isinstance(response, dict):
            continue

        if "$ref" in response:
            resolved = _resolve_ref(openapi_spec, response["$ref"])
            if not isinstance(resolved, dict):
                continue
            response = resolved

        content = response.get("content", {})
        if not isinstance(content, dict):
            continue
        json_content = content.get("application/json", {})
        if not isinstance(json_content, dict):
            continue
        schema = json_content.get("schema")
        if not isinstance(schema, dict):
            continue

        return _deep_resolve_schema(schema, openapi_spec)

    return None


def derive_output_schema(
    route: Any,
    openapi_spec: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract the output JSON Schema for a tool from the OpenAPI spec.

    Looks up the success response (200 or 201) for the tool's route in the
    OpenAPI spec and resolves ``$ref`` chains to produce a complete schema.

    Args:
        route: Route object with ``path`` and ``method`` attributes
        openapi_spec: The OpenAPI v3 specification dictionary, or ``None``

    Returns:
        JSON Schema dict for the response body, or ``None`` if unavailable.
    """
    if openapi_spec is None:
        return None

    method = getattr(route, "method", "").lower()
    return _get_success_schema(openapi_spec, route.path, method)


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


def _run_validation(
    kwargs: dict[str, Any],
    required_params: list[str] | None = None,
) -> None:
    """Run runtime validation on tool arguments.

    Checks both present arguments (format validation) and missing
    required arguments (before they reach the Gitea API as a 404).
    """
    missing = [p for p in (required_params or []) if p not in kwargs]
    if missing:
        msg = f"Missing required parameter(s): {', '.join(missing)}"
        _raise_validation_error(msg, missing[0], ValueError(msg))
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


def _schema_type_is_array(schema: dict[str, Any]) -> bool:
    """Check if a schema type is 'array', handling both string and list forms.

    OpenAPI 3.1 represents nullable types as lists (e.g. ``["array", "null"]``),
    while non-nullable types are plain strings (e.g. ``"array"``).
    """
    t = schema.get("type")
    if isinstance(t, str):
        return t == "array"
    if isinstance(t, list):
        return "array" in t
    return False


def _format_available_labels(label_names: list[str]) -> str:
    """Group label names by prefix and format for readable agent display."""
    groups: dict[str, list[str]] = {}
    for name in label_names:
        prefix = name.split("/", 1)[0] if "/" in name else ""
        groups.setdefault(prefix, []).append(name)

    lines: list[str] = []
    for prefix in sorted(groups, key=lambda p: (p == "", p)):
        label_list = sorted(groups[prefix])
        lines.append(f"  - {', '.join(label_list)}")
    return "\n".join(lines)


async def _convert_labels(
    kwargs: dict[str, Any],
    has_labels: bool,
    label_manager: LabelManager,
    gitea_client: "GiteaClient | None" = None,
) -> None:
    """Convert label names to IDs if needed."""
    if not has_labels:
        return
    labels = kwargs.get("labels")
    if not labels or all(isinstance(label, int) for label in labels):
        return

    owner = kwargs.get("owner") or kwargs.get("org")
    repo = kwargs.get("repo")
    if not owner or not repo:
        return

    if gitea_client is None:
        return

    label_map = await label_manager.get_label_map(owner, repo, gitea_client)
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
        available = sorted(v["name"] for v in label_map.values())
        formatted = _format_available_labels(available)
        msg = (
            f"Unknown label(s): {unknown}.\n\n"
            f"Available labels for {owner}/{repo}:\n"
            f"{formatted}\n\n"
            f"Use list_labels({owner}, {repo}) or read "
            f"gitea://repos/{owner}/{repo}/labels to see details."
        )
        raise ValidationError(message=msg, field="labels")

    kwargs["labels"] = converted


async def _run_with_error_handling(
    kwargs: dict[str, Any],
    component: Any,
    route: Any,
    openapi_spec: dict[str, Any] | None,
) -> Any:
    """Run the component with enhanced error handling."""
    try:
        return await component.run(kwargs)
    except ValueError as e:
        cause = e.__cause__
        if isinstance(cause, httpx.HTTPStatusError) and openapi_spec is not None:
            status_code = cause.response.status_code
            description = _lookup_response_description(openapi_spec, route, status_code)
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


def _prepare_annotations(component: Any, title: str) -> ToolAnnotations:
    """Prepare tool annotations from component."""
    if component.annotations is None:
        new_annotations = ToolAnnotations()
    elif isinstance(component.annotations, ToolAnnotations):
        new_annotations = component.annotations.model_copy()
    else:
        try:
            new_annotations = ToolAnnotations(**component.annotations)
        except (TypeError, ValueError):
            new_annotations = ToolAnnotations()
    new_annotations.title = title
    return new_annotations


def _prepare_description(component: Any) -> tuple[str, bool]:
    """Prepare tool description and return has_labels flag."""
    description = getattr(component, "description", "") or ""

    params = getattr(component, "parameters", None) or {}
    props = params.get("properties", {})
    has_labels = "labels" in props and _schema_type_is_array(props["labels"])
    if has_labels and LABEL_GUIDANCE.strip() not in description:
        description += LABEL_GUIDANCE
    return description, has_labels


def customize_component(
    route: Any,
    component: Any,
    label_manager: LabelManager,
    openapi_spec: dict[str, Any] | None = None,
    gitea_client: "GiteaClient | None" = None,
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
         gitea_client: Optional GiteaClient for label name resolution.
                      Required for string-to-ID label conversion.

     Returns:
         A new Tool instance with customizations applied, or None if the component is not an OpenAPITool.
    """
    if not isinstance(component, OpenAPITool):
        return None

    title = generate_tool_title(route)
    category = categorize_tool(route.path)

    tags = (set(component.tags) if component.tags else set()) | {category}

    annotations = _prepare_annotations(component, title)
    add_inferred_hints(route, annotations)

    method = getattr(route, "method", None)
    if method:
        patterns = compute_invalidation_patterns(route.path, method)
        if patterns:
            register_tool_invalidation(component.name, patterns)

    required_scope = derive_required_scope(
        set(component.tags) if component.tags else None,
        method,
    )

    description, has_labels = _prepare_description(component)

    output_schema = derive_output_schema(route, openapi_spec)

    augment_schema_with_validation(component)
    if has_labels:
        update_labels_schema(component)

    async def transform_fn(**kwargs: Any) -> Any:
        _run_validation(kwargs, component.parameters.get("required"))
        await _convert_labels(kwargs, has_labels, label_manager, gitea_client)
        return await _run_with_error_handling(kwargs, component, route, openapi_spec)

    # Set x-fastmcp-wrap-result on the inner OpenAPITool so its run()
    # wraps all response types (not just non-dict) in {"result": ...}.
    # The TransformedTool then passes through the ToolResult unchanged.
    if component.output_schema is not None:
        component.output_schema["x-fastmcp-wrap-result"] = True

    if output_schema is not None:
        output_schema["x-fastmcp-wrap-result"] = True

    component_meta = dict(component.meta) if component.meta else {}
    component_meta.setdefault("fastmcp", {}).setdefault("_internal", {})[
        "required_scope"
    ] = required_scope

    return Tool.from_tool(
        component,
        title=title,
        tags=tags,
        annotations=annotations,
        description=description,
        transform_fn=transform_fn,
        output_schema=output_schema,
        meta=component_meta,
    )


def _compact_search_serializer(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Return minimal tool info for search results.

    Only includes name and description. Full schemas are available
    via the tool_info tool and the gitea://tool/{name}/schema resource.
    """
    result = []
    for tool in tools:
        item: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description or "",
        }
        result.append(item)
    return result


def _example_object(
    schema: dict[str, Any],
    depth: int,
    max_depth: int,
    max_properties: int,
) -> dict[str, Any]:
    """Generate an example for an object schema."""
    if depth >= max_depth:
        return {}
    properties = schema.get("properties", {})
    if not properties:
        return {}
    example: dict[str, Any] = {}
    for prop_name in list(properties.keys())[:max_properties]:
        prop_schema = properties[prop_name]
        example[prop_name] = _schema_to_example(
            prop_schema if isinstance(prop_schema, dict) else {},
            depth + 1,
            max_depth,
            max_properties,
        )
    return example


def _example_array(
    schema: dict[str, Any],
    depth: int,
    max_depth: int,
    max_properties: int,
) -> list[Any]:
    """Generate an example for an array schema."""
    items = schema.get("items", {})
    if isinstance(items, dict) and items:
        return [_schema_to_example(items, depth, max_depth, max_properties)]
    return []


def _example_string(schema: dict[str, Any]) -> str:
    """Generate an example for a string schema."""
    fmt = schema.get("format")
    if fmt == "date-time":
        return "2024-01-01T00:00:00Z"
    if fmt == "email":
        return "user@example.com"
    if fmt == "uri":
        return "https://example.com"
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        return str(enum_vals[0])
    return "text"


def _schema_to_example(  # noqa: PLR0911, PLR0912 -- type-dispatch inherently has many returns/branches
    schema: dict[str, Any],
    depth: int = 0,
    max_depth: int = 3,
    max_properties: int = 15,
) -> Any:
    """Generate a compact example value from a JSON Schema."""
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict) and opt.get("type") != "null":
                    return _schema_to_example(opt, depth, max_depth, max_properties)

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for t in schema_type:
            if t != "null":
                schema_type = t
                break
        else:
            schema_type = "null"

    if "example" in schema:
        return schema["example"]

    if schema_type == "object":
        return _example_object(schema, depth, max_depth, max_properties)
    if schema_type == "array":
        return _example_array(schema, depth, max_depth, max_properties)
    if schema_type == "string":
        return _example_string(schema)
    if schema_type in ("integer", "number", "boolean", "null"):
        return {"integer": 0, "number": 0.0, "boolean": True, "null": None}[schema_type]
    return None


def _serialize_tool_schema(tool: Tool) -> dict[str, Any]:
    """Serialize a tool's full schema for tool_info responses."""
    data: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.parameters,
    }
    if tool.output_schema is not None:
        inner = tool.output_schema.get("properties", {}).get("result", {})
        data["output_example"] = _schema_to_example(inner)
    if tool.annotations:
        ann = tool.annotations
        data["annotations"] = {
            k: getattr(ann, k)
            for k in ("title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
            if getattr(ann, k, None) is not None
        }
    if tool.tags:
        data["tags"] = list(tool.tags)
    if tool.version:
        data["version"] = tool.version
    return data


class TolerantSearchTransform(BM25SearchTransform):
    """Search transform with tolerant tool discovery, call_tool proxy, and tool_info.

    Extends BM25SearchTransform with:
    - Tolerant argument handling (JSON string parsing)
    - Compact search results (name + description only)
    - tool_info synthetic tool for retrieving full tool schemas
    - Enhanced BM25 search with alias expansion and 2-char token support
    """

    def __init__(self, **kwargs: Any) -> None:
        if "search_result_serializer" not in kwargs:
            kwargs["search_result_serializer"] = _compact_search_serializer
        self._tool_info_name = kwargs.pop("tool_info_name", "tool_info")
        super().__init__(**kwargs)
        self._searcher = TolerantBM25Search()

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Extend base transform to include tool_info synthetic tool."""
        pinned = [t for t in tools if t.name in self._always_visible]
        return [*pinned, self._make_search_tool(), self._make_call_tool(), self._make_tool_info_tool()]

    async def get_tool(
        self, name: str, call_next: Any, *, version: Any = None
    ) -> Tool | None:
        """Intercept tool_info name; delegate everything else."""
        if name == self._tool_info_name:
            return self._make_tool_info_tool()
        return await super().get_tool(name, call_next, version=version)

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        """Delegate search to TolerantBM25Search."""
        return self._searcher.search(tools, query, self._max_results)

    def _make_search_tool(self) -> Tool:
        """Create the search_tool with minimal results (name + description only)."""
        transform = self

        async def search_tools(
            query: Annotated[str, "Natural language query to search for tools"],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Search for tools by name or description.

            Returns compact results (name + description only). Use this for
            lightweight discovery — find what tools are available and what they do.

            When you need full parameter details, an output example, or annotations
            for a specific tool, use tool_info with the exact tool name.
            """
            assert ctx is not None
            hidden = await transform._get_visible_tools(ctx)
            results = await transform._search(hidden, query)
            rendered = await transform._render_results(results)
            return ToolResult(structured_content={"result": rendered})

        return Tool.from_function(
            fn=search_tools,
            name=self._search_tool_name,
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                            },
                        },
                        "description": "Matching tool definitions (name + description only)",
                    },
                },
            },
        )

    def _make_call_tool(self) -> Tool:
        """Create the call_tool proxy that executes discovered tools."""
        transform = self

        async def call_tool(
            name: Annotated[str, "The name of the tool to call"],
            arguments: Annotated[Any, "Arguments to pass to the tool (dict or JSON string)"] = None,
            ctx: Context | None = None,
        ) -> ToolResult:
            """Call a tool by name with the given arguments.

            Use this to execute tools discovered via search_tools.
            """
            if name in {transform._call_tool_name, transform._search_tool_name, transform._tool_info_name}:
                msg = f"'{name}' is a synthetic search tool and cannot be called via the call_tool proxy"
                _raise_value_error(msg)
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as e:
                    msg = f"Invalid JSON in arguments: {e}"
                    _raise_value_error_from(msg, e)
            if arguments is not None and not isinstance(arguments, dict):
                msg = f"Arguments must be a dict or JSON string, got {type(arguments).__name__}"
                _raise_value_error(msg)
            assert ctx is not None
            return await ctx.fastmcp.call_tool(name, arguments)

        return Tool.from_function(
            fn=call_tool,
            name=self._call_tool_name,
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "description": "Result of the tool call, wrapped in result for consistency",
                    },
                },
            },
        )

    def _make_tool_info_tool(self) -> Tool:
        """Create the tool_info tool that returns full schema for a named tool."""
        transform = self

        async def tool_info(
            name: Annotated[str, "The exact name of the tool to inspect"],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Get the full schema for a tool by name.

            Returns the complete input parameters, an output example, annotations,
            tags, and version for a specific tool.

            Typical workflow:
            1. search_tools — discover what tools are available (name + description)
            2. tool_info — get full parameters and output example for specific tools
            3. call_tool — execute the tool with proper arguments
            """
            assert ctx is not None
            tools = await transform.get_tool_catalog(ctx)
            for tool in tools:
                if tool.name == name:
                    return ToolResult(structured_content={"result": _serialize_tool_schema(tool)})
            msg = f"Tool '{name}' not found"
            _raise_value_error(msg)

        return Tool.from_function(
            fn=tool_info,
            name=self._tool_info_name,
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "parameters": {"type": "object"},
                            "output_example": {"description": "Example return value (may be object, array, etc.)"},
                            "annotations": {"type": "object"},
                            "tags": {"type": "array"},
                            "version": {"type": "string"},
                        },
                        "description": "Full tool schema",
                    },
                },
            },
        )


__all__ = [
    "TAG_TO_SCOPE",
    "TolerantSearchTransform",
    "add_inferred_hints",
    "categorize_tool",
    "compute_invalidation_patterns",
    "customize_component",
    "derive_output_schema",
    "derive_required_scope",
    "generate_tool_title",
    "update_labels_schema",
]
