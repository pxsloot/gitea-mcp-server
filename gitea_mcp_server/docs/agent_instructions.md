# Gitea MCP Server

This server provides ~200 tools and resources to interact with Gitea/Forgejo.
Everything is discoverable: you do not need a fixed list. Use search to find a
tool, `tool_info` to inspect it, and `read_resource` for cached reads. This
document teaches the naming grammar and a few workflow shapes so you can guess
correctly and confirm with one search.

## Tool Naming Grammar

All tools are prefixed with `gitea_` in the MCP protocol. Use that prefix when
calling. The rest of the name follows a predictable grammar derived from the
Gitea API operationId (camelCase -> snake_case):

- `gitea_{domain}_{action}_{resource?}`  e.g. `gitea_issue_create_issue`, `gitea_repo_delete`
- `gitea_{domain}_list_{resource}`       e.g. `gitea_user_list_orgs`, `gitea_org_list_repos`
- `gitea_{domain}_search_{resource}`     e.g. `gitea_repo_search`, `gitea_issue_search_issues`

Domains: `issue`, `repo`/`repository`, `pull_request`, `user`, `org`,
`team`, `milestone`, `label`, `comment`, `release`, `tag`, `branch`,
`protected_branch`, `key`, `webhook`, `admin`, `topic`, `gpg_key`.

Synthetic (discovery) tools are also `gitea_`-prefixed: `gitea_search`,
`gitea_search_tools`, `gitea_tool_info`, `gitea_call_tool`, `gitea_list_resources`,
`gitea_read_resource`, `gitea_search_resources`, `gitea_read_doc`, `gitea_search_docs`.
They carry the `synthetic` tag in search results.

**Workflow**: form a guess from the grammar, then confirm with `search_tools`
before calling. Example: "list an org's teams" -> guess `gitea_org_list_teams`
-> `search_tools("org list teams")` to confirm.

## Discovery Flow

Tools are lazy-loaded: `list_tools()` does not return them. Discover instead:

- `search_tools("issue")`        -> name, description, tags, annotations
- `search_tools("create pr", category="pull_request")`  -> narrow by category
- `tool_info("gitea_issue_get_issue")`  -> parameters, output example, annotations
- `search("create issue")`       -> unified search across tools, docs, resources

All search tools accept `min_score` (0.0-1.0, default 0.1) to tune relevance.
Use `tool_info(name, detail="full")` for the complete JSON schema when needed.

## Calling Tools

Call any tool via the host's `call_tool`. The synthetic `gitea_call_tool` is a
proxy that also resolves unprefixed names (e.g. `search_tools`), except
`call_tool` itself.

```
call_tool("gitea_user_get_current")
call_tool("gitea_issue_get_issue", {"owner": "org", "repo": "repo", "index": 1})
call_tool("gitea_issue_create_issue", {"owner": "org", "repo": "repo", "title": "Bug", "body": "details"})
```

## Resources

Resources give cached, pre-formatted reads. For any read-only operation, prefer
`read_resource()` over calling a tool. URI pattern:

- `gitea://repos/{owner}/{repo}`            -> repository summary
- `gitea://repos/{owner}/{repo}/issues`     -> issues (Markdown)
- `gitea://repos/{owner}/{repo}/labels`     -> labels (names, IDs, scoped flags)
- `gitea://repos/{owner}/{repo}/readme`     -> README (text)
- `gitea://users/{username}`                -> user profile
- `gitea://version`                         -> server version
- `gitea://server/info`                     -> server metadata
- `gitea://tool/{name}/schema`              -> full tool schema (JSON)

List with `list_resources(tag=..., type=...)`; search with `search_resources(query)`.

## Workflow Skeletons

These show the discover -> call shape. The names follow the grammar above, so
they generalize to the other ~190 tools.

### Issue CRUD

```
search_tools("issue")                                  # discover
call_tool("gitea_issue_get_issue",   {owner, repo, index})        # read
call_tool("gitea_issue_list_issues", {owner, repo, state})        # list
call_tool("gitea_issue_create_issue",{owner, repo, title, body, labels})  # create
call_tool("gitea_issue_edit_issue",  {owner, repo, index, ...})  # update
```

Labels accept names (strings) or IDs (integers), validated against the repo's
existing labels. See the `labels` workflow guide (`read_doc("labels")` or
`gitea://docs/guide/labels`) for scoped/exclusive labels and validation errors.

### Pull Request CRUD

```
search_tools("pull request")                            # discover
call_tool("gitea_repo_list_pull_requests", {owner, repo, state})     # list
call_tool("gitea_repo_get_pull_request",   {owner, repo, index})     # read
call_tool("gitea_repo_create_pull_request",{owner, repo, title, head, base})  # create
call_tool("gitea_repo_edit_pull_request",  {owner, repo, index, ...})  # update
```

## Output Format

Every tool except `call_tool` accepts a `format` parameter:

| Format    | When to use |
|-----------|-------------|
| `markdown`| Default. Schema-aware tables, best for reading. |
| `json`    | Programmatic extraction (e.g. `result["owner"]["id"]`). |
| `raw`     | Exact API response, for undocumented fields or debugging. |

## Tool Annotations

Every tool carries four hints. Inspect via `tool_info(name)`. Full semantics in
`docs/TOOL_ANNOTATIONS.md`.

| Hint             | Meaning                                  | Use for |
|------------------|------------------------------------------|---------|
| `readOnlyHint`   | Reads only, no side effects              | Safe to call anytime |
| `destructiveHint`| Can delete/destroy data                  | Warn / confirm first |
| `idempotentHint` | Repeat = same effect                     | Safe to retry on failure |
| `openWorldHint`  | Calls the external Gitea server          | All API tools are open-world |

## Authentication

Auth is set via environment variables at startup; you cannot change it. Verify
identity with `call_tool("gitea_user_get_current")`. Admin-only tools (and the
`gitea_`-prefixed `sudo` virtual param) appear only when your token permits.

## Troubleshooting

- **"Unknown tool"** -> the name is wrong; `search_tools(...)` to find it.
- **No search results** -> simplify to one keyword; or you lack permission.
- **Empty resource** -> reflects permissions; use `gitea_user_current_list_repos` for private repos.
- **"Only administrators allowed to sudo"** -> your token lacks `sudo`/`all` scope; the `sudo` param should not be visible.
- **Need full schema** -> `tool_info(name, detail="full")` or `read_resource("gitea://tool/{name}/schema")`.
