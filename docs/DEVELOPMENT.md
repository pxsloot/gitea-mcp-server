---
audience: developer
type: how-to
covers: Env setup, running, adding customizations/resources, MCP extensions, exclusion config, OTEL
---

# Development Guide

## Environment Setup

```bash
# Install mise (if not installed): https://mise.jdx.dev
mise install
mise trust
eval "$(mise activate bash)"

# Activate the project virtualenv
.venv/bin/activate  # or: mise exec -- ...

# Install dependencies
uv sync

# Copy and configure env
cp .env.example .env
# Edit .env: GITEA_URL, GITEA_TOKEN
```

**Key**: The `.venv` managed by `mise` must be active. The system Python
will not work -- dependencies are pinned via `uv.lock`.

---

## Running the Server

```bash
# Stdio transport (default)
uv run python -m gitea_mcp_server

# HTTP transport
TRANSPORT_TYPE=http uv run python -m gitea_mcp_server
```

---

## Running Tests

```bash
# All tests
uv run pytest

# Specific area
uv run pytest tests/unit/openapi_converter/
uv run pytest tests/unit/test_tool_annotations.py -v

# With coverage
uv run pytest --cov=gitea_mcp_server

# Integration tests (respx-mocked, no external deps)
uv run pytest tests/integration/

# Live end-to-end tests (need real Gitea instance + .env.dev.local)
uv run pytest tests/live/
```

See `docs/TESTING_STANDARDS.md` for full details.

---

## Code Organization Rules

### Public vs Private

- Functions that are **implementation details** should be prefixed with `_`.
- The module's `__all__` documents the intended public API.
- Tests may import private functions (they test internals), but production
  code should only import from `__all__`.

### File Responsibilities

| Directory | Contains |
|-----------|----------|
| `gitea_mcp_server/` | Core modules -- config, client, conversion, server assembly, exceptions, constants, `label_service`, `format` |
| `gitea_mcp_server/tools/` | **Runtime** tool customization -- customize, schemas, errors, labels, examples, exclusion, search, virtual_params, namespace |
| `gitea_mcp_server/resources/` | **Runtime** resource system -- auto-generated, custom, format helpers, scope derivation, resource registration |
| `gitea_mcp_server/server_setup/` | **Startup-only** -- spec loading, MCP builder, extensions, resource orchestration, permissions |
| `gitea_mcp_server/docs/` | **Agent-facing** documentation (loaded as MCP server instructions) |
| `docs/` | **Developer-facing** documentation (this file, ARCHITECTURE.md, etc.) |
| `tests/` | Unit tests (`unit/`) and integration tests (`integration/`) |

---

## How to Add a Tool Customization

Tool customizations are organized under `gitea_mcp_server/tools/`:

| Module | Concern |
|--------|---------|
| `tools/customize.py` | Helpers: title/category generation, hint inference, invalidation |
| `tools/schemas.py` | Output schema derivation, `$ref` resolution |
| `tools/errors.py` | Error translation, argument validation runner |
| `tools/labels.py` | Label name→ID conversion, label schema updates |
| `tools/examples.py` | Schema→example generation, tool schema serialization |
| `tools/search.py` | Name-match + BM25 search + `TolerantSearchTransform`, synthetic tools |
| `tools/type_info.py` | ``resolve_type`` tool + ``gitea://types/{typeName}`` resource — ``$ref:Type`` name resolution and cross-references |
| `tools/virtual_params.py` | Virtual parameter registry + lifecycle — generic mechanism for agent-facing params stripped before HTTP call. Registered entries: ``sudo`` (user impersonation, scope-gated by token permissions). The ``format`` param is promoted to a first-class concept handled directly in ``_ToolWrappingTransform._wrap()``. |
| `tools/namespace.py` | `GiteaNamespace` transform (prefix tools, pass resources) |

Scope derivation — see `docs/SCOPE_MODEL.md` for the full scope model
(derivation, filtering, and virtual-param gating).

The customization pipeline has two phases:

1. **`_customize_metadata()`** in `server_setup/mcp_builder.py` — in-place
   metadata (title, annotations, hints, labels, invalidation) applied per-tool
   at startup via OpenAPIProvider's ``mcp_component_fn`` hook.
2. **`_ToolWrappingTransform._run_transform_pipeline()`** in
   `server_setup/mcp_builder.py` — runtime wrapping (validation, label
   conversion, error handling, text wrapping, pagination) applied via a
   provider-level ``Transform`` at query time.  The pipeline now also
   injects the MCP ``Context`` object (via ``CurrentContext()``) for
   ``ctx.info()`` logging and ``ctx.report_progress()`` calls at key
   stages, and extracts the core logic into ``_pipeline_with_context(ctx)``
   for clean separation.

Common customizations:

### 1. Schema augmentation (parameter constraints)

Add to `SCHEMA_CONSTRAINTS` in `validation.py`:

```python
SCHEMA_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "owner": {"minLength": 1, "maxLength": 50, "pattern": OWNER_REPO_PATTERN},
    # ... add new parameter constraint
}
```

### 2. Custom annotation hints

Annotations are inferred from HTTP method in `add_inferred_hints()`.
To override for a specific tool, use `mcp_extensions.yaml`:

```yaml
tool_names:
  repo_delete:
    title: "Delete Repository"
    description: "Permanently deletes a repository..."
```

### 3. New validation function

1. Add validator in `validation.py`
2. Add to `SINGLE_VALIDATORS` dict keyed by parameter name
3. The runtime pipeline `_ToolWrappingTransform._run_transform_pipeline()` in `server_setup/mcp_builder.py` automatically calls it

### 4. Cache invalidation pattern

Add to `TOOL_INVALIDATION_PATTERNS` in `constants.py`:

```python
TOOL_INVALIDATION_PATTERNS: list[tuple[str, str | None, list[str]]] = [
    ("/repos/{owner}/{repo}/topics", None, [PATTERN_REPO]),
    # ...
]
```

### 5. Add a virtual parameter

Virtual parameters appear in the tool schema so agents know about them, but are
stripped from ``kwargs`` before the HTTP call and can transform the result after.
They are registered by appending to the ``_VIRTUAL_PARAMS`` dict in
``virtual_params.py``:

```python
# gitea_mcp_server/tools/virtual_params.py

_VIRTUAL_PARAMS["verbose"] = VirtualParam(
    schema={"type": "boolean"},
    default=False,
    description="Enable verbose output.",
    # Optional: pre-hook runs after extraction, before the HTTP call.
    # Use for side effects like setting a context variable.
    pre_hook=_prepare_verbose,
    # Optional: post-hook transforms the result after the API call.
    post_hook=_apply_verbose,  # (result, value) -> result
    # Optional: loop-hook runs inside the execution pipeline, after the
    # HTTP call and pagination metadata but before post_hook.  Receives
    # an ``execute_fn`` callable to re-invoke the HTTP path with updated
    # arguments (e.g. incremented ``page`` for auto-pagination).
    # The ``_fetch_all_loop`` hook delegates to ``PaginationRunner``
    # (see ``gitea_mcp_server/pagination.py``) for the actual loop logic.
    loop_hook=None,  # e.g. _fetch_all_loop  (result, value, kwargs, execute_fn) -> result
)
```

The lifecycle functions are called automatically in ``_wrap()``:

1. ``inject_into(tool.parameters)`` — adds the param to every tool's schema
2. ``extract_from(kwargs)`` — pops it from kwargs before the HTTP request
3. ``apply_pre_hooks(extracted)`` — runs pre-hooks (e.g. set ContextVar via
   ``_sudo_pre_hook``)
4. ``_run_transform_pipeline(kwargs, tool, extracted=virtual_values)`` —
   executes the HTTP call and pagination metadata, then invokes every
   registered ``loop_hook`` with an ``execute_fn`` that re-invokes
   ``_run_with_error_handling`` for subsequent pages
5. ``apply_to(result, extracted)`` — runs post-hooks after the API call

A ``loop_hook`` is how you implement params that need to **re-execute** the
HTTP call — for example auto-pagination (``fetch_all``).  Unlike pre/post hooks
which are pure value transformers, a loop hook receives a callable
``execute_fn(updated_kwargs) → ToolResult`` so it can fetch additional pages
and merge results.  The hook should update the ``ToolResult``'s
``structured_content`` (typically setting ``has_more=False``) and return it.

.. note::

    ``fetch_all`` has two implementations depending on tool type:

    **API tools** (auto-generated from OpenAPI spec):
    ``fetch_all`` is a virtual parameter registered in ``virtual_params.py``.
    The ``_fetch_all_loop`` hook (a thin wrapper around
    :class:`~gitea_mcp_server.pagination.PaginationRunner`) fetches all pages
    via HTTP and merges them into a single result, capped at
    ``FETCH_ALL_MAX_PAGES`` pages.  See ``gitea_mcp_server/constants.py`` for
    the cap value.

    **Synthetic tools** (``search_tools``, ``search_resources``,
    ``search_docs``, ``search``, ``list_resources``):
    ``fetch_all`` is declared as an explicit parameter in the tool's function
    signature.  Since all data is already in memory (tool catalog, doc index,
    resource list), ``fetch_all`` simply skips the page/limit slice and
    returns all results through the shared
    :func:`~gitea_mcp_server.format._format_paginated_result` utility — no
    loop needed.

**Scope-gating**: Virtual parameters can be gated behind token scopes.
The mechanism (how `apply_scope_filter` toggles `.visible`, and how a single
`required_scope=` on a `VirtualParam` is picked up automatically) is the
canonical reference in `docs/SCOPE_MODEL.md` → "Virtual Parameter Scope Gating".
From this doc's how-to angle: to add a new scope-gated param, set
`required_scope=` on the `VirtualParam` and nothing else changes.

.. note::

    The ``format`` and ``detail`` parameters are **not** implemented as
    virtual params.  They are promoted, first-class concepts handled
    directly in ``mcp_builder._ToolWrappingTransform._wrap()``.

    ``format``'s default is injected at construction time via
    ``response_format``, so the transform never calls ``Config.get()``
    at wrap time.  ``detail`` is injected per-tool from the shared
    ``DETAIL_PARAM_SCHEMA`` constant.      Both are popped from ``kwargs``
    before the HTTP call and forwarded to ``apply_format`` in the output
    formatting layer.

    Because ``format`` and ``detail`` are not virtual params, they don't
    appear in ``virtual_params.py`` and don't go through the
    ``extract_from`` / ``apply_to`` lifecycle.  If you need to add
    another param that affects output formatting only (not the API call),
    follow the same pattern: inject it in ``_ToolWrappingTransform``,
    pop it from kwargs alongside ``format`` and ``detail``, and pass it
    to the formatting functions.  See ``constants.py`` and
    ``mcp_builder.py`` for the canonical implementation.

---

## How to Add a Custom Resource

### Preferred: Use the factory (``make_api_resource``)

For most API-backed resources, use the factory in `resources/factory.py`.
It auto-derives the response schema from the OpenAPI spec, handles
``str`` vs JSON branching, and registers the resource in one call -- no
manual ``_get_success_schema`` / ``_unwrap_result_schema`` boilerplate.

1. **Add a display formatter** (if needed) in `tools/display.py`:
   ```python
   @register_formatter("my_type")
   def _format_my_type(data: dict, *, detail: str = "full") -> str:
       ...
   ```

2. **Add a factory call** in `register_custom_resources()` in
   `resources/custom.py`:
   ```python
   from gitea_mcp_server.resources.factory import make_api_resource

   make_api_resource(
       mcp, gitea_client, openapi_spec,
       uri="gitea://my/{param}",
       api_path="/api/path/{param}",
       method="GET",
       format_hint="my_type",
       scope="read:repository",
       cache_ttl=300,
       tags={"my_tag"},
       error_message="My resource '{param}' not found.",
       available_scopes=available_scopes,
   )
   ```

The factory:
- Derives the response schema automatically from ``openapi_spec[api_path][method]``
- Generates a handler closure that calls ``gitea_client.request``
- Handles ``isinstance(data, str)`` branching (text/plain vs application/json)
- Attaches the schema and ``format_hint`` in ``ResourceContent.meta``
- Registers via ``mcp.resource()`` and adds the URI to ``_registered_uris``
- Skips registration when the token's scopes are insufficient
- Returns ``None`` if scope-filtered, the handler otherwise

**Optional query parameters**: For resources with optional query params
(e.g. ``state`` filter on issues/pulls), set ``query_params=["state"]``.
The factory extracts those kwargs into a ``params`` dict passed to the
API call — they are *not* substituted into the path template.  When the
param must be validated against a fixed set of values (e.g. ``"open"`` /
``"closed"``), add ``query_param_validators={"state": ["open", "closed"]}``
and the handler raises a clear ``ResourceError`` on invalid input.

**Discovery metadata**: Set ``optional_params=[{"name": "state", ...}]``
to surface available optional parameters in the ``list_resources`` output.
Each dict should have at least a ``"name"`` key; ``"type"``, ``"values"``,
and ``"description"`` are recommended.

See the issues and pulls factory calls in ``custom.py`` for a complete example::

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/issues",
        api_path="/repos/{owner}/{repo}/issues",
        format_hint="issues",
        resource_type="issues",
        scope="read:repository",
        tags={"issues"},
        query_params=["state"],
        query_param_validators={"state": ["open", "closed"]},
        optional_params=[{"name": "state", "type": "string", "values": ["open", "closed"]}],
        available_scopes=available_scopes,
    )

No manual ``AUTO_GENERATED_RESOURCE_SKIP_URIS`` maintenance is needed --
the factory's ``_registered_uris`` set is populated at registration time
and combined with the legacy ``_NON_FACTORY_SKIP_URIS`` set (in
``auto.py``) by ``resource_setup.py`` to form the auto-generation skip list.

**Note**: If future patterns repeat (many list resources sharing the same
structure), consider extracting higher-level wrappers like
``make_list_resource()`` that compose ``make_api_resource`` with common
defaults.  The current approach adds params directly to the factory
(``Option A``) — straightforward and zero-impact on existing consumers.

### Legacy: Hand-written resource (``@_register`` pattern)

For resources with special logic (base64 decoding, static pre-computed data,
non-GET methods), use the legacy ``@_register`` decorator pattern:

1. **Add a display formatter** (if needed) in `tools/display.py`.
2. **Write the resource function** in ``resources/custom.py`` with the
   legacy ``@_register`` + ``@resource_handler`` decorators (see existing
   handlers for reference).
3. **No skip-URI update needed** -- add the URI to ``auto.py``'s
   ``_NON_FACTORY_SKIP_URIS`` set if the resource overrides a GET endpoint
   and is not yet migrated to the factory.

### Pre-computed static resources

For resources whose data is static for the server session (server version,
token scopes, server info), pre-fetch the data at startup in
``create_mcp_server()`` and pass it as a parameter to ``register_custom_resources()``.
The handler becomes a simple closure over the cached value — no API calls on read.

```python
# In server.py: pre-fetch at startup (async context available)
version_str: str = "Unknown"
try:
    version_data = await gitea_client.request("GET", "/version")
    if isinstance(version_data, dict):
        version_str = str(version_data.get("version", "Unknown"))
except GiteaAPIError:
    pass

# Pass through the registration chain
register_all_resources(..., version_str=version_str, ...)
```

```python
# In custom.py: handler is a closure — no API call on read
@_register("gitea://version", mime_type="text/plain", ...)
async def get_version() -> ResourceResult:
    return ResourceResult(contents=[
        ResourceContent(content=version_str, mime_type="text/plain")
    ])
```

See ``register_custom_resources()`` for the available pre-computed parameters
(``version_str``, ``server_info_md``, and ``available_scopes`` for token scopes).

---

## How to Add a Synthetic Tool (and Optional Resource)

Synthetic tools and resources are hand-written (not auto-generated from the
OpenAPI spec). They live in the same codebase and register themselves via
``mcp.tool()`` / ``mcp.resource()`` directly. Examples: ``resolve_type``,
``search_tools``, ``tool_info``, ``gitea://types/{typeName}{?detail}``.

### Pattern

1. **Create a module** in ``gitea_mcp_server/tools/`` (e.g. ``tools/type_info.py``).

2. **Core logic** goes in pure functions that accept typed inputs and return
   plain dicts/lists — easy to unit test without mocking FastMCP.

3. **Registration closure** is a ``register_*`` function that takes ``mcp: FastMCP``
   (and any deps like ``openapi_spec``) and calls ``mcp.tool()`` / ``mcp.resource()``:

   ```python
   def register_my_tool(
       mcp: FastMCP,
       openapi_spec: OpenAPISpec | None = None,
   ) -> None:
       # Build index / cache at registration time
       my_data = build_my_data(openapi_spec)

       async def _my_tool_impl(
           param: str,
           ctx: Context,
           format: str = "markdown",
       ) -> ToolResult:
           """Description for agents."""
           if not my_data:
               _raise_value_error("Not available")
           await ctx.info(f"Processing '{param}'", ...)
           result = do_the_work(my_data, param)
           await ctx.report_progress(progress=1.0)
            return apply_format(result, format)

       mcp.tool(
           name="my_tool",
           description="...",
           tags={"synthetic", "my-domain"},
           annotations=synthetic_annotations(read_only=True, open_world=False),
           output_schema={...},
       )(_my_tool_impl)

       # Optional companion resource
       async def _my_resource(
           param: str,
           ctx: Context,
           detail: str = "full",
       ) -> str:
           """Description."""
           await ctx.info(...)
           info = do_the_work(my_data, param)
           return json.dumps(info, indent=2)

        mcp.resource(
            uri="gitea://my/{param}",
            mime_type="application/json",
            annotations={"readOnlyHint": True, "idempotentHint": True},
            meta=scope_meta(...),
            tags={"synthetic", "my-domain"},
        )(_my_resource)
   ```

4. **Wire into ``server.py``** by importing and calling `register_*` in
   ``create_mcp_server()`` — see lines 330–332 for the canonical placement.

5. **Export ``__all__``** with all functions (public and ``_``-prefixed helpers).

### Key conventions

| Concern | Convention |
|---------|-----------|
| Function injection | FastMCP auto-injects ``ctx: Context`` via type annotation — declare it in the handler signature |
| Observability | Use ``ctx.info()`` before/after work and ``ctx.report_progress()`` for long ops — agents rely on this |
| ``format`` param | Accept it as the last non-``ctx`` param with default ``"markdown"``, dispatch via ``apply_format()``. For paginated list results, prefer ``_format_paginated_result()`` which handles slicing, ``fetch_all``, and pagination metadata in one call. |
| ``detail`` param | Optional: ``"full"`` (default) or ``"concise"`` — controls data shaping: ``"concise"`` collapses nested ``$ref``-backed objects to ``$ref:TypeName`` labels at depth >= 1. Affects both ``json`` and ``markdown`` output. |
| Annotations | Use ``synthetic_annotations(read_only=True, open_world=False)`` for tools; annotate resources inline |
| ``meta`` / scope | Set ``meta=scope_meta(scope)`` on resources — ``None`` means scope-free (explain *why* in a comment) |
| ``openapi_spec`` parameter | Pass as ``OpenAPISpec \| None`` — handle ``None`` with a helpful error message |
| URI templates | Use ``{?param}`` in the URI template for query params so FastMCP routes them to the handler. The display layer (``_clean_resource_uri`` in ``mcp_tools.py``) strips ``{?...}`` from ``list_resources`` output, showing clean URIs. Agents discover available optional params via ``optional_params`` metadata. When using ``make_api_resource()`` with ``query_params``, the factory automatically adds the params to the handler's ``__signature__`` so FastMCP validates them as optional keyword-only parameters. For hand-written synthetic resources, include ``{?param}`` in the URI template and ensure the handler has matching optional params with defaults. |
| Import pattern | ``from fastmcp.server.context import Context`` (not ``from fastmcp import Context`` — triggers ruff TC002). Import ``OpenAPISpec`` at module level (no circular risk). **Never** use ``from __future__ import annotations`` in registration modules — FastMCP's pydantic introspection resolves type hints at registration time and will ``NameError`` on types under ``TYPE_CHECKING`` |
| Error handling | ``_raise_value_error(msg)`` raises ``ValueError``; FastMCP catches it and re-raises as ``ToolError`` (tool calls) or ``ResourceError`` (resource reads). Unit test the ``ValueError``; integration test the ``ToolError`` / ``ResourceError`` |
| Test pattern | Unit test the core logic; integration test the registration wiring. ``mcp.call_tool()`` returns ``ToolResult`` — access data via ``result.structured_content["result"]``. ``mcp.read_resource()`` returns ``ResourceResult`` — access JSON via ``json.loads(content.contents[0].content)``. Catch ``ToolError`` / ``ResourceError`` from FastMCP, not raw ``ValueError`` |

### When to choose a synthetic tool vs. customizing an auto-generated one

| Situation | Approach |
|-----------|----------|
| Wraps an existing API endpoint with formatting | Customize via ``_customize_metadata`` (see above) |
| Computes new data from the spec / index | Synthetic tool |
| Combines multiple API calls into one result | Synthetic tool |
| Exposes server metadata or configuration | Synthetic tool + resource |
| Adds a convenience alias for an existing endpoint | ``mcp_extensions.yaml`` or synthetic proxy |

---



## Shared Formatters (`format.py`)

General-purpose schema-aware formatting lives in `gitea_mcp_server/format.py`.
This module is shared by both `tools/` and `resources/` -- never import
formatting utilities from one domain into the other.

Add a utility formatter there if multiple consumers need it:

```python
# gitea_mcp_server/format.py
def _format_custom_type(data: dict) -> str:
    ...
```

Domain-specific resource formatters are registered in `tools/display.py` via the ``@register_formatter`` decorator. See "How to Add a Custom Resource" above.

---

## MCP Extensions (YAML)

The `mcp_extensions.yaml` file at project root lets you override tool titles,
descriptions, and parameter docs without touching Python code.

```yaml
tool_names:
  operation_id_name:
    title: "Human-Readable Title"
    description: |
      Detailed description of what this tool does.
      Supports multi-line.
    parameters:
      - name: param_name
        description: "Override parameter description"
```

Set `MCP_EXTENSIONS_PATH` env var to use a different file location.

---

## Tool/Resource Exclusion Config

The server supports excluding or including specific tools, resources, and
resource templates via a YAML config file.  This is useful for fine-grained
control beyond token-scope filtering — e.g., hiding destructive operations
or admin tools.

### Setup

Set the `EXCLUDE_CONFIG_PATH` env var to point to your YAML config:

```bash
EXCLUDE_CONFIG_PATH=/path/to/disable.yaml uv run python -m gitea_mcp_server
```

### Config format

```yaml
# disable.yaml
exclude:
  - "repo_delete"           # exact name match (operationId)
  - "admin_*"               # fnmatch glob on component name
  - "tag:admin"             # tag-based (all tools with 'admin' tag)
include:
  - "admin_get_server_version"   # override: re-allow within excluded group
```

### How it works

- Patterns match against both unprefixed (operationId) and prefixed
  (`gitea_`-prefixed) component names, so both forms work in patterns.
- `include` overrides `exclude`: if a component matches any include pattern,
  it passes through regardless of exclude matches.
- `include` without `exclude` is a no-op.
- Token scope filter runs **before** exclusion config: a tool filtered by
  scope cannot be re-added via include.
- The exclusion is applied as a **server-level transform**, covering tools,
  resources, and resource templates from all providers.

### Startup customization order

This is the *startup* axis: the sequence in which customization is wired into
the server before it serves requests. Tool/resource visibility filtering now
happens at spec-prep time via `route_map_fn` (see `docs/SCOPE_MODEL.md` and
`docs/ARCHITECTURE.md`), so it is no longer part of the query-time transform
chain (TolerantSearch → GiteaNamespace → ExtensionMetadata). The startup order:

1. Spec-prep filtering (`spec_loader.py`) — computes excluded routes (deprecated + scope + config-excluded) applied via `route_map_fn`
2. Runtime wrapping (`_ToolWrappingTransform`) — validation, labels, error handling, context logging, progress reporting

---

## Common Pitfalls

1. **Don't edit on `main`** -- Always create a feature branch first.
2. **Don't import from outside `__all__`** in production code.  Internal
   functions may be renamed/refactored without notice.
3. **Resource URIs conflict** -- When adding a custom resource that shadows
   a GET endpoint, either use ``make_api_resource()`` (factory auto-tracks
   URIs in ``_registered_uris``) or, for legacy ``@_register`` resources,
   add the URI to ``auto.py``'s ``_NON_FACTORY_SKIP_URIS``.
4. **Tests that make HTTP calls** -- Use `respx` to mock the Gitea API.
   Integration tests need a real `.env` with credentials.
5. **Cache confusion** -- Resource reads are cached.  If your changes don't
   appear, check cache TTL or invalidate manually.
   6. **Schema changes** -- The `openapi_converter.py` transforms Swagger 2.0 → 3.1.
    If you add a new schema feature, ensure the converter preserves it.  Note:
    the converter *intentionally* strips all `x-*` vendor extensions from
    schema objects (Gitea leaks `x-go-name`/`x-go-package` Go internals) -- this
    is by design, not a bug.  The surgical scope of that strip (schema-level only,
    operation-level `x-*` preserved) and the rationale are in
    `docs/ARCHITECTURE.md` → "Vendor extension (`x-*`) stripping in the converter".

---

## OpenTelemetry Observability

FastMCP 3.x includes native OpenTelemetry instrumentation. The server emits
auto-generated spans for all tool calls, resource reads, and prompt renders
with no code changes.

### Span Hierarchy (auto-generated + custom)

```
tools/call gitea_issue_create_issue          (auto, by FastMCP)
├── gitea_issue_create_issue.validate        (custom, validation)
├── gitea_issue_create_issue.validate_labels  (custom, label conversion)
└── gitea_issue_create_issue.execute         (custom, HTTP execution)
```

### Quick Start (local trace visualization)

```bash
# Terminal 1: Start otel-desktop-viewer (UI at http://localhost:8000)
brew install nico-barbas/brew/otel-desktop-viewer
otel-desktop-viewer

# Terminal 2: Run server with tracing
opentelemetry-instrument \
  --service_name gitea-mcp-server \
  fastmcp run python -m gitea_mcp_server
```

### Production Configuration

```bash
# Install the OTLP exporter
uv add opentelemetry-exporter-otlp

# Run with tracing
opentelemetry-instrument \
  --service_name gitea-mcp-server \
  --exporter_otlp_endpoint http://localhost:4317 \
  fastmcp run python -m gitea_mcp_server
```

Or configure via environment variables:

```bash
export OTEL_SERVICE_NAME=gitea-mcp-server
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
opentelemetry-instrument fastmcp run python -m gitea_mcp_server
```

### Testing Telemetry

Tests use ``InMemorySpanExporter`` from ``opentelemetry-sdk``. See
``tests/unit/test_mcp_builder.py::TestToolWrappingTransformTelemetry``
for the fixture pattern.

### Key Reference

- [FastMCP Telemetry Docs](https://gofastmcp.com/servers/telemetry.md)
- [OpenTelemetry Python SDK](https://opentelemetry.io/docs/languages/python/)

---

## FastMCP Reference

This project uses FastMCP 3.x.  Key APIs:

- `OpenAPIProvider(spec, client)` -- auto-generates tools from OpenAPI spec
- `ResponseCachingMiddleware` -- TTL-based resource caching
- `BM25SearchTransform` -- lazy loading with name-match + BM25 search
- `Transform` -- modify tool lists, intercept tool lookups
- `Tool.from_tool(existing, transform_fn=...)` -- wrap existing tools with new behavior
- `FastMCP(name=..., lifespan=lifespan)` -- async context manager for resource lifecycle (startup/teardown)
- `CurrentContext()` -- async context manager that resolves the current MCP ``Context`` inside a request scope
- `ctx.info()` / `ctx.warning()` / `ctx.error()` / `ctx.debug()` -- client-side structured logging
- `ctx.report_progress(progress=..., total=...)` -- send progress updates to the agent host (both floats)

For up-to-date FastMCP docs: https://gofastmcp.com/llms.txt
