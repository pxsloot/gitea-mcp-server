# Gitea MCP Server

This server provides ~200 tools and resources to interact with Gitea (self-hosted Git service).

## Unified Search

The `search` tool searches across **tools, workflow docs, and resources** in a single call. Results include a `type` discriminator so you know how to access each result:

```
result = search("issue")                     # default: markdown output
result = search("create pull request")       # natural language works
result = search("branch protection", format="json")
result = search("webhook", min_score=0.5)    # only highly relevant results
```

Each result item:
- `type`: `"tool"`, `"doc"`, or `"resource"`
- `name`: tool name, doc topic, or resource name
- `description`: brief summary
- `tags`: categorization tags
- `access_uri`: how to access it (tool name for tools, `gitea://docs/guide/{topic}` for docs, `gitea://...` URI for resources)

This is the recommended starting point for discovery. Use focused search tools (`search_tools`, `search_docs`, `search_resources`) when you need to narrow to a specific subsystem.

## Calling Tools

All tools are called via the MCP host's `call_tool` function. **Synthetic tools** (discovery helpers) and **API tools** both follow the same prefix convention - all tool names are prefixed with `gitea_`. The table below shows the conceptual names alongside their actual MCP protocol names:

| Category | Conceptual name | Actual MCP name |
|----------|----------------|-----------------|
| Synthetic | `search`, `search_tools`, `tool_info`, `list_resources`, `read_resource`, `read_doc`, `call_tool`, ... | `gitea_search`, `gitea_search_tools`, `gitea_tool_info`, `gitea_list_resources`, `gitea_read_resource`, `gitea_read_doc`, `gitea_call_tool`, ... |
| API | `issue_create_issue`, `user_get_current`, ... | `gitea_issue_create_issue`, `gitea_user_get_current`, ... |

**When calling tools:** Use the **actual MCP name** (with the `gitea_` prefix):

```
call_tool("gitea_search", {"query": "issue"})
call_tool("gitea_search_tools", {"query": "create pr"})
call_tool("gitea_tool_info", {"name": "gitea_issue_get_issue"})
call_tool("gitea_read_resource", {"uri": "gitea://repos/org/repo"})
call_tool("gitea_list_resources", {"tag": "repository"})

call_tool("gitea_user_get_current")
call_tool("gitea_issue_get_issue", {"owner": "org", "repo": "repo", "index": 1})
call_tool("gitea_issue_create_issue", {"owner": "org", "repo": "repo", "title": "Bug", "body": "details"})
call_tool("gitea_repo_list_branches", {"owner": "org", "repo": "repo", "format": "markdown"})  # formatted
```

The synthetic `call_tool` tool (e.g., `call_tool("gitea_search_tools", ...)`) is a proxy that dispatches to other tools. Both prefixed and unprefixed names work with it - it automatically resolves unprefixed names (e.g., `search_tools`) to their prefixed form. The only exception: `call_tool("call_tool")` is blocked to prevent infinite recursion.

Tools are lazy-loaded (not in `list_tools()`) but the host can still call them by name.

## Discovering Tools

The full tool list is **not** available via `list_tools()` (lazy loading). Use `search_tools` to find tools by keyword:

```
results = search_tools("issue")      # returns name + description + tags + annotations for issue tools
results = search_tools("list repo")  # natural-language queries work
results = search_tools("create", category="admin")  # narrow by category: admin, organization, user, issue, pull_request, repository, misc
results = search_tools("webhook", min_score=0.8)    # tighten relevance threshold
```

All search tools (`search`, `search_tools`, `search_resources`, `search_docs`) accept a `min_score` parameter (0.0-1.0, default 0.1) to control relevance. A value of 0.0 returns everything with any overlap; 1.0 returns only the single best match. Raise `min_score` to reduce noise from broad queries.

Each result includes `tags` (category labels), `annotations` (readOnlyHint, destructiveHint, idempotentHint, openWorldHint, title), and a `score` (normalized relevance, 0.0-1.0 where 1.0 is the top match for that query) alongside `name` and `description`. Use `score` to apply your own relevance threshold when `min_score` is too coarse.

Once you have a tool name, inspect its parameters with `tool_info`:

```
info = tool_info("gitea_issue_get_issue")
# returns: parameters, output_example, annotations, tags
```

The `output_example` is a **compact type-summary**. Nested `$ref` component
types are shown as `{"$ref": "TypeName"}` (JSON) or ``$ref:TypeName``
(markdown) instead of inlining the full schema - so you can see the data
shape at a glance without wasting tokens.  For example:

```
$ref:User          → the field is a User object (expandable)
$ref:Milestone     → the field is a Milestone object
$ref:Label         → the field is a Label object
"Example Title"    → literal string value
```

When you need the fully-resolved schema, use `detail="full"`:

```
info = tool_info("gitea_issue_get_issue", detail="full")
# same compact output_example, plus: output_schema (full JSON Schema)
```

The `gitea://tool/{name}/schema` resource always includes both the compact
example and the full schema in a single JSON document.

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

For other API tools, use `search_tools` → `tool_info` → `call_tool`.

## Tool Annotations

Every tool carries four machine-readable annotations that help you make safer and more informed choices:

| Annotation | Meaning | Use for |
|------------|---------|---------|
| `readOnlyHint` | Tool only reads data -- no side effects | Discovery, preview, safe to call anytime |
| `destructiveHint` | Tool can delete/destroy data | Warn before calling, require confirmation |
| `idempotentHint` | Calling multiple times has same effect as once | Safe to retry on network failure |
| `openWorldHint` | Tool interacts with external Gitea server | All tools are open-world |

**Best practices**:
- Prefer tools with `readOnlyHint: true` for non-destructive data retrieval (e.g., listing, searching, getting).
- Display a warning before calling any tool where `destructiveHint: true`.
- Retry on transient failures only when `idempotentHint: true` -- POST and PATCH operations are NOT idempotent.
- All Gitea tools have `openWorldHint: true` -- they make HTTP calls to the Gitea API and reflect real server state.

Inspect annotations via `tool_info("gitea_tool_name")` -- the response includes an `annotations` object with all four hints.

## Authentication
Auth is configured via environment variables at server startup. You cannot change it. Verify identity with `call_tool("gitea_user_get_current")`.

## Tool Naming Convention

All tools - both API tools and synthetic tools - are prefixed with `gitea_` in the MCP protocol. This prefix is applied by the server at runtime, so every tool you call must include it.

- **API tools**: Snake_case derived from Gitea API operationIds (camelCase → snake_case), prefixed with `gitea_` (e.g., `gitea_issue_create_issue`).
  - `gitea_{domain}_{action}_{resource?}` - `gitea_issue_create_issue`, `gitea_repo_delete`, `gitea_user_get`
  - `gitea_{domain}_list_{resource}` - `gitea_user_list_orgs`, `gitea_org_list_repos`
  - `gitea_{domain}_search_{resource}` - `gitea_repo_search`, `gitea_issue_search_issues`
- **Synthetic tools**: Lowercase, also prefixed with `gitea_` (e.g., `gitea_search`, `gitea_search_tools`, `gitea_call_tool`, `gitea_search_docs`, `gitea_read_doc`, `gitea_list_resources`, `gitea_read_resource`, `gitea_search_resources`, `gitea_tool_info`). They carry a `"synthetic"` tag in search results.

**Note**: The conceptual names shown in documentation (e.g., `search_tools`, `tool_info`) omit the prefix for readability. Always use the `gitea_`-prefixed form when calling tools. When using the synthetic `call_tool` proxy, unprefixed names also work - it resolves them automatically.

## Tool Discovery Tips
- **Start with broad keywords**: "issue", "repo", "user", "pull", "org", "topic", "release", "admin", "milestone", "label", "comment", "webhook", "key", "branch", "tag", "team", "permission".
- **If no results**: Simplify the query to one word. Search is case-insensitive and matches on tool name, description, and tags.
- **Synthetic tools vs API tools**: Both appear in `search_tools` results - synthetic tools are tagged with `"synthetic"`. Both are called using the `gitea_`-prefixed name via the host's `call_tool`. The synthetic `call_tool` proxy also accepts unprefixed names.
- **Admin tools**: `admin_*` tools only appear in search results if you are an admin.
- **Save a tool name** for reuse: Once you find a tool name, you can use it directly without searching again.

## Resources
Resources provide cached, read-only access. Use them for efficient data retrieval when you know the URI pattern. **For any read-only operation, prefer `read_resource()` over calling a tool** -- resources are cached, pre-formatted, and consistently structured.

**List resources**: `list_resources(format="markdown", tag="", type="")` supports `markdown`/`raw`/`json` output. Filter by `tag` (e.g. `"wrapper"`, `"repository"`, `"issue"`) or `type` (`"resource"` or `"template"`) to narrow results.
**Search resources**: `search_resources(query, format="markdown", min_score=0.1)` finds resources by natural language (BM25 ranking). Adjust `min_score` to control relevance strictness.
**Read resource**: `read_resource(uri, format="markdown")` accepts the same `format` parameter (``markdown`` / ``raw`` / ``json``). Common URIs:
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

**Both names and IDs are validated** against the repository's existing labels.
Unknown values of either type produce an error listing available labels.

Scoped (exclusive) labels with `/` in their name (e.g., `Kind/Bug`, `Priority/High`)
allow only one label per scope at a time. See the [`labels` workflow guide](gitea://docs/guide/labels)
for details.

### Canonical Workflow: Discover → Pass → Handle Errors

```python
# 1. Discover available labels (prefer the cached resource)
labels_info = read_resource("gitea://repos/org/repo/labels")
# Returns formatted list with label names, IDs, colors, and scoped info

# 2. Create an issue with label names
search_tools("create issue")
result = call_tool("gitea_issue_create_issue", {
    "owner": "myorg",
    "repo": "myrepo",
    "title": "Bug: Something is broken",
    "body": "Details...",
    "labels": ["Kind/Bug", "Priority/High"],
})

# 3. Or use integer IDs (more reliable - never affected by rename)
result = call_tool("gitea_issue_create_issue", {
    "owner": "myorg",
    "repo": "myrepo",
    "title": "Critical bug",
    "body": "Urgent fix needed",
    "labels": [5, 12],  # label IDs, not names
})

# 4. Handle validation errors - the error message lists available labels
#    e.g., "Unknown label name(s): ['nonexistent']. Available labels: ..."
```

### Validation Details

- **String names**: case-insensitive matching (`"bug"` matches `"Bug"`, `"BUG"`)
- **Integer IDs**: validated against the repository's label ID map
- **Unknown values**: a `ValidationError` includes grouped available labels

### Available Tools with Labels

Use `search_tools("labels")` to find all tools that accept labels:

| Tool | Purpose |
|------|---------|
| `gitea_issue_create_issue` | Create issue with labels |
| `gitea_issue_edit_issue` | Update issue labels |
| `gitea_issue_add_issue_labels` | Add labels to existing issue |
| `gitea_issue_replace_labels` | Replace all labels on issue |
| `gitea_repo_create_pull_request` | Create PR with labels |

### Labels Resource

The `gitea://repos/{owner}/{repo}/labels` resource (cached, Markdown) includes:
- Accepted format: `[string, integer][]`
- Label names, IDs, colors, descriptions
- Scoped/exclusive flag for each label
- Archived status

## Workflows

### 1. Get current user and list their repositories
```python
user = call_tool("gitea_user_get_current")
username = user["login"]

repos = call_tool("gitea_user_current_list_repos", {"page": 1, "limit": 50})
```

### 2. Search and create an issue
```python
search_tools("issue")
create = call_tool("gitea_issue_create_issue", {
    "owner": "myorg", "repo": "myrepo",
    "title": "Bug report", "body": "details", "labels": ["bug"],
})
```

### 3. Manage repository topics
```python
search_tools("topic")
call_tool("gitea_repo_add_topic", {"owner": "org", "repo": "repo", "topic": "gitea"})
call_tool("gitea_repo_delete_topic", {"owner": "org", "repo": "repo", "topic": "old"})
```

## Troubleshooting
- **"Unknown tool"**: The tool name doesn't exist. Search for it first with `search_tools(...)`.
- **No search results**: Try a single keyword. If still none, the tool may not exist or you lack permission.
- **Empty resource**: Resources reflect permissions (e.g., `users/{username}/repos` returns public repos only). Use tools like `gitea_user_current_list_repos` to see private/accessible repos.
- **Need to see all tools**: There is no way to list all tools directly due to lazy loading. Use broad search queries like "repo" to surface most repository-related tools.
- **Need full tool schema**: Use `tool_info("name")` to get parameters, output_example, annotations, and tags. Or read the `gitea://tool/{name}/schema` resource.
- **Tool requires admin**: `admin_*` tools are hidden if you aren't an admin.
- **"Only administrators allowed to sudo"**: The ``sudo`` parameter impersonates a user and requires an admin token with ``sudo`` or ``all`` scope. If this error appears, your token lacks the necessary scope - the ``sudo`` parameter should not be visible on your tools. Use ``gitea_user_get_current`` to verify your identity and scope.

## Tool Prefixes (for search)
`issue_`, `repo_`, `pull_request_`, `pr_`, `user_`, `org_`, `team_`, `milestone_`, `label_`, `comment_`, `release_`, `tag_`, `branch_`, `protected_branch_`, `protected_tag_`, `key_`, `webhook_`, `gpg_key_`, `gitea_`, `admin_`, `topic_`, `search_`

## Pagination
Most list operations - both API tools and synthetic tools - accept `page` (1-based) and `limit` (page size). Use these to paginate through large sets. Default limits vary: API tools often default to 30-50, synthetic tools default to 10 (max 100). Always paginate to avoid overwhelming responses. Pagination metadata (`has_more`, `next_offset`, `total_count`) is included in every list response's `structured_content`.

## Workflow Guides

Workflow guides explain Forgejo concepts and settings beyond individual API calls.
Available guides are listed below. Use them when you need to understand how features
work -- token scopes, branch protection, permission models, labels, etc.

**Two ways to access:**
- `search_docs(query)` -- find guides by natural language
- `read_doc(topic)` -- read a full guide
- `gitea://docs/guide/{topic}` -- same content as a resource

## Output Format (`format` parameter)

**Every tool except ``call_tool``** (which is a transparent proxy) accepts a
``format`` parameter to control how results are presented.  For API tools,
include ``format`` in the arguments dict; for synthetic tools, pass it as a
direct parameter (e.g. ``search(query, format="json")``):

| Format | When to use |
|--------|-------------|
| `markdown` | **Default.** Schema-aware Markdown with tables and sections. Best for browsing, display, and human/agent reading. Nested objects render as `##` sections with their own tables. Consistent across tools and resources - the same data looks the same regardless of access pattern. |
| `json` | Raw JSON structure. Best for **programmatic extraction**: get a specific field (`result["owner"]["id"]`), count results, or pass output to another computation. |
| `raw` | Return the result exactly as received from the underlying API. Use when you need the exact data shape -- for example, to check undocumented response fields or debug. |

The default response format (**``markdown``**) is configured server-wide via
``DEFAULT_RESPONSE_FORMAT`` in ``config.py`` and can be overridden via the
``DEFAULT_RESPONSE_FORMAT`` environment variable.

Examples:

```python
# Default: markdown (human-readable tables on every tool)
call_tool("gitea_user_get_current")
call_tool("gitea_issue_get_issue", {"owner": "org", "repo": "repo", "index": 42})

# JSON -- for programmatic extraction
call_tool("gitea_repo_get", {"owner": "org", "repo": "repo", "format": "json"})
search("create pr", format="json")
search_tools("issue", format="json")

# Raw API output -- for debugging
read_resource("gitea://repos/org/repo", format="raw")
```

## Resources vs Tools
- **Tools**: Two kinds: synthetic tools (`search`, `search_tools`, `search_docs`, `search_resources`, `tool_info`, `call_tool`, `list_resources`, `read_resource`, `read_doc`) are called directly; API tools (`gitea_*`) are called via `call_tool`. Use `search(...)` for unified discovery or `search_tools(...)` for tool-only results. To control output format on any tool except `call_tool` (which is a transparent proxy), include `format` in its arguments - see the **Output Format** section below.
- **Resources**: Cached, efficient reads. `list_resources`, `read_resource`, and `search_resources` accept a `format` parameter and are called directly (not via `call_tool`).

Combine both: use tools to find identifiers, then resources to read detailed cached summaries where available.
