# Gitea MCP Server

This server provides ~200 tools and resources to interact with Gitea (self-hosted Git service).

## Calling Tools

All tools are prefixed with `gitea_` (e.g., `gitea_user_get_current`). Call any tool by name through the host's tool-call mechanism:

```
call_tool("gitea_user_get_current")
call_tool("gitea_issue_get_issue", {"owner": "org", "repo": "repo", "index": 1})
call_tool("gitea_issue_create_issue", {"owner": "org", "repo": "repo", "title": "Bug", "body": "details"})
```

Tools are lazy-loaded (not in `list_tools()`) but the host can still call them by name.

## Discovering Tools

The full tool list is **not** available via `list_tools()` (lazy loading). Use `search_tools` to find tools by keyword:

```
results = search_tools("issue")      # returns name + description for all issue-related tools
results = search_tools("list repo")  # natural-language queries work
results = search_tools("user")       # broad queries surface related tools
```

Once you have a tool name, inspect its parameters with `tool_info`:

```
info = tool_info("gitea_issue_get_issue")
# returns: parameters, output_example, annotations, tags
```

## Commonly Used Tools (known names, no search needed)

| Tool | Description | Common args |
|------|-------------|-------------|
| `gitea_user_get_current` | Get authenticated user | (none) |
| `gitea_user_current_list_repos` | List your repos | `page`, `limit` |
| `gitea_repo_search` | Search repositories | `q`, `page`, `limit`, `owner`, `topic`, `private`, `template` |
| `gitea_repo_get` | Get a repository | `owner`, `repo` |
| `gitea_issue_list_issues` | List issues in a repo | `owner`, `repo`, `state`, `page`, `limit` |
| `gitea_issue_get_issue` | Get a single issue | `owner`, `repo`, `index` |
| `gitea_issue_create_issue` | Create an issue | `owner`, `repo`, `title`, `body`, `labels`, `assignees`, `milestone` |
| `gitea_issue_edit_issue` | Edit an issue | `owner`, `repo`, `index`, `title`, `body`, `state`, `labels` |
| `gitea_repo_list_pull_requests` | List PRs in a repo | `owner`, `repo`, `state`, `page`, `limit` |
| `gitea_repo_create_pull_request` | Create a PR | `owner`, `repo`, `title`, `body`, `head`, `base` |
| `gitea_repo_list_branches` | List branches | `owner`, `repo`, `page`, `limit` |
| `gitea_org_list_current_user_orgs` | List your organizations | (none) |

For other tools, use `search_tools` → `tool_info` → `call_tool`.

## Authentication
Auth is configured via environment variables at server startup. You cannot change it. Verify identity with `call_tool("gitea_user_get_current")`.

## Tool Discovery Tips
- **Start with broad keywords**: "issue", "repo", "user", "pull", "org", "topic", "release", "admin", "milestone", "label", "comment", "webhook", "key", "branch", "tag", "team", "permission".
- **If no results**: Simplify the query to one word. Search is case-insensitive and matches on tool name, description, and tags.
- **Tool naming**: Tools use snake_case, derived from Gitea API operationIds (camelCase → snake_case).
- **Tool prefix**: All tools are prefixed with `gitea_` (e.g., `gitea_issue_get_issue`).
- **Common patterns**:
  - `{domain}_{action}_{resource?}` — `issue_create_issue`, `repo_delete`, `user_get`
  - `{domain}_list_{resource}` — `user_list_orgs`, `org_list_repos`
  - `{domain}_search_{resource}` — `repo_search`, `issue_search_issues`
- **Admin tools**: `admin_*` tools only appear in search results if you are an admin.
- **Save a tool name** for reuse: Once you find a tool name, you can use it directly without searching again.

## Resources
Resources provide cached, read-only access. Use them for efficient data retrieval when you know the URI pattern. **For any read-only operation, prefer `mcp_read_resource()` over calling a tool** — resources are cached, pre-formatted, and consistently structured.

**List resources**: `mcp_list_resources()` (always available)
**Read resource**: `mcp_read_resource(uri)` where `uri` is like:
- `gitea://repos/{owner}/{repo}` → repository summary (Markdown)
- `gitea://repos/{owner}/{repo}/issues` → all issues (Markdown)
- `gitea://repos/{owner}/{repo}/readme` → README (plain text)
- `gitea://users/{username}` → user profile (Markdown)
- `gitea://version` → server version (plain text)
- `gitea://server/info` → server metadata: type (Gitea/Forgejo), API version, description (Markdown)
- `gitea://tool/{name}/schema` → full tool schema (JSON)

To get your own repos via resource: first get your username (`call_tool("gitea_user_get_current")`), then use `gitea://repos/{username}` as owner in the URI.

## Labels

When creating or editing issues or pull requests, you can specify labels using either:
- **Names** (strings): e.g., `"bug"`, `"Kind/Feature"`, `"Priority/High"`
- **IDs** (integers): e.g., `1`, `42`, `184`

⚠️ **Important**: Only existing repository labels are allowed. If you use a name that doesn't exist, you'll get an error with the list of available labels.

### Best Practice: Discover labels first

Before creating an issue/PR with labels, fetch the available labels:

```python
# Option 1: Search then call
call_tool("search_tools", {"query": "list labels"})
labels = call_tool("gitea_issue_list_labels", {"owner": "org", "repo": "repo"})

# Option 2: Read the labels resource (faster, cached)
mcp_read_resource("gitea://repos/org/repo/labels")
```

### Example: Create an issue with label names

```python
call_tool("search_tools", {"query": "create issue"})
result = call_tool("gitea_issue_create_issue", {
    "owner": "myorg",
    "repo": "myrepo",
    "title": "Bug: Something is broken",
    "body": "Details...",
    "labels": ["Kind/Bug", "Priority/High"],
})
```

## Workflows

### 1. Get current user and list their repositories
```python
user = call_tool("gitea_user_get_current")
username = user["login"]

repos = call_tool("gitea_user_current_list_repos", {"page": 1, "limit": 50})
```

### 2. Search and create an issue
```python
call_tool("search_tools", {"query": "issue"})
create = call_tool("gitea_issue_create_issue", {
    "owner": "myorg", "repo": "myrepo",
    "title": "Bug report", "body": "details", "labels": ["bug"],
})
```

### 3. Manage repository topics
```python
call_tool("search_tools", {"query": "topic"})
call_tool("gitea_repo_add_topic", {"owner": "org", "repo": "repo", "topic": "gitea"})
call_tool("gitea_repo_delete_topic", {"owner": "org", "repo": "repo", "topic": "old"})
```

## Troubleshooting
- **"Unknown tool"**: The tool name doesn't exist. Search for it first with `call_tool("search_tools", ...)`.
- **No search results**: Try a single keyword. If still none, the tool may not exist or you lack permission.
- **Empty resource**: Resources reflect permissions (e.g., `users/{username}/repos` returns public repos only). Use tools like `gitea_user_current_list_repos` to see private/accessible repos.
- **Need to see all tools**: There is no way to list all tools directly due to lazy loading. Use broad search queries like "repo" to surface most repository-related tools.
- **Need full tool schema**: Use `call_tool("tool_info", {"name": "..."})` to get parameters, output_example, annotations, and tags. Or read the `gitea://tool/{name}/schema` resource.
- **Tool requires admin**: `admin_*` tools are hidden if you aren't an admin.

## Tool Prefixes (for search)
`issue_`, `repo_`, `pull_request_`, `pr_`, `user_`, `org_`, `team_`, `milestone_`, `label_`, `comment_`, `release_`, `tag_`, `branch_`, `protected_branch_`, `protected_tag_`, `key_`, `webhook_`, `gpg_key_`, `gitea_`, `admin_`, `mcp_`, `topic_`, `search_`

## Pagination
Most list operations accept `page` (1-based) and `limit` (page size). Use these to paginate through large sets. Default limits vary (often 30-50). Always paginate to avoid overwhelming responses.

## Resources vs Tools
- **Tools**: Execute API calls, may modify state, typically return structured data. Use `call_tool("search_tools", ...)` to discover.
- **Resources**: Cached, efficient reads of formatted data (Markdown, JSON). Use `mcp_list_resources` to see all available URIs.

Combine both: use tools to find identifiers, then resources to read detailed cached summaries where available.
