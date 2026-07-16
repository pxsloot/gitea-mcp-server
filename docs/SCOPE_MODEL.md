# Scope Model

> How Gitea token scopes control tool/resource visibility and virtual parameter
> availability in the MCP server.

## Overview

Gitea's API uses [OAuth2-style token scopes](https://docs.gitea.com/development/api-usage#scopes)
to authorize operations (`read:repository`, `write:issue`, `sudo`, etc.).
This server translates those scopes into three distinct control mechanisms:

| Mechanism | Controls | Module |
|-----------|----------|--------|
| Tool/resource filtering | Which tools and resources are visible to the agent | `tool_filter.py` (PermissionFilterTransform) |
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
  ├─ 1. fetch_token_scopes(gitea_client, token)
  │      ├─ GET /user              → find current username
  │      └─ GET /users/{name}/tokens → match token by last 8 chars
  │
  ├─ 2. apply_scope_filter(available_scopes)
  │      → sets .visible on each VirtualParam (e.g. sudo)
  │
  └─ 3. mcp.add_transform(PermissionFilterTransform(available_scopes))
         → filters tools/resources at query time
```

Step 1 is in `server.py:_apply_permission_filter()`. Steps 2 and 3 happen
immediately after, in the same function.

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

## Permission Filtering (tools and resources)

**Module**: `gitea_mcp_server/tool_filter.py`

**Class**: `PermissionFilterTransform(Transform)`

A FastMCP `Transform` that intercepts `list_tools`, `get_tool`,
`list_resources`, `list_resource_templates`, `get_resource`, and
`get_resource_template`. For each item it reads `item.meta["required_scope"]`
and checks `_has_sufficient_scope(required, available)`.

### Scope sufficiency rules

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

---

## Module Map

| File | Responsibility |
|------|---------------|
| `scope.py` | `derive_required_scope()` + `scope_meta()` — the two core utilities |
| `resources/scope.py` | Re-exports from `scope.py` for package-internal consumers |
| `constants.py` | `TAG_TO_SCOPE` mapping table |
| `tool_filter.py` | `PermissionFilterTransform` + `fetch_token_scopes()` + `_has_sufficient_scope()` |
| `tools/virtual_params.py` | `apply_scope_filter()` — virtual param visibility |
| `tools/exclusion.py` | `ExclusionTransform` — separate concern (exclude/include by YAML pattern), runs before scope filtering |
| `server.py` | Orchestration in `_apply_permission_filter()` |
| `mcp_builder.py` | Stores derived scope in `component.meta["required_scope"]` at customization time |
| `resources/auto.py` | Uses `derive_required_scope()` + `scope_meta()` for auto-generated resources |
| `resources/custom.py` | Uses `scope_meta()` for custom wrapper resources |

### Transform execution order

When the client lists tools or resources, transforms run in this order:

1. **TolerantSearchTransform** — lazy-loading search
2. **GiteaNamespace** — adds `gitea_` prefix
3. **ExtensionMetadataTransform** — YAML overrides
4. **ExclusionTransform** — exclude/include by config
5. **PermissionFilterTransform** — scope filtering (always last, so it sees
   the fully resolved set of visible tools and can filter them by scope)

---

## References

- `gitea_mcp_server/scope.py` — derivation + scope_meta
- `gitea_mcp_server/tool_filter.py` — PermissionFilterTransform, fetch_token_scopes, scope matching
- `gitea_mcp_server/tools/virtual_params.py` — apply_scope_filter
- `gitea_mcp_server/constants.py` — TAG_TO_SCOPE
- `gitea_mcp_server/server.py`::`_apply_permission_filter` — startup orchestration
- `docs/ARCHITECTURE.md` — design decision #7 (circular-import breaker), module map
- `docs/DEVELOPMENT.md` — "Scope-gating" section under virtual params
