"""Gitea MCP Server implementation."""

import asyncio
import contextlib
import logging
import sys
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    GetPromptSettings,
    ListResourcesSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)
from fastmcp.server.providers.openapi import OpenAPIProvider

from gitea_mcp_server.cache_invalidation import CacheInvalidationMiddleware
from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.config import Config
from gitea_mcp_server.constants import (
    CACHE_MAX_ITEM_SIZE,
    CACHE_TTL_DEFAULT,
    CACHE_TTL_RESOURCE_LIST,
    SEARCH_ALWAYS_VISIBLE_TOOLS,
    SEARCH_MAX_RESULTS,
)
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.logging import setup_logging
from gitea_mcp_server.server_setup.permissions import filter_tools_by_permissions
from gitea_mcp_server.server_setup.resource_registry import register_all_resources
from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec
from gitea_mcp_server.server_setup.tool_annotator import TolerantBM25SearchTransform

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
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
"""


async def create_mcp_server(gitea_client: GiteaClient) -> FastMCP:
    """Create the Gitea MCP server from OpenAPI spec.

    Args:
        gitea_client: Initialized GiteaClient to use for API calls

    Returns:
        Configured FastMCP server instance

    Raises:
        SpecError: If spec loading or conversion fails
    """
    config = gitea_client._config

    # Setup logging as early as possible
    setup_logging(level=config.log_level, log_format=config.log_format)

    logger.info("Starting Gitea MCP Server initialization")

    # Load and convert OpenAPI spec
    try:
        openapi_spec = await load_and_convert_spec(gitea_client)
    except SpecError:
        raise
    except Exception as e:
        msg = f"Failed to load or convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    # Initialize label manager
    label_manager = LabelManager()

    # Create OpenAPI provider
    provider = create_openapi_provider(
        openapi_spec=openapi_spec,
        client=gitea_client.client,
        label_manager=label_manager,
    )

    # Create FastMCP server
    mcp = FastMCP(
        name="Gitea MCP Server",
        providers=[provider],
        instructions=INSTRUCTIONS,
    )

    # Add response caching middleware
    logger.info("Adding response caching middleware...")
    caching_middleware = ResponseCachingMiddleware(
        cache_storage=None,  # In-memory cache
        read_resource_settings=ReadResourceSettings(enabled=True, ttl=CACHE_TTL_DEFAULT),
        list_resources_settings=ListResourcesSettings(enabled=True, ttl=CACHE_TTL_RESOURCE_LIST),
        call_tool_settings=CallToolSettings(enabled=False),
        get_prompt_settings=GetPromptSettings(enabled=False),
        max_item_size=CACHE_MAX_ITEM_SIZE,
    )
    mcp.add_middleware(caching_middleware)

    # Add cache invalidation middleware (must come after caching middleware)
    logger.info("Adding cache invalidation middleware...")
    invalidation_middleware = CacheInvalidationMiddleware(caching_middleware)
    mcp.add_middleware(invalidation_middleware)

    # Add search transform for lazy loading (FastMCP 3.x)
    if getattr(config, "enable_lazy_loading", True):
        logger.info("Adding search transform for lazy loading...")
        mcp.add_transform(
            TolerantBM25SearchTransform(
                max_results=SEARCH_MAX_RESULTS,
                always_visible=SEARCH_ALWAYS_VISIBLE_TOOLS,
            )
        )
    else:
        logger.info("Lazy loading disabled via config; all tools will be listed directly")

    # Register resources
    logger.info("Registering MCP resources...")
    register_all_resources(mcp, gitea_client, openapi_spec)

    # Register MCP resource access tools (for agents to read resources)
    logger.info("Registering MCP resource access tools...")
    from gitea_mcp_server.mcp_tools import register_mcp_resource_tools

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
    except Exception:
        logger.exception("Server crashed")
        sys.exit(1)
    finally:
        with contextlib.suppress(Exception):
            await gitea_client.close()
        logging.shutdown()
        logging.shutdown()


def main() -> None:
    """Synchronous entry point that runs the async main."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
