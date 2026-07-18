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
`gitea_read_resource`, `gitea_search_resources`, `gitea_read_doc`, `gitea_search_docs`,
`gitea_resolve_type`.
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

## Parameters: never guess, always confirm

There are ~400 tools and the exact parameters differ per tool. **Do not guess a
parameter name or type from memory.** The authoritative contract for any tool
is one call away:

```
tool_info("gitea_issue_create_issue")
```

`tool_info` returns the full parameter list (types, which are required, enums,
and validation patterns), a compact `output_example`, the tool's annotations,
and its tags. Trust that over anything you assume. Use `tool_info(name,
detail="full")` only when you need the complete JSON Schema -- it is hundreds of
lines on large tools, so run it rarely (once on a small tool to learn the
shape, then trust the compact example day to day).

That said, a handful of parameters recur across almost every tool because they
mirror Gitea's API. Knowing these removes most of the uncertainty cheaply:

| Parameter   | Type    | Notes |
|-------------|---------|-------|
| `owner`     | string  | repo owner; pattern `^[a-zA-Z0-9]+([._-][a-zA-Z0-9]+)*$`, 1-50 chars |
| `repo`      | string  | repo name; same pattern rules, 1-100 chars |
| `index`/`id`| integer | the resource id (int64) -- `index` for issues/PRs, `id` elsewhere |
| `page`      | integer | 1-based page number for list/search tools (minimum 1) |
| `limit`     | integer | page size for list/search tools |
| `format`    | string  | `json` \| `markdown` (default) \| `raw` -- see Output format below |
| `sudo`      | (virtual) | appears only if your token has the admin/`sudo` scope |

If a tool takes `owner`/`repo`, it almost certainly takes them as required
strings. If it lists or searches, it almost certainly takes `page`+`limit`.
Confirm the rest -- especially optional fields, enums, and the exact resource
id parameter name -- with `tool_info`.

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
- `gitea://types/{typeName}`                -> resolved type schema (JSON with full details)

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

Note on output shape: `output_example` and `format=json` results reference
nested objects with `$ref:Type` markers (e.g. `$ref:User`, `$ref:Label`). These
are not inline -- the full object is returned by the live API, but the example
uses references to stay compact. Don't expect a flat structure; read the nested
fields from the actual response.

When you see a ``$ref:TypeName`` marker and need to know what fields that type
contains, use ``call_tool("gitea_resolve_type", {"name": "TypeName"})`` or
read ``gitea://types/{TypeName}`` for a cached JSON read. The ``resolve_type``
tool also shows which tools return or accept each type. Run
``tool_info("gitea_resolve_type")`` for the full parameter and output schema.

## Tool annotations

Every tool carries four hints. Inspect them via `tool_info(name)` -- the
response includes the annotations object. These server instructions are the
only doc injected at connection; everything else you need is reachable through
the discovery tools (`search_tools`, `search`, `tool_info`) and the workflow
guides (`read_doc`).

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

## Edge cases you will hit (and how they look)

These are the real shapes returned by this server. Knowing them saves a round-
trip of confusion:

- **Empty list is `[]`, not an error.** A list/search tool that matches nothing
  returns an empty JSON array (or an empty Markdown section). That means *no
  matching items* -- it is different from a tool being hidden by scope. Don't
  treat `[]` as "I'm filtered out."

- **`APINotFound` means the target doesn't exist -- or is out of scope.**
  Example:
  ```
  Error calling tool 'gitea_issue_get_issue': APINotFound is a not found error response
  Details: The target couldn't be found.
  ```
  This same error fires for a non-existent repo, a wrong issue `index`, or a
  repo your token cannot see. The error does **not** tell you which -- reason
  about it: if `gitea_user_current_list_repos` shows the repo, the 404 is a bad
  `index`; if the repo isn't listed there, it's scope/visibility.

- **Bad label names fail loudly with a helpful message.** Creating an issue or
  PR with a label that doesn't exist in the repo returns:
  ```
  Error calling tool 'gitea_issue_create_issue': Unknown label name(s): ['NonExistentLabelXYZ'].
  Available labels for docker/docker_python:
  <empty -- repo has no labels yet>
  Use list_labels(docker, docker_python) or read gitea://repos/docker/docker_python/labels to see details.
  ```
  Prefer integer label **IDs** over names for reliability, and confirm valid
  labels via `gitea_issue_list_labels` or the `gitea://repos/{owner}/{repo}/labels`
  resource before creating.

- **`search` returns typed, cross-cutting results.** Unlike `search_tools`,
  `search("create issue")` returns a mixed list tagged `tool` / `doc` /
  `resource`, each with an `Access Uri`. Route each hit to the right access
  path: `call_tool` for tools, `read_doc` for guides, `read_resource` for data.

- **Pagination is explicit.** List/search tools take `page` (1-based) and
  `limit`. There is no auto-iteration; to read all pages, loop `page` upward
  until you get `[]`. A short page is not necessarily the last one unless the
  next page is empty.

## Troubleshooting

- **"Unknown tool"** -> the name is wrong; `search_tools(...)` to find it.
- **No search results** -> simplify to one keyword; or the tool is scope-filtered out.
- **Tool/resource not visible** -> expected if your token lacks the scope; it is filtered, not missing.
- **Empty resource** -> reflects permissions; use `gitea_user_current_list_repos` for private repos.
- **"Only administrators allowed to sudo"** -> your token lacks the `sudo`/`all` scope; the `sudo` param is correctly hidden.
- **Need full schema** -> `tool_info(name, detail="full")` or `read_resource("gitea://tool/{name}/schema")`.

## Workflow Guides

These guides explain Forgejo workflows and concepts beyond individual API tools:

| Guide | Description |
|-------|-------------|
| `actions` | Forgejo Actions -- runner setup, workflow syntax, secrets, variables, OIDC se... |
| `authentication` | Authentication methods in Gitea/Forgejo -- OAuth2 providers, LDAP, PAM, OIDC,... |
| `branch-protection` | Branch protection rules (force push, approvals, merge restrictions), glob pat... |
| `issue-tracking` | Issue tracking in Gitea/Forgejo -- creating and managing issues, milestones, ... |
| `labels` | How Gitea/Forgejo labels work -- creating, archiving, scoped/exclusive labels... |
| `organizations` | Managing organizations and teams in Gitea/Forgejo -- creating orgs, team type... |
| `package-registry` | Package registry in Gitea/Forgejo -- supported formats, authentication, publi... |
| `permissions` | Permission model for repositories -- collaborator roles, organization teams, ... |
| `product-documentation` | Use the repository wiki as a product documentation layer - holding vision, PR... |
| `pull-requests` | Pull request workflow in Gitea/Forgejo -- creating PRs, merge styles (merge/s... |
| `repositories` | Repository lifecycle management -- creation, mirrors (push/pull), push-to-cre... |
| `server-admin` | Gitea/Forgejo server administration -- configuration cheat sheet, moderation ... |
| `templates` | Issue and pull request templates in Gitea/Forgejo -- YAML-based forms, markdo... |
| `token-scopes` | How Gitea/Forgejo API tokens work, the scope model, repository access restric... |
| `webhooks` | Webhooks in Gitea/Forgejo -- event types, payload structure, creation, and ma... |
| `wiki` | Built-in wiki in Gitea/Forgejo -- git-backed storage, permissions, markdown c... |

Use `search_docs(query)` to find guides by topic, or `read_doc(topic)` to read one.
Guides are also available as resources at `gitea://docs/guide/{{topic}}`.
