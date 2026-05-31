# Gitea MCP Server — Architecture

## Overview

This server provides ~200 tools and resources for LLM agents to interact with
Gitea/Forgejo.  Tools and resources are **auto-generated** from the Gitea
Swagger/OpenAPI spec, then **customized** with annotations, validation, label
handling, and cache control.

The codebase is designed to work *with* FastMCP, not around it.  When FastMCP's
API lacks something, we add a conversion/transform layer that can be cleanly
removed when FastMCP catches up.

> **Canonical source** — This document is the primary map for the codebase.
> Before launching exploration subagents, check whether this document already
> answers your question.  Subagents should only be used for dynamic
> investigation (test failures, runtime behavior), not static code structure
> discovery.

---

## Pipeline: Swagger 2.0 → FastMCP Tools & Resources

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐     ┌────────────┐
│ Gitea Server │────▶│ spec_loader  │────▶│ openapi_converter│────▶│ mcp_builder│
│ swagger.v1   │     │ (fetch +     │     │ (Swagger 2→3.1)  │     │ (create    │
│   .json      │     │  parse)      │     │                  │     │ OpenAPI    │
└──────────────┘     └──────────────┘     └──────────────────┘     │ Provider)  │
                                                                    └─────┬──────┘
                                                                          │
                                                    ┌─────────────────────┘
                                                    ▼
                                          ┌──────────────────┐
                                          │  FastMCP Server  │
                                          │  (provider +     │
                                          │   transforms)    │
                                          └────────┬─────────┘
                                                   │
                          ┌────────────────────────┼────────────────────┐
                          ▼                        ▼                    ▼
                  ┌──────────────┐       ┌────────────────┐    ┌──────────────┐
                  │ tool_annotat │       │  tool_filter   │    │ GiteaNamespa │
                  │ or           │       │  (permission-  │    │ ce           │
                  │ (annotations,│       │   based hide)  │    │ (prefix      │
                  │  labels,     │       └────────────────┘    │  tools only) │
                  │  validation) │                              └──────────────┘
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐       ┌─────────────────────┐
                  │ TolerantSearch│      │ Resource Registry   │
                  │ Transform     │      │ (auto-generated +   │
                  │ (lazy loading)│      │  custom overrides)  │
                  └──────────────┘       └─────────────────────┘
```

---

## Module Map

### Core Pipeline

| Module | Role | Public API |
|--------|------|------------|
| `config.py` | Pydantic settings from env vars (GITEA_URL, GITEA_TOKEN, etc.) | `Config` |
| `client.py` | httpx client with retry, rate-limit handling, SSL | `GiteaClient` |
| `openapi_converter.py` | Swagger 2.0 → OpenAPI 3.1 | `convert_swagger_to_openapi_v3` |
| `spec_loader.py` | Fetch spec, convert, apply extensions | `load_and_convert_spec` |
| `mcp_builder.py` | Create `OpenAPIProvider` from spec + client | `create_openapi_provider` |
| `server.py` | Assemble everything, serve via stdio or HTTP | `main()`, `create_mcp_server()` |
| `constants.py` | Centralized magic numbers, cache TTLs, pattern names, scopes | (constants) |
| `logging_config.py` | JSON/text formatter, sensitive-key redaction, log setup | `setup_logging` |
| `exceptions.py` | Exception hierarchy (`GiteaMCPError` → 5 subclasses) | `GiteaAPIError`, `ValidationError`, etc. |
| `format.py` | General-purpose schema-aware markdown formatters (shared by tools & resources) | `_format_as_markdown`, `_format_datetime`, `_format_simple_value` |

### Tool Customization Stack (applied in order)

All tool-related runtime concerns live in `gitea_mcp_server/tools/`:

| Module | What it contains |
|--------|------------------|
| `tools/customize.py` | `customize_component`, title/category generation, hint inference, invalidation |
| `tools/schemas.py` | `derive_output_schema`, `$ref` resolution, text/JSON response detection |
| `tools/errors.py` | error translation, runtime validation runner, `_run_with_error_handling` |
| `tools/labels.py` | string→ID label conversion, label schema updates |
| `tools/examples.py` | schema→example generation, tool schema serialization |
| `tools/search.py` | BM25 search engine + `TolerantSearchTransform`, synthetic `search_tools`/`call_tool`/`tool_info` tools |
| `tools/namespace.py` | `GiteaNamespace` transform (prefixes tools, passes resources through) |

The customization layers as applied during server startup:

| Layer | Module | What it does |
|-------|--------|--------------|
| 1. Annotations | `tools/customize.py` | title, category tag, readOnly/destructive/idempotent hints |
| 2. Error handling | `tools/errors.py` | wraps `run()` to translate HTTP errors to agent-friendly messages |
| 3. Label support | `tools/labels.py` | string-to-ID label conversion, schema updates |
| 4. Validation | `validation.py` | runtime validation (owner/repo format, pagination, etc.) + schema augmentation |
| 5. Cache invalidation | `cache_invalidation.py` | on write, invalidate affected resource cache entries |
| 6. Permissions | `tool_filter.py` | hide tools/resources that exceed token scopes |
| 7. Search/lazy loading | `tools/search.py` | BM25 search with alias expansion, synthetic tools |
| 8. Namespace | `tools/namespace.py` | prefix all tools with `gitea_` (resources pass through unchanged) |
| 9. Response caching | `cache_invalidation.py` middleware | TTL-based caching of resource reads |

### Resource System

| Module | Role |
|--------|------|
| `resources/auto.py` | Auto-generated resources from OpenAPI GET endpoints (raw JSON) |
| `resources/custom.py` | Hand-written Markdown wrapper resources for common URIs |
| `resources/format.py` | Domain-specific resource Markdown formatters (repo, issues, pulls, users, releases) |
| `resources/scope.py` | Scope derivation (`derive_required_scope`) for tools and resources |
| `resources/registry.py` | Passive `ResourceRegistry` catalog class recording what's been registered |
| `mcp_tools.py` | `mcp_list_resources`, `mcp_read_resource`, tool schema resource |

### Server Setup Orchestration (startup-only)

| Module | Role |
|--------|------|
| `server_setup/__init__.py` | Package marker |
| `server_setup/spec_loader.py` | Fetch, convert, extend |
| `server_setup/mcp_builder.py` | Create provider + wire tools (imports from `tools/` and `label_manager`) |
| `server_setup/resource_setup.py` | Orchestrate resource registration |
| `server_setup/permissions.py` | Re-exports from `tool_filter.py` (avoids circular import) |
| `server_setup/mcp_extensions.py` | YAML-based tool customizations (titles, descriptions, params) |

---

## Key Design Decisions

1. **FastMCP providers, not manual tool registration** — The OpenAPI provider
   auto-generates tools from the spec. Customization happens via transforms
   and the `transform_fn` pattern, not by hand-registering each tool.

2. **Lazy loading** — Tools are not listed by default. Agents discover them via
   `search_tools` (BM25). This prevents context pollution from ~200 tools being
   listed at once.  Three synthetic tools (`search_tools`, `call_tool`,
   `tool_info`) are always visible.

3. **Resources pass through namespace** — Resources use the `gitea://` scheme
   directly.  FastMCP's built-in `Namespace` would double-namespace them to
   `gitea://gitea/...`, so `GiteaNamespace` explicitly passes resource URIs
   through unchanged.

4. **Custom resources override auto-generated** — Resources are registered in
   two phases: auto-generated (raw JSON from every GET endpoint) then custom
   (Markdown wrappers for common URIs).  FastMCP's last-registration-wins means
   custom ones replace raw ones at identical URIs.

5. **Response schema wrapping** — FastMCP requires `output_schema` to be
   `type: object`.  All response schemas are wrapped in `{"result": ...}` to
   match the runtime shape.  This is done in `openapi_converter.py` via
   `_wrap_success_response_schemas`.

6. **Cache invalidation via middleware** — Write tools register invalidation
   patterns at startup.  The `CacheInvalidationMiddleware` computes concrete
   URIs from tool arguments and clears them from the response cache after
   successful writes.

7. **Circular-import breaker pattern** — `server_setup/permissions.py` is a thin
   re-export from flat `tool_filter.py`, avoiding a circular import that would
   occur if `server.py` imported `tool_filter.py` directly.  Same pattern for
   `server_setup/logging.py` → `logging_config.py`.

8. **Naming collision resolved** — Two modules once shared the name
   `resource_registry`: the `resources/registry.py` (class `ResourceRegistry`
   catalog) and `server_setup/resource_registry.py` (orchestration function).
   The latter was renamed to `resource_setup.py` to eliminate confusion.

9. **Constants consolidation** — `TAG_TO_SCOPE`, `TOOL_INVALIDATION_PATTERNS`,
   and BM25 search configuration (`SEARCH_*`) were moved from scattered module-level
   definitions into `constants.py`, the single source of truth for all magic values.

---

## Response Content-Type Handling

Gitea's API mixes content types: most endpoints return JSON, but some return
plain text (diffs, patches), HTML (signing keys), or binary blobs (file
downloads).  Handling this correctly requires coordination across four stages.

### Stage 1 — Spec Conversion (`openapi_converter.py`)

Swagger 2.0 specifies response types via the `produces` field (per-operation or
top-level).  `convert_responses()` uses `produces` to set the OpenAPI 3.1
`content` type on each response — but only if `produces` is propagated to the
operation before `remove_swagger_fields()` strips it.

If no `produces` is found, the converter defaults to `application/json`. This is
correct for ~95% of endpoints, but silently wrong for the ~12 non-JSON
endpoints if `produces` propagation is missed.

### Stage 2 — Schema Wrapping (`openapi_converter.py:_wrap_success_response_schemas`)

All `application/json` response schemas are wrapped in:
```
{"type": "object", "properties": {"result": <original_schema>}}
```

This matches the runtime shape FastMCP produces (see Stage 4) and satisfies the
MCP SDK's requirement that `output_schema` be `type: object`.

Non-JSON responses (text/plain, text/html, application/octet-stream) are
**implicitly skipped** — `_wrap_response_schema` only looks at
`content["application/json"]`, so if that key is absent the function returns
without wrapping.  No special check is needed.

### Stage 3 — Output Schema Derivation (`tool_annotator.py:_get_success_schema`)

`derive_output_schema()` resolves the response schema from the OpenAPI spec for
each tool.  For JSON endpoints, it returns the wrapped schema from Stage 2.

For non-JSON endpoints, the spec has no `application/json` content entry, so
`_get_success_schema` finds nothing and returns `None`.  The tool ends up with
`output_schema = None`, which tells the MCP SDK to skip output validation.

Optionally, a lightweight schema can be set here manually:
```python
{"type": "object", "properties": {"result": {"type": "string"}}}
```
This gives agents a useful `output_example` while still matching the runtime
`{"result": text}` shape.

### Stage 4 — Runtime Execution (`tool_annotator.py:customize_component`)

At runtime, FastMCP's `OpenAPITool.run()` sends the HTTP request and receives
the response:

- **JSON response**: `response.json()` succeeds → FastMCP creates
  `ToolResult(content=str(data), structured_content=data)` → MCP SDK validates
  against `output_schema` → passes.

- **Non-JSON response** (text/plain, binary): `response.json()` raises
  `JSONDecodeError` → FastMCP falls back to
  `ToolResult(content=text, structured_content=None)` → if `output_schema` is
  set, MCP SDK rejects with *"Output validation error: outputSchema defined but
  no structured output returned"*.

  When `output_schema = None` (from Stage 3), validation is skipped. The
  `transform_fn` in `customize_component()` wraps the text in
  `{"result": raw_text}` for client consistency with JSON endpoints.

### The `x-fastmcp-wrap-result` Extension

FastMCP's `OpenAPIProvider` checks the spec for an `x-fastmcp-wrap-result`
extension on each operation.  When present (set during
`_wrap_success_response_schemas`), FastMCP wraps the raw API response in
structured content matching the output schema.  This is how `{"result": data}`
is produced at runtime for JSON endpoints.

For non-JSON endpoints, this extension is absent (no wrapping was applied in
Stage 2), so `output_schema = None` is paired with the `transform_fn` fallback
to produce the same `{"result": text}` shape.

---

## Data Flow: Agent Calls a Tool

```
Agent: call_tool("gitea_issue_create_issue", {...})
  │
  ├─▶ TolerantSearchTransform intercepts "call_tool"
  │     └─▶ ctx.fastmcp.call_tool(name, arguments)
  │
  ├─▶ CacheInvalidationMiddleware.on_call_tool()
  │     └─▶ executes tool
  │     └─▶ on success: compute URIs to invalidate → clear cache
  │
  └─▶ TransformedTool (from tool_annotator)
        ├─▶ validate arguments (validation.py)
        ├─▶ convert label strings→IDs (label_manager)
        ├─▶ OpenAPITool.run() → httpx request to Gitea API
        ├─▶ wrap response in {"result": ...}
        └─▶ on error: translate httpx errors to agent-friendly messages
```

---

## Agent-Facing Documentation

The file `gitea_mcp_server/docs/agent_instructions.md` is loaded as FastMCP
server instructions and served as context to agents at connection time.  It
explains how to discover and use tools/resources from the agent's perspective.
