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
| `tools/search.py` | BM25 search engine + `TolerantSearchTransform`, synthetic tools |
| `tools/virtual_params.py` | Virtual parameter registry + lifecycle — generic mechanism for agent-facing params stripped before HTTP call. Registered entries: ``sudo`` (user impersonation, scope-gated by token permissions). The ``format`` param is promoted to a first-class concept handled directly in ``_ToolWrappingTransform._wrap()``. |
| `tools/namespace.py` | `GiteaNamespace` transform (prefix tools, pass resources) |

Scope derivation (`derive_required_scope`) lives in `resources/scope.py` -- it is
shared between tool customization and resource registration.

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
)
```

The lifecycle functions are called automatically in ``_wrap()``:

1. ``inject_into(tool.parameters)`` — adds the param to every tool's schema
2. ``extract_from(kwargs)`` — pops it from kwargs before the HTTP request
3. ``apply_pre_hooks(extracted)`` — runs pre-hooks (e.g. set ContextVar via
   ``_sudo_pre_hook``)
4. ``apply_to(result, extracted)`` — runs post-hooks after the API call

**Scope-gating**: If a param only makes sense when the active token has a
particular scope (e.g. ``sudo``), set ``required_scope`` on its
``VirtualParam`` entry to the scope string (e.g. ``required_scope="sudo"``).
At startup, call :func:`apply_scope_filter(available_scopes)
<gitea_mcp_server.tools.virtual_params.apply_scope_filter>` — it sets
``visible`` on each param based on whether the token has the required
scope (or the ``"all"``-access shorthand).  ``inject_into`` checks
``vp.visible`` generically, so agents never discover a param they can't
use.

To add a new scope-gated param: just add ``required_scope="scope_name"``
to its ``VirtualParam(...)`` registration.  ``apply_scope_filter`` picks
it up automatically — no other file changes needed.

.. note::

    The ``format`` parameter is **not** implemented as a virtual param.
    It is a promoted, first-class concept handled directly in
    ``mcp_builder._ToolWrappingTransform._wrap()`` and reads its default
    from ``Config.response_format``.

---

## How to Add a Custom Resource

1. **Add formatter** (if needed) in `resources/format.py`:
   ```python
   def _format_my_type(data: dict) -> str:
       ...
   ```

2. **Write the resource function** in `resources/custom.py`:
   ```python
   async def my_resource(param: str, gitea_client: GiteaClient) -> ResourceResult:
       """Description for agents."""
       data = await gitea_client.request("GET", f"/api/path/{param}")
       return _format_my_type(data)
   ```

3. **Add to the `custom_resources` list** in `register_custom_resources()`:
   ```python
   custom_resources: list[tuple[str, Callable, str, set[str], dict | None]] = [
       ("gitea://my/{param}", my_resource, "text/markdown",
        {"wrapper", "my_type"}, {"required_scope": "read:repository"}),
       # ...
   ]
   ```

4. **Add URI to `AUTO_GENERATED_RESOURCE_SKIP_URIS`** in `constants.py` if a
   GET endpoint exists for the same path -- this prevents the auto-generated
   raw JSON resource from conflicting.

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

Domain-specific resource formatters still go in `resources/format.py`.

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

### Execution order

1. Token scope filter (`tool_filter.py`) — removes tools the token can't use
2. Exclusion config (new) — removes excluded, re-adds included
3. Runtime wrapping (`_ToolWrappingTransform`) — validation, labels, error handling, context logging, progress reporting

---

## Common Pitfalls

1. **Don't edit on `main`** -- Always create a feature branch first.
2. **Don't import from outside `__all__`** in production code.  Internal
   functions may be renamed/refactored without notice.
3. **Resource URIs conflict** -- When adding a custom resource that shadows
   a GET endpoint, always add the URI to `AUTO_GENERATED_RESOURCE_SKIP_URIS`.
4. **Tests that make HTTP calls** -- Use `respx` to mock the Gitea API.
   Integration tests need a real `.env` with credentials.
5. **Cache confusion** -- Resource reads are cached.  If your changes don't
   appear, check cache TTL or invalidate manually.
6. **Schema changes** -- The `openapi_converter.py` transforms Swagger 2.0 → 3.1.
   If you add a new schema feature, ensure the converter preserves it.  Note:
   the converter *intentionally* strips all `x-*` vendor extensions from
   schema objects (Gitea leaks `x-go-name`/`x-go-package` Go internals) -- this
   is by design, not a bug.  Operation-level `x-*` fields (`x-original-content-types`,
   `x-mcp`) are preserved because the pipeline depends on them.

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
- `BM25SearchTransform` -- lazy loading with BM25 search
- `Transform` -- modify tool lists, intercept tool lookups
- `Tool.from_tool(existing, transform_fn=...)` -- wrap existing tools with new behavior
- `FastMCP(name=..., lifespan=lifespan)` -- async context manager for resource lifecycle (startup/teardown)
- `CurrentContext()` -- async context manager that resolves the current MCP ``Context`` inside a request scope
- `ctx.info()` / `ctx.warning()` / `ctx.error()` / `ctx.debug()` -- client-side structured logging
- `ctx.report_progress(progress=..., total=...)` -- send progress updates to the agent host (both floats)

For up-to-date FastMCP docs: https://gofastmcp.com/llms.txt
