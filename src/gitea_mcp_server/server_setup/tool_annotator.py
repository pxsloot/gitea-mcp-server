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


def inject_label_validation_wrapper(label_manager: LabelManager, tool: OpenAPITool) -> Any:
    """Wrap a tool's run method to validate and convert label names to IDs.

    Creates a wrapper that intercepts calls to convert string labels to integer IDs
    based on the repository's label list. Replaces tool.run with the wrapper.

    Args:
        label_manager: LabelManager instance for fetching label maps
        tool: OpenAPITool to wrap (must have labels parameter if wrapping needed)

    Returns:
        The wrapped async run function (callable)
    """
    original_run = tool.run

    async def wrapped_run(arguments: dict[str, Any]) -> Any:
        # Only process if 'labels' parameter exists and contains strings
        labels = arguments.get("labels", [])
        if not labels or all(isinstance(label, int) for label in labels):
            return await original_run(arguments)

        # Extract owner and repo from arguments (required for label lookup)
        # These parameter names match the OpenAPI spec
        owner = arguments.get("owner") or arguments.get("org")
        repo = arguments.get("repo")
        if not owner or not repo:
            # Can't validate without repo context; pass through
            return await original_run(arguments)

        # Get label map from cache or fetch using label_manager
        # We need access to client; it's stored in tool._client for OpenAPITool
        client = getattr(tool, "_client", None)
        if client is None:
            # No client available; pass through (shouldn't happen in practice)
            return await original_run(arguments)

        label_map = await label_manager.get_label_map(owner, repo, client)

        # Convert labels: strings -> IDs, integers pass through
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

        # Call original with converted labels
        modified_args = dict(arguments)
        modified_args["labels"] = converted
        return await original_run(modified_args)

    tool.run = wrapped_run
    return wrapped_run


def maybe_wrap_labels(label_manager: LabelManager, component: OpenAPITool) -> None:
    """Apply label validation/conversion and description guidance if tool has 'labels' param.

    Args:
        label_manager: LabelManager instance for label lookups
        component: OpenAPITool to potentially wrap
    """
    # Check if tool has a 'labels' parameter in its schema
    params = getattr(component, "parameters", None)
    if not params:
        return

    props = params.get("properties", {})
    if "labels" not in props:
        return

    # Ensure labels is an array type (some tools might have it as something else)
    labels_schema = props["labels"]
    if labels_schema.get("type") != "array":
        return

    # Apply the validation/conversion wrapper
    inject_label_validation_wrapper(label_manager, component)

    # Enhance description with guidance
    existing_doc = component.__doc__ or ""
    if LABEL_GUIDANCE not in existing_doc:
        component.__doc__ = existing_doc + LABEL_GUIDANCE


def customize_component(route: Any, component: Any, label_manager: LabelManager) -> None:
    """Customize FastMCP components with tool annotations.

    This function is called by FastMCP's from_openapi for each generated component.
    It adds title, category, hints, invalidation patterns, and label handling.

    Args:
        route: The OpenAPI route object
        component: The generated FastMCP component (tool, resource, etc.)
        label_manager: LabelManager instance for label validation
    """
    # Only customize OpenAPITool instances
    if not isinstance(component, OpenAPITool):
        return

    # Generate and set title annotation
    title = generate_tool_title(route)
    category = categorize_tool(route.path)

    # Create or update annotations
    if component.annotations is None:
        component.annotations = ToolAnnotations()
    elif isinstance(component.annotations, dict):  # type: ignore
        # Convert dict to ToolAnnotations while preserving existing fields
        existing = component.annotations  # type: ignore
        component.annotations = ToolAnnotations(**existing)

    # Set title
    component.annotations.title = title

    # Add inferred annotation hints based on HTTP method
    add_inferred_hints(route, component.annotations)

    # Register cache invalidation patterns for write tools
    method = getattr(route, "method", None)
    if method:
        patterns = compute_invalidation_patterns(route.path, method)
        if patterns:
            register_tool_invalidation(component.name, patterns)

    # Add category to tags (used for grouping in MCP clients)
    if component.tags is None:
        component.tags = set()  # type: ignore[unreachable]
    component.tags.add(category)

    # For read-only tools, add a note encouraging use of resources
    if component.annotations and component.annotations.readOnlyHint:
        existing_doc = component.__doc__ or ""
        # Avoid adding the note twice
        if RESOURCE_NOTE not in existing_doc:
            component.__doc__ = existing_doc + RESOURCE_NOTE

    # Apply label validation/conversion to tools that have a 'labels' parameter
    maybe_wrap_labels(label_manager, component)

    # Also update the tool's parameter schema to reflect that string labels are accepted
    update_labels_schema(component)


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
    "inject_label_validation_wrapper",
    "maybe_wrap_labels",
    "customize_component",
    "TolerantBM25SearchTransform",
]
