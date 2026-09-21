# Gitea MCP Server

Welcome. You are a first-class user of this server, not an afterthought. The
tools and resources are built for agents like you: discoverable, predictable,
and honest about what they can and cannot do. This guide gets you productive in
minutes -- and says where to look when something is not where you expect it.

## What you get

The tools and resources are generated directly from *this host's* Gitea/Forgejo
Swagger/OpenAPI spec. They mirror the underlying API one-to-one: no invented
abstractions, no reimagined endpoints -- as close to the raw API as it gets,
wrapped only with discovery, annotations, and caching.

Two filters shape the set you actually see:

1. **Your token scopes** -- tools and resources your token cannot use are
   hidden. This is universal: *every* tool and resource is scope-filtered, not
   just admin ones.
2. **Server config** -- an optional exclusion/include config can further hide or
   reveal specific tools or resources.

So the surface you see is the *complete* set for your token. If a tool is not
listed, it is filtered -- not missing; `search_tools` confirms what exists.

## Tool naming and prefix

Every tool name carries the server's configured prefix (default
`{{TOOL_PREFIX}}`). The rest follows a predictable grammar derived from the
Gitea API operationId (camelCase -> snake_case):

- `{prefix}{domain}_{action}_{resource?}`  -- e.g. `{{TOOL_PREFIX}}issue_create_issue`
- `{prefix}{domain}_list_{resource}`       -- e.g. `{{TOOL_PREFIX}}user_list_orgs`
- `{prefix}{domain}_search_{resource}`     -- e.g. `{{TOOL_PREFIX}}repo_search`

Discovery tools (`search`, `search_tools`, `tool_info`, `call_tool`,
`list_resources`, `read_resource`, `read_doc`, `resolve_type`, and more) are
prefixed the same way and tagged `synthetic` in search results.

**Workflow**: form a guess from the grammar, then confirm with `search_tools`.
Example: "list an org's teams" -> guess `{{TOOL_PREFIX}}org_list_teams` ->
`search_tools("org list teams")` to confirm.

## Discovery and calling

Tools are lazy-loaded: `list_tools()` does not return them. Discover instead:

- `search_tools("issue")`        -> name, description, tags, annotations
- `search_tools("create pr", category="pull_request")`  -> narrow by category
- `tool_info("{{TOOL_PREFIX}}issue_get_issue")`  -> parameters, output example, annotations
- `search("create issue")`       -> unified search across tools, docs, resources

Searches take `min_score` (0.0-1.0, default 0.1). An empty query lists all
(paginated, no relevance score); `{{TOOL_PREFIX}}list_hidden_tools` enumerates
tools hidden from your token (scope-restricted, config-excluded, deprecated).

Call any tool via `call_tool(name, args)`. The prefixed name
(`{{TOOL_PREFIX}}call_tool`) and the bare name (`call_tool`) reach the same
proxy, and it resolves unprefixed names too. `name` is always the **target**
tool -- never `call_tool` itself; the proxy is invoked directly, not through
itself. Run `tool_info("{{TOOL_PREFIX}}call_tool")` and try both forms.

```
call_tool("{{TOOL_PREFIX}}issue_get_issue", {"owner": "org", "repo": "repo", "index": 1})
call_tool("{{TOOL_PREFIX}}issue_create_issue", {"owner": "org", "repo": "repo", "title": "Bug"})
```

A tool that is not available (scope-restricted, config-excluded, or deprecated)
returns a helpful error explaining why -- no silent failures.

## Parameters: never guess, always confirm

There are ~400 tools and the exact parameters differ per tool. **Do not guess a
parameter name or type from memory.** The authoritative contract is one call
away: `tool_info("{{TOOL_PREFIX}}issue_create_issue")`.

A handful of parameters recur because they mirror Gitea's API -- knowing these
removes most of the uncertainty cheaply:

| Parameter   | Type    | Notes |
|-------------|---------|-------|
| `owner`     | string  | repo owner; `^[a-zA-Z0-9]+([._-][a-zA-Z0-9]+)*$`, 1-50 chars |
| `repo`      | string  | repo name; same pattern rules, 1-100 chars |
| `index`/`id`| integer | resource id (int64) -- `index` for issues/PRs, `id` elsewhere |
| `page`      | integer | 1-based page for list/search tools (minimum 1) |
| `limit`     | integer | page size for list/search tools |

If a tool takes `owner`/`repo`, it almost certainly takes them as required
strings; if it lists or searches, it almost certainly takes `page`+`limit`.
Confirm the rest -- optional fields, enums, the exact id name -- with
`tool_info`; `format`, `detail`, `fetch_all`, and `sudo` are on every schema too.

## Resources

For a read-only operation, prefer `read_resource()` over calling a tool: it
gives cached, pre-formatted reads. Tool and resource display are unified -- a
tool bound by response type renders its resource sibling's view (curated for
collections, full payload for a single resource); `format=json`/`raw` are always
the raw API data. URIs follow `gitea://`:

- `gitea://repos/{owner}/{repo}`        -> repository summary
- `gitea://repos/{owner}/{repo}/issues` -> issues (Markdown)
- `gitea://repos/{owner}/{repo}/labels` -> labels (names, IDs, scoped flags)
- `gitea://repos/{owner}/{repo}/readme` -> README (text)
- `gitea://users/{username}`            -> user profile
- `gitea://version`                     -> server version
- `gitea://tool/{name}/schema`          -> full tool schema (JSON)
- `gitea://types/{typeName}`            -> resolved type schema (JSON)

List with `list_resources(tag=..., type=...)`; search with
`search_resources(query)`. Inspect a resource's metadata before reading it:

- `size_hint` (`tiny`/`small`/`medium`/`large`) -- estimated token cost. Large
  resources (issues, pulls) can exceed 300KB; prefer `detail="concise"`.
- `default_detail` -- the recommended detail level (`large` defaults to
  `concise`).
- `optional_params` -- query filters the resource accepts (e.g. `state` on
  issues/pulls) and their valid values. URIs in `list_resources` are clean (no
  `{?state}` syntax); check this field to discover filters.

## A common workflow

The tools compose into the loop you will use most:

1. **Planning** creates the work item: `search_tools("issue")` ->
   `{{TOOL_PREFIX}}issue_create_issue` with `labels` (e.g. `Kind/Feature`).
2. **Research/review** reads it and adds context: `{{TOOL_PREFIX}}issue_get_issue` ->
   `{{TOOL_PREFIX}}issue_create_comment`.
3. **Planning** revises: `{{TOOL_PREFIX}}issue_edit_issue` to update title, body, or
   labels.
4. **Development** reads the issue, does the work, opens a PR:
   `{{TOOL_PREFIX}}issue_get_issue` -> commit and push ->
   `{{TOOL_PREFIX}}repo_create_pull_request` (`head` = your branch, `base` = target).
5. **PR review** reads the PR and comments: `{{TOOL_PREFIX}}repo_get_pull_request` ->
   `{{TOOL_PREFIX}}issue_create_comment` (PRs are issues in Gitea).

That is the rhythm: issue to track, PR to deliver, comments to discuss. Labels
accept names or IDs, validated against the repo's existing labels -- see
`read_doc("labels")` for scoped labels and validation errors.

Beyond tools, this server ships **workflow guides** -- explanations of how
Gitea/Forgejo features and this server's output actually work. Find them with
`search_docs("branch protection")` or browse `gitea://docs/guide/{topic}`. When
a task touches a feature you do not fully understand, a guide is often faster
than trial and error.

## Output format

Most tools and resources accept `format` (`markdown` default | `json` | `raw`)
and `detail` (`full` default | `concise`). API tools and the synthetic tools
`tool_info`, `resolve_type`, `list_resources`, and `read_resource` take both;
search/discovery tools take `format` only, and `call_tool` takes neither.

- `markdown` -- curated, schema-aware rendering; best for reading.
- `json` -- the complete API data as a `{"result": ...}` envelope; best for
  programmatic extraction.
- `raw` -- that same envelope as deterministic JSON text (never a Python
  `repr`); always full detail.
- `detail="concise"` summarizes root objects and list items, collapsing nested
  `$ref`-backed fields to a marker -- `{"$ref": "TypeName"}`
  (`{"$ref": "TypeName", "count": N}` for a list), rendered `$ref:TypeName`.
- Content is the contract: the text channel is authoritative and mirrors
  `structured_content`. Paginated `json`/`raw` carry
  `has_more`/`next_offset`/`total_count` in the text. An empty or out-of-range
  page is that envelope with an empty `result` -- not an error.

The full contract -- markdown-vs-json, envelope shapes, `$ref` markers,
`resolve_type`, and the error catalog -- is in `read_doc("output-format")`.

`tool_info(name)` returns a compact `output_example` -- enough for almost every
call. `tool_info(name, detail="full")` adds the complete JSON Schema (hundreds
of lines on big tools), pageable with `page`/`limit`; use it rarely, and run it
once on a small tool to get a feel for the shape.

## Tool annotations

Every tool carries four hints. Inspect them via `tool_info(name)`:

| Hint             | Meaning                          | Use for |
|------------------|----------------------------------|---------|
| `readOnlyHint`   | Reads only, no side effects      | Safe to call anytime |
| `destructiveHint`| Can delete/destroy data          | Warn / confirm first |
| `idempotentHint` | Repeat = same effect             | Safe to retry on failure |
| `openWorldHint`  | Calls the external Gitea server  | All API tools are open-world |

## Authentication and scope

Auth is set via environment variables at startup; you cannot change it. Verify
identity with `call_tool("{{TOOL_PREFIX}}user_get_current")`.

You are authenticated as **{{USER_LOGIN}}** on a **{{SERVER_TYPE}}** server with
scopes: **{{TOKEN_SCOPES}}**.

All tools and resources are filtered by your token's scopes -- this is the
normal state, not a special case. `sudo` is simply one scope among others:
powerful, and ordinary in mechanism. If a tool or the `sudo` virtual param is
not visible, your token lacks the relevant scope. `{{TOOL_PREFIX}}user_get_current`
tells you who you are; the absence of a tool tells you what you cannot reach.

## Troubleshooting

- **"Unknown tool"** -> the name is wrong; `search_tools(...)` to find it.
- **"`call_tool` cannot call itself"** -> `name` must be the *target* tool, not
  `call_tool`; the proxy is invoked directly, never through itself.
- **Tool/resource not visible** -> expected if your token lacks the scope; it is
  filtered, not missing.
- **`APINotFound` / empty resource** -> the target does not exist, is out of
  scope, or is private; `{{TOOL_PREFIX}}user_current_list_repos` shows what you can see.
- **Need full schema** -> `tool_info(name, detail="full")` or
  `read_resource("gitea://tool/{name}/schema")`.
- **Deeper error and edge-case shapes** -> `read_doc("output-format")`.

{{GUIDES_LIST}}
