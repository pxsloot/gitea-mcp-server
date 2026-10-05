---
audience: developer
type: explanation
covers: Pipeline (Swagger 2.0 -> FastMCP), module map, contracts & invariants, content-type handling, runtime flows
---

# Gitea MCP Server -- Architecture

## Overview

This server provides ~400 tools and resources for LLM agents to interact with
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

## What this doc is NOT

This doc explains the server's architecture and the contracts that constrain
it. If you need:

| Topic | See |
|-------|-----|
| Environment setup, adding features, MCP extensions, exclusion config | `docs/DEVELOPMENT.md` |
| Testing conventions, zones, fixtures, mocking patterns | `docs/TESTING_STANDARDS.md` |
| How token scopes gate tool/resource visibility and the scope derivation mechanism | `docs/SCOPE_MODEL.md` |
| Full semantics of annotation fields (title, tags, hints) | `docs/TOOL_ANNOTATIONS.md` |
| Documentation set structural rules | `docs/DOCUMENTATION_STANDARDS.md` |

---

## Pipeline: Swagger 2.0 → FastMCP Server

| Stage | Owner | What happens |
|-------|-------|--------------|
| Fetch + convert | `server_setup/spec_loader.py`, `openapi_converter/` | Fetch Gitea's Swagger 2.0, convert to OpenAPI 3.1, wrap response schemas, apply parameter extensions, normalize agent-misleading spec quirks |
| Spec-prep filtering | `spec_loader.py` + `mcp_builder.py` | Compute the excluded set (deprecated + scope + config-excluded); `route_map_fn` drops those operations before FastMCP builds tools (see Spec-Level Filtering) |
| Tool build | `server_setup/mcp_builder.py` | `create_openapi_provider` generates one tool per operation; `_customize_metadata` assigns identity, category, hints, description, schema, and the registration record |
| Resource build | `server_setup/resource_setup.py` | Register custom resources first, then auto-generated GET resources; custom wins (see Resource registration and derivation) |
| Server assembly | `server.py` | Mount resources; register the server-level contract transform, namespace, extension-metadata and search transforms, middleware, and the lifespan that opens/closes the httpx client |

**Transform chain** (applied on `list_tools`, in order): the server-level
contract transform `_ToolWrappingTransform` (registered first, wraps every tool
whose registration record has `wrap=True`) → `TolerantSearchTransform` (hides
everything except the synthetic discovery tools) → `GiteaNamespace` (prefixes
tool names `gitea_`; resources pass through) → `ExtensionMetadataTransform`
(YAML overrides).  Spec-prep filtering happens before FastMCP sees the spec.

### Spec-Level Filtering

All filtering (scope, deprecation, config exclusion) is decided once at
spec-prep time, before FastMCP ever sees tools or resources.  The same
``filtered_tools_info`` data structure drives both registration decisions
and agent-facing error messages.

```
Server startup
  │
  ├─ 1. load_and_convert_spec(...)
  │      → openapi_spec (converted)
  │      → filtered_tools_info (scope + deprecation + config exclusion)
  │      → excluded_routes (tools to drop via route_map_fn)
  │      → available_scopes (for custom resources + virtual params)
  │
  ├─ 2. create_openapi_provider(..., excluded_routes=...)
  │      → route_map_fn drops filtered tool operations
  │
  ├─ 3. register_all_resources(..., filtered_tools_info=...,
  │      │                       available_scopes=...)
  │      ├─ auto resources: skip if operationId in filtered_tools_info
  │      │   (covers scope + deprecation + config exclusion)
  │      └─ custom resources: skip if has_sufficient_scope() fails
  │          (scope-only — hand-written resources)
  │
  └─ 4. apply_scope_filter(available_scopes)
         → gates virtual params (e.g. sudo)
```

The filtering invariants — ``filtered_tools_info`` as the single source of
truth for auto-generated resource visibility, and custom resources gated by
``available_scopes`` — are stated once in Contracts & Invariants.  The one
mechanism fact local to this section: ``load_exclusion_config`` lives in
``spec_loader.py`` alongside its only consumer (``load_and_convert_spec``),
while ``tools/exclusion.py`` retains only the pattern-matching helpers
(``matches_any``, ``matches_pattern``) used by ``filter_info.py``.

### Runtime: Tool Call & Resource Read Flows

```
Agent calls a tool (via call_tool proxy or direct MCP call):

  call_tool("gitea_issue_create_issue", {...})
    │
    ├─▶ FilteredToolMiddleware    — check tool name against filter
    │     │                         predictions (scope/excluded/
    │     │                         deprecated).  For proxy calls
    │     │                         the middleware checks the proxy
    │     │                         name and passes through; the
    │     │                         inner check happens in
    │     │                         _call_tool_impl below.
    │     └─▶ if filtered: raise ToolError with helpful message
    │
    ├─▶ TolerantSearchTransform (synthetic handler)
    │     └─▶ ctx.fastmcp.call_tool(name, args)
    │
    ├─▶ ResponseCacheMiddleware  — pass-through for tools (resource
    │     reads are cached; see the resource flow below)
    │
    ├─▶ CacheInvalidationMiddleware
    │     ├─▶ executes the tool (auto OTEL span: tools/call gitea_*)
    │     └─▶ on success: invalidate cached resources
    │
    ├─▶ GiteaNamespace            - strip gitea_ prefix
    ├─▶ _ToolWrappingTransform    — server-level contract transform
    │     │                         (spine: tools/contract.build_transform_fn)
    │     │                         extract + validate virtual params
    │     │                         (registry schema enum, before executor)
    │     │                         validate real args (OTEL: .validate span)
    │     │                         → log context (ctx.info)
    │     │                         → report progress (ctx.report_progress)
    │     │                         → call inner tool's run()
    │     └─▶ LabelTransform      — convert labels (.validate_labels span)
    │                              → log context (ctx.info)
    │                              → call original tool's run()
    │           └─▶ OpenAPITool.run() — httpx → Gitea API
    │                                    → raw data (ExecutionResult)
    │                              → render (tools/result_pipeline.render):
    │                                shape → paginate → format → ToolResult
    │                              → report progress (ctx.report_progress)

Agent reads a resource:

  read_resource("gitea://repos/owner/repo", format="markdown")
    │
    ├─▶ ResponseCacheMiddleware  — return cached if fresh (TTL)
    │
    ├─▶ _mcp_read_resource_impl(ctx, uri)
    │     └─▶ ctx.read_resource(uri) → ResourceResult
    │           └─▶ Resource handler  — auto or custom
    │                 returns raw data + metadata (schema, format_hint, extra)
    │           ← (raw, schema, format_hint, extra)
    │
    ├─▶ read_resource executor (mcp_tools.py:_read_resource_tool)
    │     ├─ decode base64 (always, like autogen text responses)
    │     ├─ parse JSON; classify shape (list/object/scalar/text)
    │     │   (an array resource is shape="list", paginated=True,
    │     │    total_count=len — the pipeline slices it)
    │     └─ return ExecutionResult(data, total_count, shape, paginated,
    │            schema, markdown_formatter, extra)
    │            (markdown_formatter = get_formatter(format_hint); extra from
    │            content meta — display input, not display logic)
    │
    └─▶ Single result pipeline (tools/result_pipeline.py:render)
          shape → paginate → format → dual-channel ToolResult
          ├─ format/json: collapse_data when detail=concise + schema
          │   (root-list items summarized via one-level $ref resolution)
          ├─ format/markdown: pre-collapse + formatter (resolved by
          │   _resolve_formatter — the result's own MarkdownFormatter, else
          │   the type-bound domain formatter for the result's response_type,
          │   else the schema-bound format_as_markdown fallback — dispatched
          │   via call_markdown_formatter, which passes only the kwargs the
          │   formatter declares; collapsed fields are {"$ref": "TypeName"}
          │   markers)
          └─ format/raw: serialized envelope {"result": <data>}
```

### The execution contract

Autogenerated and synthetic tools expose the same agent-facing contract
through one spine: ``tools/contract.build_transform_fn(tool, executor,
default_format=...)``.  The spine extracts virtual params, runs pre-hooks,
resolves the MCP ``Context`` via ``context_utils.resolve_current_context()``
(which returns ``None`` outside an active session, so context calls degrade
safely), invokes the executor, and renders through the single result pipeline.
The only difference between the tool families is the executor — the autogen
HTTP pipeline (``server_setup/mcp_builder.py``) or a synthetic in-memory
implementation.

**Executors return raw data only** — an ``ExecutionResult``.  The result
pipeline (``tools/result_pipeline.render``) then applies shape → paginate →
format and is the **single writer of both channels**; the full output contract
is canonical in that module's docstring.  See Contracts & Invariants for the
rules that follow ("One result pipeline", "Registration metadata").

Pagination bounds are owned by the paging executor and validated exactly once
per call; params a call neutralizes (``fetch_all`` → ``page``/``limit``) are
skipped.  Every tool family names the page-size parameter ``limit`` and declares
its cap as a JSON Schema ``maximum`` so agents discover it via ``tool_info``.

### Resource registration and derivation

Resources are registered in two phases: custom (Markdown wrappers for common
URIs) then auto-generated (raw JSON for every GET endpoint); the orchestrator
passes the custom URI set to the auto pass so a wrapper always overrides its
auto sibling.

``make_api_resource()`` **derives**:
- the URI template from the spec path, plus the ``x-wildcard-path-param``
  extension (rendered ``{param*}``) and the ``{?a,b}`` query suffix from
  ``param_config``;
- the name from the spec ``operationId`` (camelCase → snake_case), so wrapper
  names align with their auto siblings;
- the description from the operation summary (passed as ``description=`` to
  ``mcp.resource()`` — FastMCP prefers it over the handler docstring, so
  docstrings stay code documentation only).

``auto.py`` uses the same derivation for its skip-check URI, so the override is
structurally guaranteed rather than test-locked.  Only resources whose URI is
not a spec mirror (e.g. ``gitea://repos/{owner}/{repo}/readme``) declare an
explicit ``uri``/``name``.

---

## Module Map

> Each module's docstring is the canonical source for its role, public API,
> and design invariants.  The tables below give a one-line orientation;
> for details, read the module docstring.

### Core Pipeline

| Module | One-line role |
|--------|---------------|
| `config.py` | Pydantic settings from env vars + ``ConfigProtocol`` structural protocol |
| `client.py` | httpx client with retry, rate-limit handling, SSL |
| `openapi_converter/` | Swagger 2.0 → OpenAPI 3.1 conversion orchestration (``core.py``); schema walker/normalizer (``schema.py``); param collision resolution (``param_collision.py``); spec normalization (``normalize.py``); type-reference analysis (``type_references.py`` — stamps ``x-resource-types`` / ``x-modifies-type`` pre-wrap); display-view hints (``display_hints.py`` — curated ``omit`` / ``compact`` / ``flag`` tables, validated against the component schemas and resolved per entity at registration via ``view_hints_for``) |
| `openapi_types.py` | TypedDict types for the OpenAPI spec navigation spine |
| `registration.py` | Typed registration records (`ToolRegistration`, `ResourceRegistration`), the sanctioned accessors, and the resource content-meta helpers — the single contract for registration metadata |
| `server.py` | Assembly, main(), create_mcp_server(), lifespan, middleware wiring, server-level contract transform registration |
| `constants.py` | Centralized magic numbers, cache TTLs, scopes |
| `logging_config.py` | JSON/text formatter, sensitive-key redaction, log setup |
| `exceptions.py` | Exception hierarchy (``GiteaMCPError`` → 5 subclasses) |
| `format.py` | Schema-aware formatting shared by tools & resources: `MarkdownFormatter` (the canonical formatter contract), `collapse_data` (the single collapse authority), `call_markdown_formatter` (signature-aware dispatch), the formatter registry + `resolve_formatter` (the three-tier dispatch policy), and the generic schema-anchored collection view (``_generic_collection_view``). Domain formatters register here; the result pipeline imports only this module |
| `label_service.py` | Label cache and validation — name/ID mapping and conversion behind the label runtime |
| `tools/unified_search.py` | Unified search across tools, docs, and resources |

### Tool Customization Stack

All tool-related runtime concerns live in `gitea_mcp_server/tools/`:

| Module | One-line role |
|--------|---------------|
| `tools/contract.py` | Generic agent-facing contract spine — ``build_transform_fn(tool, executor, default_format=...)``, shared by autogen and synthetic tools |
| `tools/result_pipeline.py` | Single result pipeline — ``ExecutionResult`` (raw executor output) + ``render()`` (shape → paginate → format → ToolResult); the single writer of both channels.  Canonical home of the output contract |
| `tools/customize.py` | title/category, hint inference, annotation prep |
| `tools/schemas.py` | ``derive_output_schema``, ``$ref`` resolution, response classification |
| `tools/errors.py` | runtime validation runner, HTTP error translation |
| `tools/labels.py` | label string→ID conversion, label schema updates (schema-time) |
| `tools/label_transform.py` | FastMCP ``Transform`` — runtime label validation (innermost) |
| `tools/examples.py` | schema→example generation, tool schema serialization |
| `tools/extensions_metadata.py` | ``ExtensionMetadataTransform`` — YAML metadata overrides at query time |
| `tools/exclusion.py` | pattern matching helpers for config-based exclusion |
| `tools/filter_info.py` | filter prediction data + ``FilteredToolMiddleware`` |
| `tools/search.py` | BM25 search + ``TolerantSearchTransform``, synthetic discovery tools (``search_tools``, ``search_resources``, ``list_hidden_tools``); shared ``search_or_list_all`` envelope — empty query lists the full catalog |
| `tools/synthetic_contract.py` | Synthetic registration contract — `SyntheticToolSpec` declarative specs + `register_all_synthetic_tools`, registration record + executor registry, virtual-param allowlists, pagination envelope, page/limit bounds |
| `tools/type_info.py` | ``resolve_type`` tool + ``gitea://types/{typeName}`` resource |
| `tools/docs_tools.py` | ``search_docs`` / ``read_doc`` tools + guide resources |
| `tools/virtual_params.py` | virtual parameter registry + lifecycle (inject/extract/validate/apply) |
| `tools/namespace.py` | ``GiteaNamespace`` transform (prefixes tool names with the server's ``gitea_``; resources pass through).  A host/client may prepend its own server identifier at the protocol level, so a tool can read ``gitea_mcp_gitea_*`` |

The startup order in which these layers are wired (scope filter → exclusion →
runtime wrap) is a contributor how-to: see `docs/DEVELOPMENT.md` → "Startup
customization order".  The module docstrings above are the canonical source for
each layer's behaviour.

### Resource System

| Module | One-line role |
|--------|---------------|
| `resources/auto.py` | Auto-generated resources from OpenAPI GET endpoints |
| `resources/custom.py` | Hand-written resource implementations (factory + static) |
| `resources/factory.py` | ``make_api_resource()`` factory with auto schema derivation and URI-template derivation (spec path + wildcard extension + query suffix) |
| `resources/meta.py` | ``ResourceMeta`` dataclass, ``size_hint`` / ``default_detail`` auto-derivation |
| `resources/surface.py` | Registered resource surface — the single source of truth for cache-invalidation targets and per-resource cache TTLs |
| `tools/display.py` | Bespoke display formatter **plugins** — the one view the schema cannot express (`labels`, which carries accepted-format/validation guidance).  Every other agent-facing markdown view is derived from the response schema by `format._generic_collection_view`; the only curated knowledge is the converter's deficiency tables (`openapi_converter/display_hints.py`), resolved at registration into `view_hints`.  Holds no registry state — the registry lives in `format.py`, and `server.py` (the composition root) imports this module for its registration side effect |
| `tools/resource_display.py` | Resource content helpers — `extract_resource_content` (pull text from a `ResourceResult`) and a `clean_resource_uri` re-export.  `read_resource` is an ordinary synthetic tool whose executor returns an `ExecutionResult` rendered by the single pipeline |
| `resources/scope.py` | Scope derivation for tools and resources |
| `tools/mcp_tools.py` | ``list_resources`` / ``read_resource`` tools, tool schema resource |

### Server Setup Orchestration (startup-only)

| Module | One-line role |
|--------|---------------|
| `server_setup/spec_loader.py` | Fetch, convert, extend; compute excluded routes |
| `server_setup/mcp_builder.py` | Create provider + wire tools |
| `server_setup/resource_setup.py` | Orchestrate resource registration (custom → auto) |
| `server_setup/mcp_extensions.py` | YAML-based parameter extensions |
| `server_setup/http_server.py` | HTTP transport runner (uvicorn) |

### Flat Infrastructure Modules (shared, not domain-specific)

| Module | One-line role |
|--------|---------------|
| `context_utils.py` | Safe MCP context helpers (``safe_ctx_info``, ``safe_ctx_report_progress``) |
| `request_context.py` | Request-scoped ContextVars shared across layers (``sudo_context``) |
| `models.py` | TypedDict models for structured output types (zero runtime overhead) |
| `marker.py` | Agent-facing ``$ref`` marker contract (``RefMarker`` / ``ref_marker`` / ``is_ref_marker`` / ``ref_marker_label``) |
| `ref_resolver.py` | Shared payload-``$ref`` chain resolver (``resolve_ref_chain``) used by the collapse and the compact example generator.  A ``payload-resolution``-tier module (above the converter, below format) — not a leaf |
| `schema_utils.py` | Shared JSON Schema type utilities (shared leaf) |
| `scope.py` | Scope derivation (shared leaf between tools/ and resources/) |
| `search.py` | Generic BM25 search engine (infra layer) |
| `pagination.py` | Pagination metadata, headers |
| `validation.py` | Argument validation + schema augmentation — the two-layer enum mechanism (schema-driven validation + description inference for spec types that lack machine-readable enums) |
| `cache_invalidation.py` | `CacheInvalidationMiddleware` — derives write-tool invalidation targets from the spec + registered resource surface and clears the response cache after successful writes |
| `response_cache.py` | `ResponseCacheMiddleware` — TTL cache for resource reads and listings, keyed by canonical (percent-decoded) resource URIs |
| `param_rename.py` | The ``x-param-rename`` contract — the single home for the spec operation's normalized→wire rename map.  ``read_param_rename`` exposes the raw map to the tool surface; ``path_param_map`` returns the shared wire↔normalized view consumed by the resource factory and cache invalidation.  Leaf module |
| `uri_utils.py` | URI template helpers (``clean_resource_uri``, ``render_wildcard_segment``, ``iter_path_params`` / ``path_param_names``, ``wildcard_param_names``, ``expand_path_params``) shared by resources, tools, and display layers.  ``expand_path_params`` is the single percent-encoding contract for path substitution — the inverse of FastMCP's resource matcher |

---

## Contracts & Invariants

Present-tense rules the codebase must keep.  Each names its source; where a
guard exists, the guard — not this prose — is the contract.

**Tools are generated, then wrapped — never hand-registered.** The OpenAPI
provider generates one tool per operation; customization rides a single
server-level transform (``mcp.add_transform()``, first in the chain) that wraps
every tool whose registration record has ``wrap=True``.  Synthetic discovery
tools are registered manually but carry the same record and ride the same
transform.  Source: `server_setup/mcp_builder.py`, `tools/contract.py`.

**Lazy loading, with pinned synthetics.** Tools are not listed by default;
agents discover them via ``search_tools`` (name-match + BM25).  Synthetic tools
are always pinned in ``list_tools()``, and an empty query lists the full
catalog.  ``search`` merges tools, docs, and resources with a ``type``
discriminator.  Source: `tools/search.py`, `tools/unified_search.py`.

**Resources pass through the namespace transform unchanged.** ``GiteaNamespace``
prefixes tool names with ``gitea_`` but leaves ``gitea://`` resource URIs alone;
FastMCP's built-in ``Namespace`` would double-namespace them.  Source:
`tools/namespace.py`.

**``filtered_tools_info`` is the single source of truth for auto-generated
resource visibility.** The same structure that drops tools at spec-prep time
(``route_map_fn``) is consulted when registering auto resources, and it drives
agent-facing error messages.  Custom resources have no operationId and are gated
by ``available_scopes`` directly.  Source: `server_setup/spec_loader.py`.

**Custom resources override auto-generated ones; resource URIs, names, and
descriptions are derived from the spec, not declared.** ``make_api_resource()``
and ``auto.py`` derive from the same spec so the override is structurally
guaranteed; explicit ``uri``/``name`` are reserved for non-spec-mirror
resources.  Source: `resources/factory.py`.

**Response-schema wrapping is permanent; the inner schema is derived once and
cached.** FastMCP requires ``output_schema`` to be ``type: object``, so every
JSON response schema is wrapped as ``{"result": ...}`` and is never unwrapped
on ``tool.output_schema``.  Consumers that need the actual API response shape
read the inner schema (the record's ``output_schema_raw`` / content-meta
``response_schema``), derived once at storage time.  The wrapped form is
validated by FastMCP; the inner form feeds collapse and example generation.
Source: `openapi_converter/core.py`, `tools/schemas.py`.

**The primary response type is stamped operation-level, pre-wrap.**
``stamp_type_references`` writes ``x-response-type`` onto the operation before
wrapping inlines the media-type ``$ref`` (which would otherwise erase the root
type name).  The registration layers propagate it into the tool record /
resource content meta, and the display pipeline resolves type-bound formatters
from it.  This is our metadata, not a Gitea leak.  Source:
`openapi_converter/type_references.py`.

**Cache-invalidation targets are derived from the spec + the registered
resource surface, never hardcoded.** A write at path ``P`` invalidates every
resource whose api_path is a prefix of (or equal to) ``P``; a write carrying
``x-modifies-type`` invalidates every resource whose response schema references
that type (transitive and deliberately conservative — broad invalidation is
preferred to stale hits on busy servers).  The store canonicalises keys by
percent-decoding once.  Source: `resources/surface.py`,
`server_setup/mcp_builder.py`.

**The ``x-*`` strip is surgical: schema-level only.** Gitea leaks ``x-go-name``
/ ``x-go-package`` on schema properties; those are stripped at conversion.
Operation-level ``x-*`` carry meaning — ``x-original-content-types``,
``x-mcp``, and our own ``x-response-type``, ``x-resource-types``,
``x-modifies-type``, ``x-param-rename``, ``x-wildcard-path-param`` — and must
be preserved.  Do not broaden the strip to the whole spec.  Source:
`openapi_converter/core.py`.

**Parameter spelling is normalized once and mapped back to the wire form at the
edge.** Non-snake_case path/query/header/cookie params and body properties are
renamed (camelCase/PascalCase/kebab-case → snake_case) and body/path collisions
get a ``body_`` prefix; both are recorded in ``x-param-rename`` on the
operation.  Runtime corrects FastMCP's ``parameter_map`` so the HTTP request
still sends the original wire name.  ``x-param-rename`` is the single rename
carrier, shared by the tool shim, the resource factory, and cache invalidation.
Source: `openapi_converter/normalize.py`,
`openapi_converter/param_collision.py`, `param_rename.py`.

**Only agent-misleading spec quirks are normalized; everything else is mirrored.**
Rules trigger on spec *shape* (naming convention, response structure) where
possible; the two source-driven exceptions (wildcard path params, scope-tag
reconciliation) are curated from the Gitea router because the information is
erased from the spec, and each carries a two-directional drift guard.  Source:
`openapi_converter/normalize.py`.

**One result pipeline.** Every tool — autogen, synthetic, and ``read_resource``
— returns raw data only (an ``ExecutionResult``); ``tools/result_pipeline.render``
applies shape → paginate → format and is the single writer of both output
channels.  No display logic lives in an executor.  Source:
`tools/result_pipeline.py` (canonical module docstring).

**The markdown collection view is schema-anchored, not hand-written.** The
generic collection view derives from the response type's schema — a relation is
any property whose schema references an object type, so an unknown type
compacts its relations for free.  The only curated knowledge is the converter's
deficiency tables (``omit`` / ``compact`` / ``flag``), validated against the
schema at conversion (a stale hint fails loudly) and resolved once at
registration into ``view_hints``; the render path never reads the spec.  Source:
`format.py`, `openapi_converter/display_hints.py`.

**Descriptions come from the spec; overrides are explicit.** Tool descriptions
default to the OpenAPI operation ``summary``; per-tool overrides live in
``mcp_extensions.yaml``.  Hand-crafting descriptions for ~400 tools would drift
from the spec.  Source: `server_setup/mcp_extensions.py`.

**The dependency direction is enforced.** ``tests/unit/test_layer_contract.py``
orders modules into layers (leaf → converter → payload-resolution → format →
client → runtime → setup → root) and fails on any upward import or forbidden
edge; a module in no layer fails, so a new top-level module forces a placement
decision.  ``ref_resolver`` sits in ``payload-resolution`` (it calls the
converter's ``resolve_spec_ref`` and is consumed by ``format``); the shared
leaves (``schema_utils``, ``scope``, ``request_context``) may not be imported
upward.  A subpackage-local import path is a re-export, not a copy.  Source:
`tests/unit/test_layer_contract.py`, `tests/helpers/import_graph.py`.

**Registration metadata is one typed record, read only through sanctioned
accessors, and finalised at exposure.** Every exposed tool and registered
resource carries a complete record under ``meta["registration"]``; runtime code
reads it via ``get_tool_registration`` / ``get_resource_registration``.  A
``ToolRegistration`` is finalised once at ``_ToolWrappingTransform._wrap``
(virtual params injected); the contract spine raises on a pending record.  The
record travels on the wire as the MCP ``_meta.registration``, so the contract is
checkable over the raw SDK transport.  Source: `registration.py`,
`tests/integration/test_registration_contract.py`.

**The module map is executable.** ``tests/unit/test_architecture_doc.py`` fails
when a file named in the Module Map is missing, when a production module is
absent from the map, or when a named package does not exist.  Source:
`tests/unit/test_architecture_doc.py`.

**Observability is free when unset.** FastMCP emits native OTEL spans for all
MCP operations; we add three custom child spans per tool (``validate``,
``validate_labels``, ``execute``) for per-stage latency.  The spans are no-ops
unless an OpenTelemetry SDK and exporter are configured.  The operational
how-to lives in `docs/DEVELOPMENT.md` → "OpenTelemetry Observability".  Source:
`server_setup/mcp_builder.py`.

---

## Response Content-Type Handling

Gitea's API mixes content types: most endpoints return JSON, but some return
plain text (diffs, patches), base64-encoded JSON (file content), or binary
blobs (zip archives).  Handling this correctly requires coordination across five
stages.

| Stage | Location | What it does |
|-------|----------|--------------|
| 0. Spec-time patching | `openapi_converter/core.py` (`OperationTransformer.transform()`) | For `ContentsResponse` endpoints (`GET /.../contents/{path}`), patch `produces` to `["text/plain"]` and set `x-response-transform: base64-decode`.  Detection is schema-driven (`_response_is_contents_base64` checks for a `$ref` ending in `/ContentsResponse`), not an operationId list.  `normalize_spec()` sets the second `x-response-transform` value, `"boolean-check"` |
| 1. Spec conversion | `openapi_converter/core.py:convert_responses()` | `produces` sets the OpenAPI 3.1 `content` type on each response; defaults to `application/json` when absent (correct for ~95% of endpoints, silently wrong for the ~12 non-JSON ones if `produces` propagation is missed) |
| 2. Schema wrapping | `openapi_converter/core.py:_wrap_success_response_schemas` | Wrap `application/json` schemas in `{"type": "object", "properties": {"result": ...}}`.  Non-JSON responses are implicitly skipped — `_wrap_response_schema` only looks at `content["application/json"]` |
| 3. Output schema derivation | `tools/schemas.py:derive_output_schema` | JSON → the wrapped schema.  Non-JSON → `output_schema = None` (the MCP SDK skips output validation).  Empty-body responses get a `{"result": null}` fallback schema (see Empty-body) |
| 4. Runtime classification | `server_setup/mcp_builder.py:_pipeline_with_context` | Classify the HTTP response into a result shape and return an ``ExecutionResult``; the single result pipeline renders it |

### Runtime response classes

- **JSON** — `response.json()` succeeds; the executor classifies arrays as
  `shape="list"` (paginated, with the total from the `X-Total-Count` header
  captured by an httpx event hook) and objects as `shape="object"`.
- **Text/plain** (diffs, patches) — classified `shape="text"`; the pipeline
  wraps the raw text in `{"result": text}`.  Triggered by `is_text_response`
  (from `x-original-content-types`).
- **Binary** (`application/zip`, octet-stream) — classified `shape="binary"`
  with structured `content_info` metadata (type, size, guidance) instead of raw
  bytes; the raw bytes remain reachable via `format="raw"`.  Triggered by
  `is_binary_response`.
- **Base64-encoded JSON** (`ContentsResponse`) — at runtime `response.json()`
  succeeds, so `structured_content` is not `None`; the executor detects
  `response_transform == "base64-decode"`, decodes via `decode_base64_content`
  (shared in `format.py`), and classifies `shape="text"`.  Detection falls back
  to inspecting the resolved schema for `encoding` + `content` properties
  (`_compute_tool_schema`) because Forgejo's spec structure varies.
- **Empty-body** (204/205) — classified `shape="empty"` with the message
  `"Operation completed successfully."`; the json/raw text is the serialized
  `{"result": null}` envelope.
- **Unwrapped JSON** (no output schema) — FastMCP leaves `structured_content`
  as the raw dict or `None`; the executor classifies `shape="object"` /
  `shape="text"` accordingly.

### The `x-fastmcp-wrap-result` Extension

FastMCP's `OpenAPIProvider` wraps the raw API response in structured content
matching the output schema when the operation carries `x-fastmcp-wrap-result`
(set during `_wrap_success_response_schemas`).  This is how `{"result": data}`
is produced at runtime for JSON endpoints.  For non-JSON endpoints the extension
is absent (no wrapping in stage 2), so `output_schema = None` is paired with the
runtime fallback to produce the same `{"result": text}` shape.

### Empty-body Responses (200, 201, 202, 204, 205)

Some endpoints return success with no body (204/205, or 202 without a body);
others (e.g. `POST /repos/{owner}/{repo}/pulls/{index}/merge`) return 200/201
with an explicitly empty body via `$ref: #/responses/empty`.

- **Schema time** (`_apply_fallback_schemas`): `response_has_no_content()` in
  `tools/schemas.py` checks for a 2xx response without a `content` key.
  202/204/205 are always checked; 200/201 only when the response uses `$ref`
  (inline 200/201 without `content` are treated as spec gaps).  A
  `{"result": null}` schema is set, triggering `x-fastmcp-wrap-result: true`.
- **Runtime**: when `is_empty_response` is true and `structured_content` is
  still `None`, the executor classifies `shape="empty"`.  The pipeline renders
  `{"result": None}` — a visible confirmation instead of a silent empty string.

`serialize_tool_schema` omits a `None` `output_example` rather than emitting
`null`, since agents can infer the shape from `output_schema`.

---

## Agent-Facing Documentation

The file `gitea_mcp_server/docs/agent_instructions.md` is loaded as FastMCP
server instructions and served as context to agents at connection time.  It
explains how to discover and use tools/resources from the agent's perspective.

Its size is guarded as the *template* (``agent_instructions.md`` with
placeholders unresolved) by `test_agent_instructions_line_budget`; the
generated workflow-guide manifest is not counted. The injected doc is
orientation only -- welcome, feature introduction, quick start, and discovery.
Reference depth lives in discoverable homes: tool schemas (`tool_info`),
`TOOL_ANNOTATIONS.md`, `SCOPE_MODEL.md`, and the workflow guides -- including
the agent-facing `tool-output-format` guide
(`gitea_mcp_server/docs/guides/tool-output-format.md`) for reading results.
Implementation internals (the dual-channel contract, deterministic `raw`,
envelope location, base64 handling, skip-slice) stay in this document, never on
the agent surface. See `docs/AGENT_INSTRUCTIONS_STANDARDS.md` for the boundary
and editing rules.
