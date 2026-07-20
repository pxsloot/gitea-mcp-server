---
audience: developer
type: reference
covers: Token scope -> tool/resource visibility, virtual param gating, scope derivation
---

# Scope Model

> How Gitea token scopes control tool/resource visibility and virtual parameter
> availability in the MCP server.

## Overview

Gitea's API uses [OAuth2-style token scopes](https://docs.gitea.com/development/api-usage#scopes)
to authorize operations (`read:repository`, `write:issue`, `sudo`, etc.).
This server translates those scopes into three distinct control mechanisms:

| Mechanism | Controls | Module |
|-----------|----------|--------|
| Tool/resource filtering | Which tools and resources are visible to the agent | `spec_loader.py` (route_map_fn, Phase 2 spec-level filtering) |
| Virtual param gating | Whether agent-facing virtual params (e.g. `sudo`) appear in tool schemas | `tools/virtual_params.py` (`apply_scope_filter`) |
| Scope derivation | How a required scope is computed from Swagger tags + HTTP method | `scope.py` (`derive_required_scope`) |

At startup the server fetches the active token's scopes once, then uses them
to configure all three mechanisms. The result is cached for the lifetime of
the server process.

---

## Startup Flow

```
Server startup (spec prep — Phase 2)
  │
  ├─ 1. load_and_convert_spec(gitea_client, config)
  │      ├─ fetch_token_scopes(gitea_client, token)
  │      │    ├─ GET /user                → find current username
  │      │    └─ GET /users/{name}/tokens → match token by last 8 chars
  │      ├─ load_exclusion_config(...)    → exclude/include patterns
  │      ├─ compute_filtered_tools_info(...)  → prediction data
  │      │    (drives rich error messages for synthetic tools)
  │      └─ _compute_excluded_routes(...)    → set of (path, METHOD)
  │           to drop via route_map_fn
  │
  ├─ 2. create_openapi_provider(..., excluded_routes=...)
  │      → route_map_fn drops filtered operations BEFORE FastMCP
  │        builds the tools (deprecated + scope + config-excluded)
  │
  └─ 3. apply_scope_filter(available_scopes)
         → sets .visible on each VirtualParam (e.g. sudo)
```

Steps 1–2 happen in `spec_loader.load_and_convert_spec()` and
`mcp_builder.create_openapi_provider()`. Step 3 (virtual-param gating) runs
in `server.py:_apply_virtual_param_scope_filter()`.

If the token's scopes cannot be fetched (auth failure, network error), no
filtering is applied and **all tools remain visible**. This is fail-open by
design — an agent with limited diagnostics is better than a server that
silently hides half its tools.

---

## Scope Derivation

**Module**: `gitea_mcp_server/scope.py`

```
derive_required_scope(swagger_tags, method) → str | None
```

| Input | Source | Example |
|-------|--------|---------|
| `swagger_tags` | OpenAPI `tags` array on the operation | `{"repository", "issue"}` |
| `method` | HTTP method | `"GET"`, `"POST"`, `"DELETE"` |

Logic:

1. Scan `swagger_tags` for the first tag that appears in `TAG_TO_SCOPE`
   (defined in `constants.py`). That gives the scope *resource name*.
2. If the resource name is `"sudo"`, return `"sudo"` regardless of method.
3. If method is GET/HEAD/OPTIONS → `"read:{resource}"`.
4. Otherwise → `"write:{resource}"`.

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

**On tools**: `mcp_builder.py:_customize_metadata()` stores it in
`component.meta["required_scope"]`, alongside other metadata.

**On resources**: auto-generated resources (`resources/auto.py`) and custom
resources (`resources/custom.py`) both use the `scope_meta()` helper to set
`meta={"required_scope": "..."}` on the resource registration.

---

## `scope_meta()` helper

**Module**: `gitea_mcp_server/scope.py`

```python
def scope_meta(required_scope: str | None) -> dict[str, Any]:
    return {"required_scope": required_scope}
```

A one-line factory that signals *intent* — "this dict is scope metadata" —
rather than inlining `{"required_scope": x}` at every call site.

Used in 14 places across `resources/auto.py` and `resources/custom.py`,
typically merged into a larger metadata dict:

```python
# From resources/custom.py
_meta = {"cache_ttl": CACHE_TTL_REPOSITORY, **scope_meta("read:repository")}
```

The re-export chain (`scope.py` → `resources/scope.py` → `resources/__init__.py`)
follows the circular-import breaker pattern documented in `ARCHITECTURE.md`
design decision #7: the flat `scope.py` avoids package-level imports that would
create cyclic dependencies between the `tools/` and `resources/` packages.

---

## Spec-Level Filtering (tools and resources)

**Module**: `gitea_mcp_server/server_setup/spec_loader.py` +
`gitea_mcp_server/server_setup/mcp_builder.py`

As of Phase 2 of the Spec-Level Filtering milestone (#472), tool/resource
visibility is decided **once at spec-prep time**, not at query time via a
runtime transform. `load_and_convert_spec()` fetches token scopes and the
exclusion config, then computes the set of `(path, UPPER_METHOD)` tuples to
exclude (`_compute_excluded_routes`). `create_openapi_provider()` receives
that set and applies it through FastMCP's public `route_map_fn`, which returns
`MCPType.EXCLUDE` for each filtered operation — so FastMCP never builds a tool
or resource for it.

The same `compute_filtered_tools_info()` call that produces the excluded set
also produces the `x-mcp-filtered-tools` prediction data used by synthetic
tools (`tool_info`, `call_tool`, `search_tools`) to give rich error messages.
Both the *visibility* decision and the *error message* data come from one
source, so they can never diverge.

### Scope sufficiency rules

These rules (in `scope.has_sufficient_scope`) determine whether an operation
is excluded by scope:

| Required | Available | Result |
|----------|-----------|--------|
| `None` | anything | ✅ allowed |
| anything | `"sudo"` | ✅ allowed |
| anything | `"all"` | ✅ allowed (Gitea full-access shorthand) |
| `"read:repository"` | `"read:repository"` | ✅ exact match |
| `"read:repository"` | `"write:repository"` | ✅ write implies read |
| `"write:issue"` | `"read:issue"` | ❌ read does not imply write |

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
| `scope.py` | `derive_required_scope()` + `scope_meta()` + `has_sufficient_scope()` — the core utilities |
| `resources/scope.py` | Re-exports from `scope.py` for package-internal consumers |
| `constants.py` | `TAG_TO_SCOPE` mapping table |
| `server_setup/spec_loader.py` | `fetch_token_scopes()` + `_compute_excluded_routes()` — spec-prep filtering |
| `tools/virtual_params.py` | `apply_scope_filter()` — virtual param visibility |
| `tools/exclusion.py` | `load_exclusion_config()` + `matches_any()`/`matches_pattern()` — exclusion config loading & matching |
| `server.py` | Orchestration in `create_mcp_server()` + `_apply_virtual_param_scope_filter()` |
| `mcp_builder.py` | `create_openapi_provider()` applies `excluded_routes` via `route_map_fn`; stores derived scope in `component.meta["required_scope"]` |
| `resources/auto.py` | Uses `derive_required_scope()` + `scope_meta()` for auto-generated resources |
| `resources/custom.py` | Uses `scope_meta()` for custom wrapper resources |

### Filtering happens at spec-prep time

Tool/resource visibility is no longer a query-time transform. The canonical
server transform chain (documented in `docs/ARCHITECTURE.md`) is now just
TolerantSearch → GiteaNamespace → ExtensionMetadata. Filtering is applied
earlier, in `route_map_fn` during provider creation, so filtered operations
never become tools/resources.

---

## References

- `gitea_mcp_server/scope.py` — derivation + scope_meta + sufficiency check
- `gitea_mcp_server/server_setup/spec_loader.py` — fetch_token_scopes, excluded-routes computation
- `gitea_mcp_server/tools/virtual_params.py` — apply_scope_filter
- `gitea_mcp_server/constants.py` — TAG_TO_SCOPE
- `gitea_mcp_server/server.py`::`_apply_virtual_param_scope_filter` — startup orchestration
- `docs/ARCHITECTURE.md` — design decision #7 (circular-import breaker), module map
- `docs/DEVELOPMENT.md` — "Scope-gating" section under virtual params
