"""Tool annotation and customization utilities.

This module contains functions for customizing FastMCP tools generated from OpenAPI,
including title generation, categorization, hint inference, label handling, and
cache invalidation pattern computation.
"""

import logging
from typing import Annotated, Any, Sequence

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


def categorize_tool(path: str) -> str:
    """Categorize a tool based on its OpenAPI path.

    Args:
        path: The OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")

    Returns:
        Category string: "repository", "issue", "pull_request", "user", "organization", "admin", or "misc"
    """
    # Admin paths
    if path.startswith("/admin"):
        return "admin"

    # Organization paths
    if path.startswith(("/orgs", "/org/")):
        return "organization"

    # User paths
    if path.startswith(("/user", "/users/")):
        return "user"

    # Issue paths
    if "/issues" in path or path.startswith("/issues"):
        return "issue"

    # Pull request paths
    if "/pulls" in path or path.startswith("/pulls"):
        return "pull_request"

    # Repository paths (most common)
    if path.startswith("/repos"):
        return "repository"

    # Everything else
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
    from gitea_mcp_server.constants import (
        PATTERN_FILES,
        PATTERN_ISSUES_CLOSED,
        PATTERN_ISSUES_LIST,
        PATTERN_ISSUES_OPEN,
        PATTERN_PULLS_CLOSED,
        PATTERN_PULLS_LIST,
        PATTERN_PULLS_OPEN,
        PATTERN_REPO,
    )

    # Only consider write methods (safe methods don't need invalidation)
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return []

    # Issue operations: any path that starts with /repos/{owner}/{repo}/issues
    if path.startswith("/repos/{owner}/{repo}/issues"):
        return [PATTERN_ISSUES_LIST, PATTERN_ISSUES_OPEN, PATTERN_ISSUES_CLOSED]

    # Pull request operations: starts with /repos/{owner}/{repo}/pulls
    if path.startswith("/repos/{owner}/{repo}/pulls"):
        return [PATTERN_PULLS_LIST, PATTERN_PULLS_OPEN, PATTERN_PULLS_CLOSED]

    # Repository direct edit: exactly /repos/{owner}/{repo} (e.g., repo_edit)
    if path == "/repos/{owner}/{repo}":
        return [PATTERN_REPO]

    # File contents: /repos/{owner}/{repo}/contents[...] (create, update, delete files)
    if path.startswith("/repos/{owner}/{repo}/contents"):
        return [PATTERN_FILES]

    # Label operations: affect both issues and PRs
    if path.startswith("/repos/{owner}/{repo}/labels"):
        return [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]

    # Milestone operations: affect both issues and PRs
    if path.startswith("/repos/{owner}/{repo}/milestones"):
        return [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]

    # Release operations: affect repository
    if path.startswith("/repos/{owner}/{repo}/releases"):
        return [PATTERN_REPO]

    # Topic operations: affect repository
    if path.startswith("/repos/{owner}/{repo}/topics"):
        return [PATTERN_REPO]

    # Add more as needed...
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


def customize_component(route: Any, component: Any, label_manager: LabelManager) -> Tool | None:
    """Customize FastMCP components with tool annotations.

    This function is called by FastMCP's from_openapi for each generated component.
    It adds title, category, hints, invalidation patterns, and label handling.

    Args:
        route: The OpenAPI route object
        component: The generated FastMCP component (tool, resource, etc.)
        label_manager: LabelManager instance for label validation

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
    elif isinstance(component.annotations, dict):
        new_annotations = ToolAnnotations(**component.annotations)
    else:
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
    description = component.__doc__ or ""
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
    async def transform_fn(**kwargs) -> Any:
        # Runtime validation
        for name, value in kwargs.items():
            if name in SINGLE_VALIDATORS:
                try:
                    SINGLE_VALIDATORS[name](value, field=name)
                except ValidationError:
                    raise
                except Exception as e:
                    raise ValidationError(f"Validation error for {name}: {e}", field=name) from e
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
                            raise ValueError(
                                f"Unknown label(s): {unknown}. "
                                f"Available labels: {', '.join(available)}. "
                                f"Use list_labels(owner, repo) or read gitea://repos/{owner}/{repo}/labels to see details."
                            )
                        kwargs = dict(kwargs)
                        kwargs["labels"] = converted
        return await component.run(kwargs)

    # Create transformed tool
    new_tool = Tool.from_tool(
        component,
        title=title,
        tags=new_tags,
        annotations=new_annotations,
        description=description,
        transform_fn=transform_fn,
    )
    return new_tool


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

    def __init__(self, **kwargs):
        # Force our compact serializer if not provided
        if "search_result_serializer" not in kwargs:
            kwargs["search_result_serializer"] = _compact_search_serializer
        super().__init__(**kwargs)

    async def get_tool_catalog(
        self, ctx: Context, *, run_middleware: bool = True
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
                raise ValueError(
                    f"'{name}' is a synthetic search tool and cannot be called via the call_tool proxy"
                )
            # If arguments is a string, attempt to parse as JSON
            if isinstance(arguments, str):
                try:
                    import json

                    arguments = json.loads(arguments)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON in arguments: {e}") from e
            # Ensure arguments is a dict (or None)
            if arguments is not None and not isinstance(arguments, dict):
                raise ValueError(
                    f"Arguments must be a dict or JSON string, got {type(arguments).__name__}"
                )
            return await ctx.fastmcp.call_tool(name, arguments)

        return Tool.from_function(fn=call_tool, name=self._call_tool_name)


__all__ = [
    "generate_tool_title",
    "categorize_tool",
    "add_inferred_hints",
    "compute_invalidation_patterns",
    "update_labels_schema",
    "customize_component",
    "TolerantBM25SearchTransform",
]
