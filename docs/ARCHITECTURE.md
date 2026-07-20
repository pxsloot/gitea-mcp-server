---
audience: developer
type: explanation
covers: Pipeline (Swagger 2.0 -> FastMCP), module map, design decisions, content-type handling, runtime flows
---

# Gitea MCP Server -- Architecture

## Overview

This server provides ~200 tools and resources for LLM agents to interact with
Gitea/Forgejo.  Tools and resources are **auto-generated** from the Gitea
Swagger/OpenAPI spec, then **customized** with annotations, validation, label
handling, and cache control.

The codebase is designed to work *with* FastMCP, not around it.  When FastMCP's
API lacks something, we add a conversion/transform layer that can be cleanly
removed when FastMCP catches up.

> **Canonical source** -- This document is the primary map for the codebase.
> Before launching exploration subagents, check whether this document already
> answers your question.  Subagents should only be used for dynamic
> investigation (test failures, runtime behavior), not static code structure
> discovery.

---

## Pipeline: Swagger 2.0 → FastMCP Server

```
┌──────────────┐
│ Gitea Server │
│ swagger.json │
└──────┬───────┘
       │
       ▼
┌───────────────────────────────────────────────────┐
│             spec_loader                           │
│  load_and_convert_spec()                          │
│  ┌───────────┐   ┌─────────────────────────────┐  │
│  │ fetch +   │──▶│ openapi_converter           │  │
│  │ parse     │   │  Swagger 2.0 → OpenAPI 3.1  │  │
│  └───────────┘   │  + wrap response schemas    │  │
│                  │  + apply param extensions   │  │
│                  └───────────┬─────────────────┘  │
└──────────────────────────────┼────────────────────┘
                               │
                               │ OpenAPI 3.1 spec
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
┌───────────────────────────┐  ┌──────────────────────────┐
│       mcp_builder         │  │      resource_setup      │
│  create_openapi_provider  │  │  register_all_resources  │
│                           │  │                          │
│  Phase 0: _get_deprecated │  │  • auto_generated:       │
│  _routes → exclude        │  │    every GET endpoint    │
│  deprecated endpoints     │  │    → raw JSON resource   │
│                           │  │                          │
│  Phase 1: _customize      │  │  • custom wrappers:      │
│  _metadata (per tool):    │  │    Markdown formatters   │
│  • title, category        │  │    for common URIs       │
│  • annotations, hints     │  │    (override auto)       │
│  • output/label schemas   │  │                          │
│  • invalidation patterns  │  │                          │
│                           │  │                          │
│  Phase 2: LabelTransform  │  │                          │
│  (innermost):             │  │                          │
│  • label string→ID conv   │  └───────────┬──────────────┘
│                           │              │
│  Phase 3: _ToolWrapping   │              │
│  _Transform (outermost):  │              │
│  • inject virtual params  │              │
│  • validate args          │              │
│  • error translation      │              │
│  • text result wrapping   │              │
│  • pagination metadata    │              │
│  • apply virtual params   │              │
└─────────────┬─────────────┘              │
              │                            │
              └──────────┬─────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   FastMCP Server                        │
│                                                         │
│  Server transforms (applied in order on list_tools):    │
│    1. TolerantSearchTransform — hides all except        │
│       synthetic tools needed for discovery              │
│    2. GiteaNamespace — prefixes tool names with         │
│       gitea_; resources pass through unchanged          │
│    3. ExtensionMetadataTransform — applies YAML         │
│       overrides (title, description, tags, hints)       │
│       to matching tools; matches both prefixed and      │
│       unprefixed names                                  │
│                                                         │
│  Spec-prep filtering (before FastMCP sees the spec):    │
│    • route_map_fn — drops operations that are           │
│      deprecated, scope-filtered, or config-excluded     │
│      (see Spec-Level Filtering milestone, Phase 2)      │
│                                                         │
│  Middleware:                                            │
│    • ResponseCaching          — TTL for resources       │
│    • CacheInvalidationOnWrite — clear on write tools    │
└─────────────────────────────────────────────────────────┘
```

## Runtime: Tool Call & Resource Read Flows

```
Agent calls a tool:

  call_tool("gitea_create_issue", {...})
    │
    ├─▶ TolerantSearchTransform (synthetic handler)
    │     └─▶ ctx.fastmcp.call_tool(name, args)
    │
    ├─▶ CacheInvalidationMiddleware
    │     ├─▶ executes the tool (auto OTEL span: tools/call gitea_*)
    │     └─▶ on success: invalidate cached resources
    │
    ├─▶ GiteaNamespace            - strip gitea_ prefix
    ├─▶ _ToolWrappingTransform    — validate (OTEL: .validate span)
    │                              → log context (ctx.info)
    │                              → report progress (ctx.report_progress)
    │                              → call inner tool's run()
    │     └─▶ LabelTransform      — convert labels (.validate_labels span)
    │                              → log context (ctx.info)
    │                              → call original tool's run()
    │           └─▶ OpenAPITool.run() — httpx → Gitea API
    │                                    → {"result": data}
    │                              → wrap result (pagination, text)
    │                              → report progress (ctx.report_progress)

Agent reads a resource:

  read_resource("gitea://repos/owner/repo")
    │
    ├─▶ ResponseCachingMiddleware  — return cached if fresh
    └─▶ Resource handler           — auto or custom
         └─▶ format as Markdown → return content
```

---

## Module Map

### Core Pipeline

| Module | Role | Public API |
|--------|------|------------|
| `config.py` | Pydantic settings from env vars (GITEA_URL, GITEA_TOKEN, etc.) | `Config` |
| `client.py` | httpx client with retry, rate-limit handling, SSL | `GiteaClient` |
| `openapi_converter/` | Swagger 2.0 → OpenAPI 3.1 (split into `core.py` for conversion pipeline, `schema.py` for schema walker/transformers) | `convert_swagger_to_openapi_v3` |
| `openapi_types.py` | TypedDict types for the OpenAPI spec navigation spine (`OpenAPISpec`, `SwaggerV2Spec`, `OpenAPIOperation`, etc.) | 7 TypedDict types |
| `spec_loader.py` | Fetch spec, convert, apply parameter extensions; load YAML overrides for transform | `load_and_convert_spec` |
| `mcp_builder.py` | Create `OpenAPIProvider` from spec + client; exclude deprecated endpoints via `route_map_fn` | `create_openapi_provider`, `_get_deprecated_routes` |
| `server.py` | Assemble everything, serve via stdio or HTTP | `main()`, `create_mcp_server()` |
| `constants.py` | Centralized magic numbers, cache TTLs, pattern names, scopes | (constants) |
| `logging_config.py` | JSON/text formatter, sensitive-key redaction, log setup | `setup_logging` |
| `exceptions.py` | Exception hierarchy (`GiteaMCPError` → 5 subclasses) | `GiteaAPIError`, `ValidationError`, etc. |
| `format.py` | General-purpose schema-aware markdown formatters (shared by tools & resources) | `_format_as_markdown`, `_format_datetime`, `_format_simple_value` |
| `unified_search.py` | Unified search across tools, workflow docs, and MCP resources (merged BM25 + `type` discriminator) | `register_unified_search` |

### Tool Customization Stack (applied in order)

All tool-related runtime concerns live in `gitea_mcp_server/tools/`:

| Module | What it contains |
|--------|------------------|
| `tools/customize.py` | title/category generation, hint inference, annotation prep, invalidation helpers |
| `tools/schemas.py` | `derive_output_schema`, `$ref` resolution, text/JSON response detection |
| `tools/errors.py` | error translation, runtime validation runner, `_run_with_error_handling` |
| `tools/labels.py` | string→ID label conversion, label schema updates (schema-time only) |
| `tools/label_transform.py` | FastMCP `Transform` — runtime label validation and conversion, runs as innermost provider-level transform | `LabelTransform`, `_convert_labels_inline` |
| `tools/examples.py` | schema→example generation, tool schema serialization |
| `tools/extensions_metadata.py` | `ExtensionMetadataTransform` — applies YAML metadata overrides (title, description, tags, annotation hints) to all tools at query time |
| `tools/exclusion.py` | `load_exclusion_config` + `matches_any`/`matches_pattern` — exclusion config loading and pattern matching, consumed by spec-level `route_map_fn` filtering |
| `tools/search.py` | BM25 search engine + `TolerantSearchTransform`, synthetic `search_tools`/`call_tool`/`tool_info` tools |
| `tools/type_info.py` | ``resolve_type`` tool + ``gitea://types/{typeName}`` resource — resolve ``$ref:Type`` names to schema and cross-references |
| `tools/virtual_params.py` | Virtual parameter registry + lifecycle (``inject_into``, ``extract_from``, ``apply_pre_hooks``, ``apply_to``) — generic mechanism for agent-facing params that are stripped before the HTTP call. Registered entries: ``sudo`` (user impersonation via ``?sudo=``, scope-gated by token permissions). The ``format`` param is promoted to a first-class concept handled directly in ``_ToolWrappingTransform._wrap()``. |
| `tools/namespace.py` | `GiteaNamespace` transform (prefixes tools, passes resources through) |

The customization layers as applied during server startup:

| Layer | Module | What it does |
|-------|--------|--------------|
| 0. Deprecated filter | `server_setup/mcp_builder.py` | exclude endpoints with `deprecated: true` via FastMCP `route_map_fn` before component creation |
| 1. Annotations | `tools/customize.py` | title, category tag, readOnly/destructive/idempotent hints |
| 2. Error handling | `tools/errors.py` | wraps `run()` to translate HTTP errors to agent-friendly messages |
| 3. Label schema | `tools/labels.py` | `update_labels_schema()` — augment label parameter description at schema time |
| 4. Validation | `validation.py` | runtime validation (owner/repo format, pagination, etc.) + schema augmentation |
| 5. Cache invalidation | `cache_invalidation.py` | on write, invalidate affected resource cache entries |
| 6. Permissions | `spec_loader.py` (route_map_fn) | drop operations that exceed token scopes or match exclusion config, before provider creation |
| 8. Search/lazy loading | `tools/search.py` | BM25 search with alias expansion, synthetic tools |
| 9. Namespace | `tools/namespace.py` | prefix all tools with `gitea_` (resources pass through unchanged) |
| 10. Extension metadata | `tools/extensions_metadata.py` | apply YAML overrides (title, description, tags, hints) to matching tools — runs after namespace so it matches both `gitea_` and unprefixed names |
| 11. Unified search | `unified_search.py` | merged BM25 search across tools, docs, and resources with `type` discriminator |
| 12. Response caching | `cache_invalidation.py` middleware | TTL-based caching of resource reads |
| 13. Label runtime | `tools/label_transform.py` | `LabelTransform` — innermost provider-level transform, converts label strings to IDs before HTTP call (registered via `provider.add_transform()`) |

### Resource System

| Module | Role |
|--------|------|
| `resources/auto.py` | Auto-generated resources from OpenAPI GET endpoints (raw JSON) |
| `resources/custom.py` | Hand-written Markdown wrapper resources for common URIs |
| `resources/format.py` | Domain-specific resource Markdown formatters (repo, issues, pulls, users, releases, labels) |
| `resources/scope.py` | Scope derivation (`derive_required_scope`) for tools and resources; see `docs/SCOPE_MODEL.md` |
| `mcp_tools.py` | `mcp_list_resources`, `mcp_read_resource`, tool schema resource |

### Server Setup Orchestration (startup-only)

| Module | Role |
|--------|------|
| `server_setup/__init__.py` | Package marker |
| `server_setup/spec_loader.py` | Fetch, convert, extend |
| `server_setup/mcp_builder.py` | Create provider + wire tools (imports from `tools/` and `label_service`) |
| `server_setup/resource_setup.py` | Orchestrate resource registration |
| `server_setup/permissions.py` | Re-exports scope-filtering helpers (avoids circular import) |
| `server_setup/mcp_extensions.py` | YAML-based parameter extensions (applied to spec before tool generation) |

### Flat Infrastructure Modules (shared, not domain-specific)

| Module | Role |
|--------|------|
| `models.py` | TypedDict models for structured output types (`ToolSearchEntry`, `ResourceEntry`, `ResourceListing`, `DocEntry`, `UnifiedSearchItem`, `ToolSchemaResult`, `SimpleStringResult`) — zero runtime overhead, pure annotation types |
| `scope.py` | Scope derivation (`derive_required_scope`) for tools and resources; flat module breaks circular import between `tools/` and `resources/`; see `docs/SCOPE_MODEL.md` |
| `search.py` | BM25 search engine infrastructure (`BM25SearchEngine`) — generic text indexing and ranking, used by `tools/search.py` |
| `pagination.py` | Pagination metadata injection: `capture_pagination_headers()` httpx event hook, `add_pagination_metadata()` shared helper for adding `has_more`/`next_offset`/`total_count` to structured content, used by both API tools (`_ToolWrappingTransform`) and synthetic tools (search, list, docs) |

---

## Key Design Decisions

1. **FastMCP providers, not manual tool registration** -- The OpenAPI provider
   auto-generates tools from the spec. Customization happens via
   `_ToolWrappingTransform` and the `transform_fn` pattern, not by
   hand-registering each tool.

2. **Lazy loading** -- Tools are not listed by default. Agents discover them via
   `search_tools` (BM25). This prevents context pollution from ~200 tools being
   listed at once.  All tools tagged `synthetic` are always pinned in
   `list_tools()` so agents can call them without searching.

3. **Resources pass through namespace** -- Resources use the `gitea://` scheme
   directly.  FastMCP's built-in `Namespace` would double-namespace them to
   `gitea://gitea/...`, so `GiteaNamespace` explicitly passes resource URIs
   through unchanged.

4. **Custom resources override auto-generated** -- Resources are registered in
   two phases: auto-generated (raw JSON from every GET endpoint) then custom
   (Markdown wrappers for common URIs).  FastMCP's last-registration-wins means
   custom ones replace raw ones at identical URIs.

5. **Response schema wrapping** -- FastMCP requires `output_schema` to be
   `type: object`.  All response schemas are wrapped in `{"result": ...}` to
   match the runtime shape.  This is done in `openapi_converter.py` via
   `_wrap_success_response_schemas`.

6. **Cache invalidation via middleware** -- Write tools register invalidation
   patterns at startup.  The `CacheInvalidationMiddleware` computes concrete
   URIs from tool arguments and clears them from the response cache after
   successful writes.

 7. **Circular-import breaker pattern** -- `server_setup/permissions.py` is a thin
    re-export of scope-filtering helpers, avoiding a circular import that would
    occur if `server.py` imported those helpers directly.  Same pattern:
    `resources/scope.py` re-exports from flat `scope.py`.

  8. **OpenTelemetry instrumentation** -- FastMCP 3.x includes native OTEL
     instrumentation that auto-generates spans for all MCP operations (tool
     calls, resource reads, prompt renders) with zero configuration. We add
     three custom child spans (``{tool}.validate``, ``{tool}.validate_labels``,
     ``{tool}.execute``) for per-stage latency visibility. The *why*: spans are
     no-ops unless an OpenTelemetry SDK + exporter are configured, so the
     instrumentation is free at runtime when unset. The operational how-to
     (viewer, exporters, env vars) lives in `docs/DEVELOPMENT.md` →
     "OpenTelemetry Observability".

 9. **Constants consolidation** -- `TAG_TO_SCOPE`, `TOOL_INVALIDATION_PATTERNS`,
    and BM25 search configuration (`SEARCH_*`) were moved from scattered module-level
    definitions into `constants.py`, the single source of truth for all magic values.

10. **OpenAPI spec TypedDict migration** -- All pipeline layers accept typed
    OpenAPI spec parameters instead of `dict[str, Any]`.  Seven TypedDict types
    (`OpenAPISpec`, `SwaggerV2Spec`, `OpenAPIOperation`, `OpenAPIPathItem`,
    `OpenAPIParameter`, `OpenAPIResponse`, `OpenAPIInfo`) define the navigation
    spine of the spec, with deep/recursive parts (``$ref`` chains, nested schemas)
    intentionally kept as ``dict[str, Any]`` since their keys are dynamic.
    ``total=False`` matches existing ``.get()`` guard patterns, requiring no
    logic changes.  ``cast()`` at FastMCP boundaries avoids coupling the type
    system to FastMCP internals.  Two spec shapes are tracked: ``SwaggerV2Spec``
    (pre-conversion input) and ``OpenAPISpec`` (post-conversion output, used by
    tools, server_setup, and resources).  State-mutating converter functions
    (``SpecVersionUpdater``, ``BasePathToServerConverter``) stay ``dict[str, Any]``
    since TypedDict cannot express in-place shape transitions.

11. **Auto-generated tool descriptions over hand-crafting** --
    The server generates ~200 tools from the OpenAPI spec at startup.
    Hand-crafting descriptions for all of them would be impractical and
    brittle (they'd drift from the spec).  Instead:

    - **Primary**: tool descriptions come from the OpenAPI `summary` field of
      each endpoint.  This keeps them in sync with the Gitea API spec.
    - **Overrides**: `mcp_extensions.yaml` allows manual description
      replacements on a per-tool basis for cases where the `summary` is unhelpful.
    - **Supplemental**: rich server instructions (`agent_instructions.md` +
      workflow guide manifest) give agents higher-level context about how to
      discover and compose tools, reducing reliance on per-tool descriptions.
    - **Inline guidance**: tools with a `labels` parameter get a description
      appendix explaining how to discover valid label values.

    In practice, agents discover and use tools correctly through the search
    mechanisms (`search_tools` / `search` / `tool_info`) without needing
    bespoke descriptions for every endpoint.

12. **Server naming: two independent prefix sources** --
    Tool names in this server are the concatenation of **two independent
    prefixes** applied by different layers:

    - **Server-level tool prefix**: The `GiteaNamespace` transform (see
      :ref:`module-map`) prepends the string ``gitea_`` to every tool name.
      This prefix is configurable but is treated as fixed throughout the
      codebase — all docs, server instructions, and user-facing strings
      reference tools by their ``gitea_*`` names.

    - **Host-level MCP server name**: The MCP client or host (e.g., agent
      framework, gateway) assigns an identifier to this server in its own
      configuration.  The host prepends this identifier to every tool name
      at the protocol level.  In this deployment that identifier happens to be
      ``gitea_mcp``, producing full protocol names like
      ``gitea_mcp_gitea_create_issue``.  Another deployment might use a
      different identifier (e.g., ``forgejo`` → ``forgejo_gitea_create_issue``).

    The two prefixes serve different purposes: ``gitea_`` is a namespace the
    server owns and uses internally to avoid collisions with tools from other
    MCP servers.  The host-level identifier is a deployment concern — it lets
    the agent environment route calls to the correct server and is outside the
    server's control.  Documentation and code consistently use the
    server-level ``gitea_`` form, which is the stable interface regardless of
    how the host names the server.

13. **Lifespan lifecycle and Context injection** — The server uses FastMCP's
    built-in ``lifespan`` mechanism for proper resource lifecycle management.

    - **Lifespan**: ``create_mcp_server()`` accepts an optional ``lifespan``
      callback.  ``main_async()`` defines a closure ``app_lifespan`` that yields
      ``{"gitea_client": client}`` on startup and closes the client on teardown.
      This replaced the manual ``gitea_client.close()`` in the old ``finally``
      block — lifespan handles successful shutdown; the error path preserves its
      own close in the ``except`` block since lifespan is never entered on init
      failure.

    - **Context injection**: ``_ToolWrappingTransform._run_transform_pipeline()``
      uses ``CurrentContext()`` (an async context manager) to obtain the MCP
      ``Context`` object inside the request scope.  The core pipeline was
      extracted to ``_pipeline_with_context(ctx)``, letting ``_run_transform_pipeline``
      handle the ``CurrentContext()`` boilerplate and gracefully degrade when
      called outside a request (e.g., unit tests — ``CurrentContext()`` raises
      ``RuntimeError``, caught and passed as ``ctx=None``).

    - **Agent observability**: ``ctx.info()`` calls log validation results, label
      processing, and execution completion with structured ``extra`` dicts.
      ``ctx.report_progress()`` signals progress at execution start (50%),
      paginated fetches (100%), and completion (100%).  This gives agent hosts
      visibility into long-running operations without relying solely on OTEL
      spans or stdout.

 14. **Vendor extension (``x-*``) stripping in the converter** -- Gitea's
     Swagger 2.0 spec leaks Go struct internals as vendor extensions:
     ``x-go-name`` on every schema property and ``x-go-package`` on schema
     definitions.  These carry no meaning for LLM agents and only waste
     context tokens in tool parameter schemas, so ``convert_schema()`` and
     ``convert_parameters()`` strip all ``x-*`` keys during conversion
     (alongside the existing ``readOnly``/``xml`` cleanup).

     The strip is **surgical**: only schema-level ``x-*`` fields are removed.
     Operation-level ``x-*`` fields are intentionally preserved because they
     carry semantic meaning used elsewhere in the pipeline:
     ``x-original-content-types`` (set by ``OperationTransformer`` and read by
     ``tools/schemas.py:_is_text_response`` to distinguish non-JSON endpoints)
     and ``x-mcp`` (consumed by ``server_setup/mcp_extensions.py``).  The
     post-conversion ``x-fastmcp-wrap-result`` extension is injected on output
     schemas in ``mcp_builder.py`` and is likewise unaffected.  Do not broaden
     the strip to the whole spec -- that would silently break text/plain
     response detection and MCP extension overrides.

---



## Response Content-Type Handling

Gitea's API mixes content types: most endpoints return JSON, but some return
plain text (diffs, patches), HTML (signing keys), or binary blobs (file
downloads).  Handling this correctly requires coordination across four stages.

### Stage 1 -- Spec Conversion (`openapi_converter/core.py`)

Swagger 2.0 specifies response types via the `produces` field (per-operation or
top-level).  `convert_responses()` uses `produces` to set the OpenAPI 3.1
`content` type on each response -- but only if `produces` is propagated to the
operation before `remove_swagger_fields()` strips it.

If no `produces` is found, the converter defaults to `application/json`. This is
correct for ~95% of endpoints, but silently wrong for the ~12 non-JSON
endpoints if `produces` propagation is missed.

### Stage 2 -- Schema Wrapping (`openapi_converter/core.py:_wrap_success_response_schemas`)

All `application/json` response schemas are wrapped in:
```
{"type": "object", "properties": {"result": <original_schema>}}
```

This matches the runtime shape FastMCP produces (see Stage 4) and satisfies the
MCP SDK's requirement that `output_schema` be `type: object`.

Non-JSON responses (text/plain, text/html, application/octet-stream) are
**implicitly skipped** -- `_wrap_response_schema` only looks at
`content["application/json"]`, so if that key is absent the function returns
without wrapping.  No special check is needed.

### Stage 3 -- Output Schema Derivation (`tools/schemas.py:derive_output_schema`)

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

### Stage 4 -- Runtime Execution (`server_setup/mcp_builder.py:_ToolWrappingTransform`)

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
  `_ToolWrappingTransform._wrap()` method wraps the text in
  `{"result": raw_text}` for client consistency with JSON endpoints.

### The `x-fastmcp-wrap-result` Extension

FastMCP's `OpenAPIProvider` checks the spec for an `x-fastmcp-wrap-result`
extension on each operation.  When present (set during
`_wrap_success_response_schemas`), FastMCP wraps the raw API response in
structured content matching the output schema.  This is how `{"result": data}`
is produced at runtime for JSON endpoints.

For non-JSON endpoints, this extension is absent (no wrapping was applied in
Stage 2), so `output_schema = None` is paired with the
`_ToolWrappingTransform` fallback to produce the same `{"result": text}` shape.

### Empty-Body Responses (202, 204, 205)

Some Gitea endpoints return success with no response body (204 No Content,
205 Reset Content, or 202 Accepted without a body).  Like non-JSON endpoints,
`_get_success_schema` finds nothing and returns `None` — but for a different
reason: no `content` entry exists on the success response at all.

The fix follows the same two-phase pattern as the text/plain handling:

**Schema time** (`server_setup/mcp_builder.py:_customize_metadata`):
`_response_has_no_content()` in `tools/schemas.py` checks the spec for a
2xx response without a `content` key (only 202/204/205 are checked — 200/201
always carry content in a well-formed spec).  When detected, a lightweight
schema is set:
```python
{"type": "object", "properties": {"result": {"type": "null"}}}
```
This triggers `x-fastmcp-wrap-result: true` and tells the MCP SDK to expect
structured output.

**Runtime** (`_pipeline_with_context`): when `is_empty_response` is true and
`structured_content` is still `None` (FastMCP had no JSON to unwrap), the
wrapping handler returns `ToolResult(content=[""], structured_content={"result": None})`.

The `_serialize_tool_schema` function guards against the `type: null` schema
producing a `None` output_example — that field is simply omitted when the
example would be null, since agents can infer the shape from `output_schema`.

---

## Data Flow: Agent Calls the Unified Search

```
Agent: search("create issue")
  │
  └─▶ Unified search tool (closure in server.py)
        ├─▶ fetch tools: TolerantSearchTransform.get_tool_catalog(ctx)
        ├─▶ fetch resources: _mcp_list_resources_impl(ctx)
        ├─▶ fetch docs: doc_manager.search(query)
        ├─▶ merge → BM25 rank across all three corpora
        └─▶ return results with type discriminator
```

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
  └─▶ _ToolWrappingTransform (outermost)
        ├─▶ inject virtual params into schema (tools/virtual_params.py)
        ├─▶ extract virtual params from kwargs → stash
        ├─▶ validate arguments (validation.py)
        ├─▶ log validation result (ctx.info)
        ├─▶ report execution progress (ctx.report_progress)
        ├─▶ call inner tool's run()
        │  └─▶ LabelTransform (innermost)
        │        ├─▶ convert label strings→IDs (label_service)
        │        ├─▶ log label result (ctx.info)
        │        └─▶ call original tool's run()
        │           └─▶ OpenAPITool.run() → httpx request to Gitea API
        ├─▶ log completion (ctx.info)
        ├─▶ wrap response in {"result": ...}
        ├─▶ report progress for paginated fetches (ctx.report_progress)
        ├─▶ on error: translate httpx errors to agent-friendly messages
        └─▶ apply virtual param post-hooks to result (tools/virtual_params.py)
```

---

## Agent-Facing Documentation

The file `gitea_mcp_server/docs/agent_instructions.md` is loaded as FastMCP
server instructions and served as context to agents at connection time.  It
explains how to discover and use tools/resources from the agent's perspective.
