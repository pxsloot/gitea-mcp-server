"""Core tool customization pipeline.

Immediate helpers for the customization pipeline (annotations, hint inference,
categorization, title generation, scope derivation, invalidation computation).
"""

from typing import Any

from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.constants import (
    HTTP_METHODS_DESTRUCTIVE,
    HTTP_METHODS_IDEMPOTENT,
    HTTP_METHODS_SAFE,
    LABEL_GUIDANCE,
    TITLE_TRUNCATE_LIMIT,
    TOOL_INVALIDATION_PATTERNS,
)
from gitea_mcp_server.tools.schemas import _schema_type_is_array

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


def generate_tool_title(route: Any) -> str:
    """Generate a human-readable title from the route's summary or operationId."""
    summary = getattr(route, "summary", None)
    operation_id = getattr(route, "operation_id", None)

    title: str

    if summary and summary.strip():
        title = str(summary).strip()
    elif operation_id:
        words = str(operation_id).replace("_", " ").title()
        title = words
    else:
        return "Unnamed Tool"

    if len(title) > TITLE_TRUNCATE_LIMIT:
        title = title[: TITLE_TRUNCATE_LIMIT - 3] + "..."

    return title


def categorize_tool(path: str) -> str:
    """Assign a category tag based on the request path prefix."""
    for prefix, category, contains in _CATEGORY_PREFIXES:
        if contains:
            if prefix in path:
                return category
        elif path.startswith(prefix):
            return category
    return "misc"


def add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
    """Set readOnly, destructive, idempotent, and openWorld hints from HTTP method."""
    method = getattr(route, "method", None)

    if annotations.readOnlyHint is None:
        annotations.readOnlyHint = method in HTTP_METHODS_SAFE

    if annotations.destructiveHint is None:
        annotations.destructiveHint = method in HTTP_METHODS_DESTRUCTIVE

    if annotations.idempotentHint is None:
        annotations.idempotentHint = method in HTTP_METHODS_IDEMPOTENT

    if annotations.openWorldHint is None:
        annotations.openWorldHint = True


def compute_invalidation_patterns(path: str, method: str) -> list[str]:
    """Return cache invalidation URI patterns for the given path and HTTP method."""
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return []

    for prefix, match_type, patterns in TOOL_INVALIDATION_PATTERNS:
        if match_type == "exact":
            if path == prefix:
                return patterns
        elif path.startswith(prefix):
            return patterns
    return []


def _prepare_annotations(component: Any, title: str) -> ToolAnnotations:
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


def _is_array_response(output_schema: dict[str, Any] | None) -> bool:
    if not output_schema or not isinstance(output_schema, dict):
        return False

    properties = output_schema.get("properties", {})
    if not isinstance(properties, dict):
        return False

    result_schema = properties.get("result")
    if not isinstance(result_schema, dict):
        return False

    return _schema_type_is_array(result_schema)


def _prepare_description(component: Any) -> tuple[str, bool]:
    description = getattr(component, "description", "") or ""

    params = getattr(component, "parameters", None) or {}
    props = params.get("properties", {})
    has_labels = "labels" in props and _schema_type_is_array(props["labels"])
    if has_labels and LABEL_GUIDANCE.strip() not in description:
        description += LABEL_GUIDANCE
    return description, has_labels


__all__ = [
    "add_inferred_hints",
    "categorize_tool",
    "compute_invalidation_patterns",
    "generate_tool_title",
]
