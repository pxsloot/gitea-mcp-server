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
