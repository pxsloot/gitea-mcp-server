---
audience: developer
type: reference
covers: Token scope -> tool/resource visibility, virtual param gating, scope derivation
---

# Scope Model

> How Gitea token scopes control tool/resource visibility and virtual parameter
> availability in the MCP server.

## What this doc is NOT

This doc covers the scope/permission model. If you need:

| Topic | See |
|-------|-----|
| Pipeline overview and spec-level filtering in the server's data flow | `docs/ARCHITECTURE.md` |
| How to add a virtual parameter (e.g. `sudo`) and wire scope gating | `docs/DEVELOPMENT.md` |
| Full semantics of annotation fields (title, tags, hints) and how they are inferred | `docs/TOOL_ANNOTATIONS.md` |

## Overview

Gitea's API uses [OAuth2-style token scopes](https://docs.gitea.com/development/api-usage#scopes)
to authorize operations (`read:repository`, `write:issue`, `sudo`, etc.).
This server translates those scopes into three distinct control mechanisms:

| Mechanism | Controls | Module |
|-----------|----------|--------|
| Tool/resource filtering | Which tools and resources are visible to the agent | `spec_loader.py` (route_map_fn, spec-level filtering) |
| Virtual param gating | Whether agent-facing virtual params (e.g. `sudo`) appear in tool schemas | `tools/virtual_params.py` (`apply_scope_filter`) |
| Scope derivation | How a required scope is computed from Swagger tags + HTTP method | `scope.py` (`derive_required_scope`) |

At startup the server fetches the active token's scopes once, then uses them
to configure all three mechanisms. The result is cached for the lifetime of
the server process.

---

## Startup Flow

```
Server startup
  │
  ├─ 1. load_and_convert_spec(gitea_client, config)
  │      ├─ fetch_token_scopes(gitea_client, token)
  │      │    ├─ GET /user                → find current username
  │      │    └─ GET /users/{name}/tokens → match token by last 8 chars
  │      ├─ load_exclusion_config(...)    → exclude/include patterns
  │      ├─ compute_filtered_tools_info(...)  → prediction data
  │      │    (drives rich error messages + resource filtering)
  │      └─ _compute_excluded_routes(...)    → set of (path, METHOD)
  │           to drop via route_map_fn
  │
  ├─ 2. create_openapi_provider(..., excluded_routes=...)
  │      → route_map_fn drops filtered tool operations
  │
  ├─ 3. register_all_resources(..., filtered_tools_info=...,
  │      │                       available_scopes=...)
  │      ├─ auto resources: skip if operationId in filtered_tools_info
  │      └─ custom resources: skip if has_sufficient_scope() fails
  │
  └─ 4. apply_scope_filter(available_scopes)
         → gates virtual params (e.g. sudo)
```

Steps 1–2 handle tool filtering in `spec_loader.py` and `mcp_builder.py`.
Step 3 handles resource filtering in `resource_setup.py` and
`resources/auto.py`/`custom.py`.  Step 4 gates virtual params in
`tools/virtual_params.py`, called from `server.py:_apply_virtual_param_scope_filter()`.

All filtering happens at spec-prep time — no runtime transform filters
tools or resources at query time.

If the token's scopes cannot be fetched (auth failure, network error), no
filtering is applied and **all tools and resources remain visible**. This is
fail-open by design — an agent with limited diagnostics is better than a
server that silently hides half its tools.

---

## Scope Derivation

**Module**: `gitea_mcp_server/scope.py`

```
derive_required_scope(swagger_tags, method) → frozenset[str] | None
```

| Input | Source | Example |
|-------|--------|---------|
| `swagger_tags` | OpenAPI `tags` array on the operation | `{"repository", "user"}` |
| `method` | HTTP method | `"GET"`, `"POST"`, `"DELETE"` |

Logic:

1. Map every tag that appears in `TAG_TO_SCOPE` (defined in `constants.py`)
   to its scope *resource name*.  Unknown tags are ignored.
2. A tag mapping to `"sudo"` contributes the literal `"sudo"` regardless of
   method.
3. For every other tag, a safe method (GET/HEAD/OPTIONS) contributes
   `"read:{resource}"`; otherwise `"write:{resource}"`.
4. Return the **set** of contributed scopes, or `None` when no tag maps.

Because the result is a set built by mapping every tag (never a first match),
the outcome is deterministic and independent of tag iteration order / process
hash seed.

### Multi-tag operations require **all** scopes

An operation carrying several scope tags requires a conjunction, not one of
them.  Gitea/Forgejo enforce *all* scope categories declared for a route:
`tokenRequiresScopes(...)` delegates to `AccessTokenScope.HasScope`, which
loops over the required scopes and fails if any is missing (its
"at least one" error string is stale — the code is conjunctive).  Parent-group
middleware adds its own categories, so e.g. every route under the `/user`
group requires the `user` category in addition to any route-level category.

The spec's per-operation `tags` are a **subset** of the true categories: a
per-operation tag list cannot express parent-group middleware.  A usable token
holds every true category and therefore every tag, so conjunctive derivation
never hides a tool the token can actually use.  The residual is fail-open: an
operation whose tags omit a category (e.g. `GET /user/repos`, tagged `[user]`
but truly requiring `user` **and** `repository`) may be shown to a token that
lacks the omitted scope, and the API then answers 403.

The converter repairs the known under-reported cases at spec-prep time —
**Rule D** in `openapi_converter/normalize.py` appends the missing scope tags
from a table curated from the router (`_SCOPE_TAG_OVERRIDES`).  It is a
source-driven exception like Rule C, with a drift guard that warns both when a
table entry's operation disappears and when its scope tags already match (the
upstream annotation was fixed).  The rule also corrects upstream **mis-tags**
— e.g. `GET /repos/{owner}/{repo}/issues/pinned` is tagged `repository` but
lives in the Issue group — by removing scope tags the router does not require,
while preserving non-scope tags.  This keeps `scope.py` generic: it only ever
reads operation tags.

### TAG_TO_SCOPE mapping (`constants.py`)

| Swagger tag | Scope resource name |
|-------------|-------------------|
| `admin` | `sudo` |
| `repository`, `settings` | `repository` |
| `issue` | `issue` |
| `organization` | `organization` |
| `user` | `user` |
| `notification` | `notification` |
| `package` | `package` |
| `activitypub` | `activitypub` |
| `miscellaneous` | `misc` |

### Where the derived scope is stored

**On tools**: nowhere.  Tool scope is decided solely by spec-level filtering
(see below); the tool's ``meta`` carries no scope field.  This keeps one
source of truth — a per-tool meta copy previously existed and could drift
from the filtering decision.

**On resources**: auto-generated resources (`resources/auto.py`) and custom
resources (`resources/custom.py`) use the ``ResourceMeta`` dataclass
(``resources/meta.py``) for typed registration metadata.  ``ResourceMeta``
wraps ``required_scopes`` (a list, or ``None``) alongside ``size_hint``,
``default_detail``, and ``optional_params``, serialised via ``.to_dict()``:

```python
_meta = ResourceMeta(required_scopes=["read:repository"], size_hint="medium").to_dict()
```

The re-export chain (`scope.py` → `resources/scope.py` → `resources/__init__.py`)
follows the circular-import breaker pattern documented in `ARCHITECTURE.md`
design decision #7: the flat `scope.py` avoids package-level imports that would
create cyclic dependencies between the `tools/` and `resources/` packages.

---

## Spec-Level Filtering (tools and resources)

**Module**: `gitea_mcp_server/server_setup/spec_loader.py` +
`gitea_mcp_server/server_setup/mcp_builder.py`

Tool/resource visibility is decided **once at spec-prep time**, not at query
time via a runtime transform. `load_and_convert_spec()` fetches token scopes
and the exclusion config, then computes the set of `(path, UPPER_METHOD)`
tuples to exclude (`_compute_excluded_routes`). `create_openapi_provider()`
receives that set and applies it through FastMCP's public `route_map_fn`,
which returns `MCPType.EXCLUDE` for each filtered operation — so FastMCP never
builds a tool or resource for it.

The same `compute_filtered_tools_info()` call that produces the excluded set
also produces the `x-mcp-filtered-tools` prediction data used by synthetic
tools (`tool_info`, `call_tool`, `search_tools`) to give rich error messages.
Both the *visibility* decision and the *error message* data come from one
source, so they can never diverge.

### Scope sufficiency rules

`scope.has_sufficient_scope(required, available)` takes a *collection* of
required scopes and passes only when **every** one is satisfied:

| Required | Available | Result |
|----------|-----------|--------|
| `None` / empty | anything | ✅ allowed |
| anything | `"sudo"` | ✅ allowed |
| anything | `"all"` | ✅ allowed (Gitea full-access shorthand) |
| `{"read:repository"}` | `"read:repository"` | ✅ exact match |
| `{"read:repository"}` | `"write:repository"` | ✅ write implies read |
| `{"write:issue"}` | `"read:issue"` | ❌ read does not imply write |
| `{"write:repository", "write:user"}` | `"write:repository"` | ❌ one scope missing |
| `{"write:repository", "write:user"}` | both | ✅ all present |

The per-scope rules are applied independently and combined with `all(...)`.

---

## Virtual Parameter Scope Gating

**Module**: `gitea_mcp_server/tools/virtual_params.py`

**Function**: `apply_scope_filter(available_scopes)`

Virtual parameters are synthetic params that appear in the tool schema but are
stripped before the HTTP request. The `sudo` param (user impersonation) is
scope-gated: it's only useful when the token actually has the `sudo` scope.

`apply_scope_filter` iterates every registered `VirtualParam` and sets
`.visible = True/False` based on whether `required_scope` is in the available
scopes (or `"all"` is present). `inject_into()` then only adds visible params
to tool schemas.

To add a new scope-gated param:

```python
_VIRTUAL_PARAMS["admin_mode"] = VirtualParam(
    schema={"type": "boolean"},
    default=False,
    description="Enable admin mode.",
    required_scope="sudo",  # ← this is all you need
)
```

That's it. `apply_scope_filter` picks it up automatically — no other file
changes needed.

---

## Module Map

| File | Responsibility |
|------|---------------|
| `scope.py` | `derive_required_scope()` + `has_sufficient_scope()` — core utilities |
| `resources/scope.py` | Re-exports from `scope.py` for package-internal consumers |
| `constants.py` | `TAG_TO_SCOPE` mapping table |
| `openapi_converter/normalize.py` | Rule D — reconciles under-reported/mis-tagged scope tags (`_SCOPE_TAG_OVERRIDES`) |
| `server_setup/spec_loader.py` | `load_exclusion_config()` + `fetch_token_scopes()` + `_compute_excluded_routes()` |
| `tools/virtual_params.py` | `apply_scope_filter()` — virtual param visibility |
| `tools/filter_info.py` | `compute_filtered_tools_info()` — single source of truth for tool/resource visibility |
| `server.py` | Orchestration in `create_mcp_server()` → threads `filtered_tools_info` and `available_scopes` to registration |
| `server_setup/resource_setup.py` | Orchestrates resource registration; passes filtered data to auto + custom |
| `resources/auto.py` | Registers auto-generated resources; skips filtered operationIds via `filtered_tools_info` |
| `resources/custom.py` | Registers custom wrapper resources; skips via `has_sufficient_scope()` against `available_scopes` |
| `tools/exclusion.py` | Pattern-matching helpers (`matches_any`, `matches_pattern`) — config loading moved to `spec_loader.py` |

### Filtering happens at spec-prep time

Tool/resource visibility is no longer a query-time transform. The canonical
server transform chain (documented in `docs/ARCHITECTURE.md`) is now just
TolerantSearch → GiteaNamespace → ExtensionMetadata. Filtering is applied
at spec-prep time:

- **Tools**: `route_map_fn` drops filtered operations during provider creation.
- **Auto resources**: `register_auto_generated_resources` skips operations whose
  ``operationId`` appears in ``filtered_tools_info["filtered"]``.
- **Custom resources**: ``register_custom_resources`` skips resources whose
  ``required_scopes`` are not satisfied by the token's available scopes.

Tools and auto resources use the same underlying data
(``filtered_tools_info``), so a tool and its auto resource are always filtered
by the same decision.  Custom resources declare their own ``required_scopes``
and are gated independently against ``available_scopes`` — the declaration is
hand-written, so it must match the API endpoint it wraps (it is not derived
from the spec).

---

## References

- `gitea_mcp_server/scope.py` — scope derivation + sufficiency check
- `gitea_mcp_server/server_setup/spec_loader.py` — fetch_token_scopes, excluded-routes computation
- `gitea_mcp_server/tools/virtual_params.py` — apply_scope_filter
- `gitea_mcp_server/constants.py` — TAG_TO_SCOPE
- `gitea_mcp_server/server.py`::`_apply_virtual_param_scope_filter` — startup orchestration
- `docs/ARCHITECTURE.md` — design decision #7 (circular-import breaker), module map
- `docs/DEVELOPMENT.md` — "Scope-gating" section under virtual params
