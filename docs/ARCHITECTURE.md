# Gitea MCP Server — Architecture

## Overview

This server provides ~200 tools and resources for LLM agents to interact with
Gitea/Forgejo.  Tools and resources are **auto-generated** from the Gitea
Swagger/OpenAPI spec, then **customized** with annotations, validation, label
handling, and cache control.

The codebase is designed to work *with* FastMCP, not around it.  When FastMCP's
API lacks something, we add a conversion/transform layer that can be cleanly
removed when FastMCP catches up.

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
| `openapi_converter.py` | Swagger 2.0 → OpenAPI 3.1 (949 lines) | `convert_swagger_to_openapi_v3` |
| `spec_loader.py` | Fetch spec, convert, apply extensions | `load_and_convert_spec` |
| `mcp_builder.py` | Create `OpenAPIProvider` from spec + client | `create_openapi_provider` |
| `server.py` | Assemble everything, serve via stdio or HTTP | `main()`, `create_mcp_server()` |

### Tool Customization Stack (applied in order)

| Layer | Module | What it does |
|-------|--------|--------------|
| 1. Annotations | `tool_annotator.py` | title, category tag, readOnly/destructive/idempotent hints |
| 2. Error handling | `tool_annotator.py` | wraps `run()` to translate HTTP errors to agent-friendly messages |
| 3. Label support | `tool_annotator.py` | string-to-ID label conversion, schema updates |
| 4. Validation | `validation.py` | runtime validation (owner/repo format, pagination, etc.) + schema augmentation |
| 5. Cache invalidation | `cache_invalidation.py` | on write, invalidate affected resource cache entries |
| 6. Permissions | `tool_filter.py` | hide tools/resources that exceed token scopes |
| 7. Search/lazy loading | `bm25_search.py` + `tool_annotator.py` | BM25 search with alias expansion, `search_tools`/`tool_info`/`call_tool` synthetic tools |
| 8. Namespace | `namespace.py` | prefix all tools with `gitea_` (resources pass through unchanged) |
| 9. Response caching | `cache_invalidation.py` middleware | TTL-based caching of resource reads |

### Resource System

| Module | Role |
|--------|------|
| `resources.py` | Two registration phases: auto-generated (raw JSON from GET endpoints) then custom (Markdown wrappers for common URIs) |
| `resource_registry.py` | Passive catalog recording what's been registered |
| `mcp_tools.py` | `mcp_list_resources`, `mcp_read_resource`, tool schema resource |

### Server Setup Orchestration

| Module | Role |
|--------|------|
| `server_setup/__init__.py` | Package marker |
| `server_setup/spec_loader.py` | Fetch, convert, extend |
| `server_setup/mcp_builder.py` | Create provider + customize tools |
| `server_setup/tool_annotator.py` | Full tool customization pipeline |
| `server_setup/resource_registry.py` | Orchestrate resource registration |
| `server_setup/namespace.py` | Tool-only prefix transform |
| `server_setup/permissions.py` | Re-exports from tool_filter.py (avoids circular import) |
| `server_setup/mcp_extensions.py` | YAML-based tool customizations (titles, descriptions, params) |
| `server_setup/bm25_search.py` | BM25 search logic |
| `server_setup/label_manager.py` | Cached label name→ID mapping |
| `server_setup/logging.py` | Re-exports from logging_config.py |

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
