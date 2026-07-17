# Gitea MCP Server

Welcome. You are a first-class user of this server, not an afterthought. The
tools and resources here are built for agents like you: discoverable,
predictable, and honest about what they can and cannot do. This short guide
gets you productive in minutes -- and tells you where to look when something
is not where you expect it.

## What you get

The tools and resources are generated directly from *this host's* Gitea/Forgejo
Swagger/OpenAPI spec. They mirror the underlying API one-to-one: no invented
abstractions, no reimagined endpoints. What you call is as close to the raw API
as it gets, wrapped only with discovery, annotations, and caching.

Two filters shape the set you actually see:

1. **Your token scopes** -- tools and resources your token cannot use are
   hidden from you. This is universal: *every* tool and resource is scope-
   filtered, not just admin ones.
2. **Server config** -- an optional exclusion/include config can further hide
   or reveal specific tools or resources.

So the surface you see is the *complete* set for your token. If a tool is not
listed, it is filtered -- not missing. Do not go on a wild-goose hunt for a
tool your token cannot reach; `search_tools` will confirm what exists for you.

## Tool naming and prefix

Every tool name carries the server's configured prefix (default `gitea_`).
Use that prefix when you call. The rest of the name follows a predictable
grammar derived from the Gitea API operationId (camelCase -> snake_case):

- `{prefix}{domain}_{action}_{resource?}`  e.g. `gitea_issue_create_issue`, `gitea_repo_delete`
- `{prefix}{domain}_list_{resource}`       e.g. `gitea_user_list_orgs`, `gitea_org_list_repos`
- `{prefix}{domain}_search_{resource}`     e.g. `gitea_repo_search`, `gitea_issue_search_issues`

Domains: `issue`, `repo`/`repository`, `pull_request`, `user`, `org`,
`team`, `milestone`, `label`, `comment`, `release`, `tag`, `branch`,
`protected_branch`, `key`, `webhook`, `admin`, `topic`, `gpg_key`.

Synthetic (discovery) tools are prefixed the same way: `gitea_search`,
`gitea_search_tools`, `gitea_tool_info`, `gitea_call_tool`, `gitea_list_resources`,
`gitea_read_resource`, `gitea_search_resources`, `gitea_read_doc`, `gitea_search_docs`.
They carry the `synthetic` tag in search results.

**Workflow**: form a guess from the grammar, then confirm with `search_tools`
before calling. Example: "list an org's teams" -> guess `gitea_org_list_teams`
-> `search_tools("org list teams")` to confirm.

## Discovery and calling

Tools are lazy-loaded: `list_tools()` does not return them. Discover instead:

- `search_tools("issue")`        -> name, description, tags, annotations
- `search_tools("create pr", category="pull_request")`  -> narrow by category
- `tool_info("gitea_issue_get_issue")`  -> parameters, output example, annotations
- `search("create issue")`       -> unified search across tools, docs, resources

All search tools accept `min_score` (0.0-1.0, default 0.1) to tune relevance.

Call any tool via `call_tool(name, args)`. Both the prefixed name
(`gitea_call_tool`) and the bare name (`call_tool`) reach the same proxy, and
it resolves unprefixed tool names too (e.g. `search_tools`). The one exception
is `call_tool` calling itself, which is blocked. Do not take my word for the
mechanics -- run `tool_info("gitea_call_tool")` and try both forms; the schema
and behavior are right there for you to read.

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

## A common workflow

The tools compose into the loop you will use most. Read it as a sequence, not
a menu -- each step is a real call you would make in a session:

1. **Planning** creates the work item:
   `search_tools("issue")` -> `gitea_issue_create_issue` with `labels`
   (e.g. `Kind/Feature`, `Priority/High`).
2. **Research/review** reads it and adds context:
   `gitea_issue_get_issue` -> `gitea_issue_create_comment` with findings.
3. **Planning** revises based on that:
   `gitea_issue_edit_issue` to update title, body, or labels.
4. **Development** reads the issue, does the work, and opens a PR:
   `gitea_issue_get_issue` -> commit and push -> `gitea_repo_create_pull_request`
   (`head` = your branch, `base` = target).
5. **PR review** reads the PR and comments:
   `gitea_repo_get_pull_request` -> `gitea_issue_create_comment` (PRs are issues
   in Gitea, so the same comment tool works).

That is the whole rhythm: issue to track, PR to deliver, comments to discuss.
Labels accept names (strings) or IDs (integers), validated against the repo's
existing labels -- see the `labels` guide (`read_doc("labels")`) for scoped
labels and validation errors.

Beyond tools, this server ships **workflow guides** -- explanations of how
Gitea/Forgejo features actually work (token scopes, branch protection, labels,
permissions, pull requests, and more). Find them with `search_docs("branch protection")`
or `read_doc("pull-requests")`, or browse `gitea://docs/guide/{topic}`. When a
task touches a feature you do not fully understand, a guide is often faster
than trial and error.

## Output format

Every tool except `call_tool` accepts a `format` parameter, and so do
`read_resource` and `read_doc`:

| Format    | When to use |
|-----------|-------------|
| `markdown`| Default. Schema-aware tables, best for reading. |
| `json`    | Programmatic extraction (e.g. `result["owner"]["id"]`). |
| `raw`     | Exact API response, for undocumented fields or debugging. |

`tool_info(name)` returns a compact `output_example` -- enough for almost every
call. `tool_info(name, detail="full")` adds the complete JSON Schema, which is
large (hundreds of lines on big tools). Use it rarely; run it once on a small
tool to get a feel for the shape, then trust the compact example day to day.

## Tool annotations

Every tool carries four hints. Inspect via `tool_info(name)`. Full semantics in
`docs/TOOL_ANNOTATIONS.md`.

| Hint             | Meaning                                  | Use for |
|------------------|------------------------------------------|---------|
| `readOnlyHint`   | Reads only, no side effects              | Safe to call anytime |
| `destructiveHint`| Can delete/destroy data                  | Warn / confirm first |
| `idempotentHint` | Repeat = same effect                     | Safe to retry on failure |
| `openWorldHint`  | Calls the external Gitea server          | All API tools are open-world |

## Authentication and scope

Auth is set via environment variables at startup; you cannot change it. Verify
identity with `call_tool("gitea_user_get_current")`.

All tools and resources are filtered by your token's scopes -- this is the
normal state, not a special case. `sudo` is simply one scope among others:
powerful, and ordinary in mechanism. If a tool or the `sudo` virtual param is
not visible, your token lacks the relevant scope; `gitea_user_get_current`
tells you who you are, and the absence of a tool tells you what you cannot reach.

## Troubleshooting

- **"Unknown tool"** -> the name is wrong; `search_tools(...)` to find it.
- **No search results** -> simplify to one keyword; or the tool is scope-filtered out.
- **Tool/resource not visible** -> expected if your token lacks the scope; it is filtered, not missing.
- **Empty resource** -> reflects permissions; use `gitea_user_current_list_repos` for private repos.
- **"Only administrators allowed to sudo"** -> your token lacks the `sudo`/`all` scope; the `sudo` param is correctly hidden.
- **Need full schema** -> `tool_info(name, detail="full")` or `read_resource("gitea://tool/{name}/schema")`.
