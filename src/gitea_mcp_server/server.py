"""Gitea MCP Server implementation."""

import asyncio
import contextlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Sequence

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    GetPromptSettings,
    ListResourcesSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)

# Import from new location (FastMCP 3.x)
from fastmcp.server.providers.openapi import OpenAPIProvider, OpenAPITool
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server import resources
from gitea_mcp_server.cache_invalidation import (
    CacheInvalidationMiddleware,
    register_tool_invalidation,
)
from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.config import Config
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.logging_config import setup_logging
from gitea_mcp_server.mcp_tools import register_mcp_resource_tools
from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3
from gitea_mcp_server.tool_filter import filter_tools_by_permissions

logger = logging.getLogger(__name__)

# Constants for title truncation
_TITLE_TRUNCATE_LIMIT = 50

# Label cache for validation (maps (owner, repo) -> {"map": dict, "timestamp": datetime})
_label_cache: dict[tuple[str, str], dict[str, Any]] = {}
_LABEL_CACHE_TTL = 300  # seconds (5 minutes)


async def _get_repository_label_map(
    owner: str, repo: str, client: GiteaClient
) -> dict[str, dict[str, Any]]:
    """Fetch and cache repository label map (name.lower() -> label_info).

    Args:
        owner: Repository owner
        repo: Repository name
        client: GiteaClient for API calls

    Returns:
        Dict mapping lowercase label names to label info dicts (id, name)
    """
    cache_key = (owner, repo)
    now = datetime.now()

    # Check cache
    if cache_key in _label_cache:
        entry = _label_cache[cache_key]
        if (now - entry["timestamp"]).total_seconds() < _LABEL_CACHE_TTL:
            return entry["map"]

    # Fetch labels from API
    labels = await client.request("GET", f"/repos/{owner}/{repo}/labels")
    # Response is a list of label objects: {id, name, color, description, ...}
    label_map = {}
    for label in labels:
        name = label.get("name", "")
        if name:
            label_map[name.lower()] = {"id": label["id"], "name": label["name"]}

    # Update cache
    _label_cache[cache_key] = {"map": label_map, "timestamp": now}
    return label_map


def _inject_label_validation_wrapper(tool: OpenAPITool) -> Any:
    """Wrap a tool's run method to validate and convert label names to IDs.

    Creates a wrapper that intercepts calls to convert string labels to integer IDs
    based on the repository's label list. Replaces tool.run with the wrapper.

    Args:
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

        # Get label map from cache or fetch
        # We need access to client; it's stored in tool._client for OpenAPITool
        client = getattr(tool, "_client", None)
        if client is None:
            # No client available; pass through (shouldn't happen in practice)
            return await original_run(arguments)

        label_map = await _get_repository_label_map(owner, repo, client)

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


def _maybe_wrap_labels(component: OpenAPITool) -> None:
    """Apply label validation/conversion and description guidance if tool has 'labels' param.

    Args:
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
    _inject_label_validation_wrapper(component)

    # Enhance description with guidance
    label_guidance = (
        "\n\n**Labels**: You may provide existing label names (strings) or IDs (integers). "
        "Call `list_labels(owner, repo)` or read `gitea://repos/{owner}/{repo}/labels` "
        "to see available labels. Unknown label names will produce an error."
    )
    existing_doc = component.__doc__ or ""
    if label_guidance not in existing_doc:
        component.__doc__ = existing_doc + label_guidance


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


# Custom BM25SearchTransform that tolerates string arguments from OpenCode wrapper
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


def _categorize_tool(path: str) -> str:  # noqa: PLR0911
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


def _generate_tool_title(route: Any) -> str:
    """Generate a human-readable title for a tool from its OpenAPI route metadata.

    Args:
        route: FastMCP route object with summary and operation_id attributes

    Returns:
        Title string (max 50 chars, truncated with "..." if needed)
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

    # Truncate to _TITLE_TRUNCATE_LIMIT characters
    if len(title) > _TITLE_TRUNCATE_LIMIT:
        title = title[: _TITLE_TRUNCATE_LIMIT - 3] + "..."

    return title


def _add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
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

    # Define method sets based on HTTP semantics
    safe_methods = {"GET", "HEAD", "OPTIONS"}
    destructive_methods = {"DELETE"}
    idempotent_methods = {"GET", "PUT", "DELETE", "HEAD", "OPTIONS"}

    # Only add hints if they are currently None (preserve existing manual settings)
    if annotations.readOnlyHint is None:
        annotations.readOnlyHint = method in safe_methods

    if annotations.destructiveHint is None:
        annotations.destructiveHint = method in destructive_methods

    if annotations.idempotentHint is None:
        annotations.idempotentHint = method in idempotent_methods

    if annotations.openWorldHint is None:
        # All Gitea MCP tools interact with external Gitea server
        annotations.openWorldHint = True


def _compute_tool_invalidation_patterns(path: str, method: str) -> list[str]:
    """Compute resource invalidation patterns for a tool based on its path and method.

    This function analyzes the OpenAPI path and HTTP method to determine which
    MCP resource patterns should be invalidated when this tool is called.

    Args:
        path: OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)

    Returns:
        List of pattern names (keys in RESOURCE_URI_PATTERNS) to invalidate.
        Empty list if no invalidation needed.
    """
    # Only consider write methods (safe methods don't need invalidation)
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return []

    # Issue operations: any path that starts with /repos/{owner}/{repo}/issues
    if path.startswith("/repos/{owner}/{repo}/issues"):
        return ["issues_list", "issues_open", "issues_closed"]

    # Pull request operations: starts with /repos/{owner}/{repo}/pulls
    if path.startswith("/repos/{owner}/{repo}/pulls"):
        return ["pulls_list", "pulls_open", "pulls_closed"]

    # Repository direct edit: exactly /repos/{owner}/{repo} (e.g., repo_edit)
    if path == "/repos/{owner}/{repo}":
        return ["repo"]

    # File contents: /repos/{owner}/{repo}/contents[...] (create, update, delete files)
    if path.startswith("/repos/{owner}/{repo}/contents"):
        return ["files"]

    # Label operations: affect both issues and PRs
    if path.startswith("/repos/{owner}/{repo}/labels"):
        return ["issues_list", "pulls_list"]

    # Milestone operations: affect both issues and PRs
    if path.startswith("/repos/{owner}/{repo}/milestones"):
        return ["issues_list", "pulls_list"]

    # Release operations: affect repository
    if path.startswith("/repos/{owner}/{repo}/releases"):
        return ["repo"]

    # Topic operations: affect repository
    if path.startswith("/repos/{owner}/{repo}/topics"):
        return ["repo"]

    # Add more as needed...
    return []


def _customize_component(route: Any, component: Any) -> None:
    """Customize FastMCP components with tool annotations.

    This function is called by FastMCP's from_openapi for each generated component.
    It adds title and category annotations to tools and tags for categorization.

    Args:
        route: The OpenAPI route object
        component: The generated FastMCP component (tool, resource, etc.)
    """
    # Only customize OpenAPITool instances
    if not isinstance(component, OpenAPITool):
        return

    # Generate and set title annotation
    title = _generate_tool_title(route)
    category = _categorize_tool(route.path)

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
    _add_inferred_hints(route, component.annotations)

    # Register cache invalidation patterns for write tools
    method = getattr(route, "method", None)
    if method:
        patterns = _compute_tool_invalidation_patterns(route.path, method)
        if patterns:
            register_tool_invalidation(component.name, patterns)

    # Add category to tags (used for grouping in MCP clients)
    if component.tags is None:
        component.tags = set()  # type: ignore[unreachable]
    component.tags.add(category)

    # For read-only tools, add a note encouraging use of resources
    if component.annotations and component.annotations.readOnlyHint:
        resource_note = (
            "\n\nNote: For read-only operations, consider using mcp_read_resource() "
            "instead, which provides a unified interface with better formatting and caching. "
            "See AGENT_GUIDELINES.md for details."
        )
        existing_doc = component.__doc__ or ""
        # Avoid adding the note twice
        if resource_note not in existing_doc:
            component.__doc__ = existing_doc + resource_note

    # Apply label validation/conversion to tools that have a 'labels' parameter
    _maybe_wrap_labels(component)


async def load_swagger_spec(gitea_client: GiteaClient | None = None) -> dict[str, Any]:
    """Load Swagger spec from Gitea instance or local file.

    Args:
        gitea_client: Optional client to use for fetching the spec. If not provided,
                     loads from local swagger.v1.json file.

    Returns:
        Swagger spec as dictionary

    Raises:
        SpecError: If spec cannot be loaded or parsed
    """
    if gitea_client is None:
        # Fallback to loading local spec file (for testing)
        logger.info("Loading OpenAPI spec from local swagger.v1.json")
        try:
            spec_path = Path("swagger.v1.json")
            if not spec_path.exists():
                msg = "Local swagger.v1.json file not found"
                raise SpecError(msg)
            with open(spec_path) as f:
                local_spec: dict[str, Any] = json.load(f)
            logger.info(
                "Spec loaded",
                extra={
                    "spec_version": local_spec.get("swagger"),
                    "paths_count": len(local_spec.get("paths", {})),
                },
            )
            return local_spec
        except json.JSONDecodeError as e:
            msg = f"Invalid JSON in local swagger.v1.json: {e}"
            raise SpecError(msg) from e
        except Exception as e:
            msg = f"Failed to load local swagger.v1.json: {e}"
            raise SpecError(msg) from e

    # Construct URL: base_url without /api/v1 + /swagger.v1.json
    spec_url = f"{gitea_client._config.url}/swagger.v1.json"

    logger.info("Loading OpenAPI spec from %s", spec_url)

    try:
        remote_spec = await gitea_client.request("GET", spec_url)
        # If request returned a string (unlikely for JSON), parse it
        if isinstance(remote_spec, str):
            remote_spec = json.loads(remote_spec)
        logger.info(
            "Spec loaded",
            extra={
                "spec_version": remote_spec.get("swagger"),
                "paths_count": len(remote_spec.get("paths", {})),
            },
        )
        return remote_spec
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in spec from {spec_url}: {e}"
        raise SpecError(msg) from e
    except Exception as e:
        msg = f"Failed to fetch or parse spec from {spec_url}: {e}"
        raise SpecError(msg) from e
    except Exception as e:
        msg = f"Failed to fetch spec from {spec_url}: {e}"
        raise SpecError(msg) from e


async def create_mcp_server(gitea_client: GiteaClient) -> FastMCP:
    """Create the Gitea MCP server from OpenAPI spec.

    Args:
        gitea_client: Initialized GiteaClient to use for API calls

    Returns:
        Configured FastMCP server instance

    Raises:
        SpecError: If spec loading or conversion fails
    """
    config = gitea_client._config  # Access config for logging

    # Setup logging as early as possible
    setup_logging(level=config.log_level, log_format=config.log_format)

    logger.info("Starting Gitea MCP Server initialization")

    try:
        spec = await load_swagger_spec(gitea_client)
    except SpecError:
        raise
    except Exception as e:
        msg = f"Failed to load OpenAPI spec: {e}"
        raise SpecError(msg) from e

    logger.info("Converting OpenAPI v2 to v3...")
    try:
        openapi_spec = convert_swagger_to_openapi_v3(spec)
        logger.info(
            "Conversion completed",
            extra={
                "openapi_version": openapi_spec.get("openapi"),
                "paths": len(openapi_spec.get("paths", {})),
            },
        )
    except Exception as e:
        msg = f"Failed to convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    logger.info("Creating FastMCP server...")
    # Create OpenAPI provider (FastMCP 3.x pattern)
    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=gitea_client.client,
        mcp_component_fn=_customize_component,
    )
    mcp = FastMCP(
        name="Gitea MCP Server",
        providers=[provider],
        instructions="""
# Gitea MCP Server

This server provides tools and resources to interact with Gitea (self-hosted Git service).

## CRITICAL: Lazy Loading
**This server uses lazy loading.** The full tool catalog is NOT available via `list_tools()`. You will see only:
- `search_tools` (synthetic) - to discover tools
- `call_tool` (synthetic) - to execute tools
- `mcp_list_resources`, `mcp_read_resource` (pinned internal tools)

**You MUST use a two-step pattern:**
1. **DISCOVER**: `await call_tool("search_tools", {"query": "keyword"})` returns a list of real tool definitions with their exact `name` and `description`.
2. **EXECUTE**: `await call_tool("call_tool", {"name": "<exact_name_from_search>", "arguments": {...}})` to run the tool.

You cannot call a tool by its presumed name without searching first (unless you already know it from a previous search).

## Quick Start Example
```python
# Get current user
tools = await call_tool("search_tools", {"query": "current user"})
# tools might include: {"name": "user_get_current", "description": "Get the current user", ...}
result = await call_tool("call_tool", {"name": "user_get_current"})

# List your repositories
tools = await call_tool("search_tools", {"query": "list repos"})
# Look for a tool like "user_current_list_repos" or "user_repos_list"
repos = await call_tool("call_tool", {
    "name": "user_current_list_repos",
    "arguments": {"page": 1, "limit": 50}
})
```

## Authentication
Auth is configured via environment variables at server startup. You cannot change it. Verify identity with `user_get_current` (discover via search as shown above).

## Tool Discovery Tips
- **Start with broad keywords**: "issue", "repo", "user", "pull", "org", "topic", "release", "admin", "milestone", "label", "comment", "webhook", "key", "branch", "tag", "team", "permission".
- **If no results**: Simplify the query to one word. Search is case-insensitive and matches on tool name, description, and tags.
- **Tool naming**: Tools use snake_case (underscores). They are derived from Gitea API operationIds (camelCase → snake_case).
- **Common patterns**:
  - `{domain}_{action}_{resource?}` e.g., `issue_create_repo_issue`, `repo_delete`, `user_get`
  - `{domain}_list_{resource}` e.g., `user_list_orgs`, `org_list_repos`
  - `{domain}_search_{resource}` e.g., `repo_search`, `issue_search_repo_issues`
- **Admin tools**: `admin_*` tools only appear in search results if you are an admin.
- **Save a tool name** for reuse: Once you find a tool name (e.g., `user_get_current`), you can use it directly in subsequent `call_tool` calls without searching again (unless you need other tools).

## Resources
Resources provide cached, read-only access. Use them for efficient data retrieval when you know the URI pattern.

**List resources**: `mcp_list_resources()` (always available)
**Read resource**: `mcp_read_resource(uri)` where `uri` is like:
- `gitea://repos/{owner}/{repo}` → repository summary (Markdown)
- `gitea://repos/{owner}/{repo}/issues` → all issues (Markdown)
- `gitea://repos/{owner}/{repo}/readme` → README (plain text)
- `gitea://users/{username}` → user profile (Markdown)
- `gitea://version` → server version

To get your own repos via resource: first get your username (`user_get_current`), then use `gitea://repos/{username}` as owner in the URI.

## Labels

When creating or editing issues or pull requests, you can specify labels using either:
- **Names** (strings): e.g., `"bug"`, `"Kind/Feature"`, `"Priority/High"`
- **IDs** (integers): e.g., `1`, `42`, `184`

⚠️ **Important**: Only existing repository labels are allowed. If you use a name that doesn't exist, you'll get an error with the list of available labels.

### Best Practice: Discover labels first

Before creating an issue/PR with labels, it's a good idea to fetch the available labels:

```python
# Option 1: Use the list_labels tool (discover via search)
tools = await call_tool("search_tools", {"query": "list labels"})
labels = await call_tool("call_tool", {"name": "list_labels", "arguments": {"owner": "org", "repo": "repo"}})

# Option 2: Read the labels resource (faster, cached)
labels_md = await mcp_read_resource("gitea://repos/org/repo/labels")
```

### Example: Create an issue with label names

```python
# Discover the create issue tool
tools = await call_tool("search_tools", {"query": "create issue"})
# Use label names (automatically converted to IDs)
result = await call_tool("call_tool", {
    "name": "issue_create_repo_issue",
    "arguments": {
        "owner": "myorg",
        "repo": "myrepo",
        "title": "Bug: Something is broken",
        "body": "Details...",
        "labels": ["Kind/Bug", "Priority/High"]  # Use names, not IDs
    }
})
```

## Workflows

### 1. Get current user and list their repositories
```python
# Discover and call user_get_current
u = await call_tool("search_tools", {"query": "current user"})
user = await call_tool("call_tool", {"name": "user_get_current"})
username = user["login"]

# Discover and call a tool to list repos
# Search for "list repos user" → likely "user_current_list_repos" or "user_repos_list"
repos = await call_tool("call_tool", {
    "name": "user_repos_list",
    "arguments": {"page": 1, "limit": 50}
})
```

### 2. Search and create an issue
```python
# Find tools related to issues
t = await call_tool("search_tools", {"query": "issue"})
# Create an issue (look for a tool like "issue_create_repo_issue")
create = await call_tool("call_tool", {
    "name": "issue_create_repo_issue",
    "arguments": {
        "owner": "myorg", "repo": "myrepo",
        "title": "Bug report", "body": "details", "labels": ["bug"]
    }
})
```

### 3. Manage repository topics
```python
# Discover topic management tools
t = await call_tool("search_tools", {"query": "topic"})
# Add a topic
await call_tool("call_tool", {
    "name": "repo_add_topic",
    "arguments": {"owner": "org", "repo": "repo", "topic": "gitea"}
})
# Delete a topic
await call_tool("call_tool", {
    "name": "repo_delete_topic",
    "arguments": {"owner": "org", "repo": "repo", "topic": "old"}
})
```

## Troubleshooting
- **"Unknown tool"**: You likely tried to call a tool without using `call_tool` as the proxy, or the tool name is wrong. Remember: you must use `call_tool(name="call_tool", arguments={"name": "<real_tool>", ...})`.
- **No search results**: Try a single keyword. If still none, the tool may not exist or you lack permission.
- **Empty resource**: Resources reflect permissions (e.g., `users/{username}/repos` returns public repos only). Use authenticated tools (`user_*`) to see private/accessible repos.
- **Need to see all tools**: There is no way to list all tools directly due to lazy loading. Use broad search queries like "repo" to surface most repository-related tools.
- **Tool requires admin**: `admin_*` tools are hidden if you aren't an admin. Search for them will yield no results.

## Tool Prefixes (for search)
`issue_`, `repo_`, `pull_request_`, `pr_`, `user_`, `org_`, `team_`, `milestone_`, `label_`, `comment_`, `release_`, `tag_`, `branch_`, `protected_branch_`, `protected_tag_`, `key_`, `webhook_`, `gpg_key_`, `gitea_`, `admin_`, `mcp_`, `topic_`, `search_`

## Pagination
Most list operations accept `page` (1-based) and `limit` (page size). Use these to paginate through large sets. Default limits vary (often 30-50). Always paginate to avoid overwhelming responses.

## Resources vs Tools
- **Tools**: Execute API calls, may modify state, typically return structured data. Use `search_tools` to discover.
- **Resources**: Cached, efficient reads of formatted data (Markdown, JSON). Use `mcp_list_resources` to see all available URIs.

Combine both: use tools to find identifiers, then resources to read detailed cached summaries where available.
""",
    )

    # Add response caching middleware
    logger.info("Adding response caching middleware...")
    caching_middleware = ResponseCachingMiddleware(
        cache_storage=None,  # In-memory cache
        read_resource_settings=ReadResourceSettings(enabled=True, ttl=30.0),
        list_resources_settings=ListResourcesSettings(enabled=True, ttl=300.0),
        call_tool_settings=CallToolSettings(enabled=False),
        get_prompt_settings=GetPromptSettings(enabled=False),
        max_item_size=100_000_000,  # 100MB
    )
    mcp.add_middleware(caching_middleware)

    # Add cache invalidation middleware (must come after caching middleware)
    # This ensures write operations clear affected resources from cache
    logger.info("Adding cache invalidation middleware...")
    invalidation_middleware = CacheInvalidationMiddleware(caching_middleware)
    mcp.add_middleware(invalidation_middleware)

    # Add search transform for lazy loading of tools (FastMCP 3.x)
    # Can be disabled via ENABLE_LAZY_LOADING=false (useful for tests)
    if getattr(config, "enable_lazy_loading", True):
        logger.info("Adding search transform for lazy loading...")
        mcp.add_transform(
            TolerantBM25SearchTransform(
                max_results=10,
                always_visible=[
                    # Pin essential MCP resource tools that agents need
                    "mcp_read_resource",
                    "mcp_list_resources",
                ],
            )
        )
    else:
        logger.info("Lazy loading disabled via config; all tools will be listed directly")

    # Register resources
    logger.info("Registering MCP resources...")
    # Auto-generate resources for all GET endpoints (raw JSON)
    resources.register_auto_generated_resources(mcp, gitea_client, openapi_spec)
    # Register custom-formatted resources (Markdown) and overrides
    resources.register_custom_resources(mcp, gitea_client)

    # Register MCP resource access tools (for agents to read resources)
    logger.info("Registering MCP resource access tools...")
    register_mcp_resource_tools(mcp)

    # Apply tool filtering based on user permissions if enabled
    if config.tool_filtering_enabled:
        try:
            logger.info("Applying tool permission filtering")
            await filter_tools_by_permissions(mcp, gitea_client)
        except Exception as e:
            logger.exception(
                "Tool filtering failed, proceeding without filtering",
                extra={"error": str(e)},
            )
    else:
        logger.info("Tool filtering is disabled")

    logger.info("Gitea MCP Server initialized successfully")
    return mcp


async def main_async() -> None:
    """Async main entry point."""
    try:
        config = Config.get()
        setup_logging(level=config.log_level, log_format=config.log_format)
    except Exception as e:  # noqa: BLE001
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    gitea_client = GiteaClient(config)

    try:
        mcp = await create_mcp_server(gitea_client)
    except Exception:
        logger.exception("Failed to initialize server")
        await gitea_client.close()
        sys.exit(1)

    try:
        logger.info("Starting MCP server (stdio transport)")
        await mcp.run_stdio_async()
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
        # Exit normally, finally will close resources
    except Exception:
        logger.exception("Server crashed")
        sys.exit(1)
    finally:
        # Always close client first
        with contextlib.suppress(Exception):
            await gitea_client.close()
        # Then shutdown logging to avoid writing to closed streams
        logging.shutdown()
        logging.shutdown()


def main() -> None:
    """Synchronous entry point that runs the async main."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
