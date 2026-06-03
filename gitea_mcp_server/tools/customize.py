"""Core tool customization pipeline.

Contains customize_component and its immediate helpers (annotations, hint inference,
categorization, title generation, scope derivation, invalidation computation).
"""

from typing import TYPE_CHECKING, Any

from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations
from mcp.types import TextContent

from gitea_mcp_server.cache_invalidation import register_tool_invalidation
from gitea_mcp_server.constants import (
    HTTP_METHODS_DESTRUCTIVE,
    HTTP_METHODS_IDEMPOTENT,
    HTTP_METHODS_SAFE,
    LABEL_GUIDANCE,
    TITLE_TRUNCATE_LIMIT,
    TOOL_INVALIDATION_PATTERNS,
)
from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.pagination import pagination_ctx
from gitea_mcp_server.resources.scope import derive_required_scope
from gitea_mcp_server.tools.errors import _run_validation, _run_with_error_handling
from gitea_mcp_server.tools.labels import _convert_labels, update_labels_schema
from gitea_mcp_server.tools.schemas import (
    _is_text_response,
    _schema_type_is_array,
    derive_output_schema,
)
from gitea_mcp_server.validation import augment_schema_with_validation

if TYPE_CHECKING:
    from gitea_mcp_server.client import GiteaClient

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
    for prefix, category, contains in _CATEGORY_PREFIXES:
        if contains:
            if prefix in path:
                return category
        elif path.startswith(prefix):
            return category
    return "misc"


def add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
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


def customize_component(
    route: Any,
    component: Any,
    label_manager: LabelManager,
    openapi_spec: dict[str, Any] | None = None,
    gitea_client: "GiteaClient | None" = None,
) -> Tool | None:
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

    is_text_response = (
        openapi_spec is not None
        and _is_text_response(openapi_spec, getattr(route, "path", ""), getattr(route, "method", "").lower())
    )

    async def transform_fn(**kwargs: Any) -> Any:
        _run_validation(kwargs, component.parameters.get("required"))
        await _convert_labels(kwargs, has_labels, label_manager, gitea_client)
        result = await _run_with_error_handling(kwargs, component, route, openapi_spec)

        if is_text_response and isinstance(result, ToolResult) and result.structured_content is None:
            text = next(
                (c.text for c in result.content if isinstance(c, TextContent)),
                "",
            )
            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"result": text},
            )

        if _is_array_response(output_schema) and isinstance(result, ToolResult) and result.structured_content is not None:
            result_data = result.structured_content.get("result")
            if isinstance(result_data, list):
                page = kwargs.get("page", 1)
                per_page = kwargs.get("per_page") or kwargs.get("limit", 100)

                has_more = len(result_data) == per_page if per_page else False
                next_offset = page + 1 if has_more else None

                enhanced_result = dict(result.structured_content)
                enhanced_result["has_more"] = has_more
                enhanced_result["next_offset"] = next_offset
                enhanced_result["total_count"] = pagination_ctx.get().get("total_count")

                return ToolResult(
                    content=[TextContent(type="text", text=str(enhanced_result))],
                    structured_content=enhanced_result
                )

        return result

    if component.output_schema is not None:
        component.output_schema["x-fastmcp-wrap-result"] = True

    if output_schema is not None:
        output_schema["x-fastmcp-wrap-result"] = True

        if _is_array_response(output_schema):
            props = output_schema.setdefault("properties", {})
            props["has_more"] = {
                "type": "boolean",
                "description": "Whether more pages exist",
            }
            props["next_offset"] = {
                "type": "integer",
                "description": "Page number for next page, if any",
            }
            props["total_count"] = {
                "type": "integer",
                "description": "Total item count from server, if available",
            }

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


__all__ = [
    "add_inferred_hints",
    "categorize_tool",
    "compute_invalidation_patterns",
    "customize_component",
    "generate_tool_title",
]
