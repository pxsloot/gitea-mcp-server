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

# Integration tests (need running Gitea + .env)
uv run pytest tests/integration/
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
| `gitea_mcp_server/` | Core modules -- config, client, conversion, server assembly, exceptions, constants, `label_manager`, `format` |
| `gitea_mcp_server/tools/` | **Runtime** tool customization -- customize, schemas, errors, labels, examples, exclusion, search, namespace |
| `gitea_mcp_server/resources/` | **Runtime** resource system -- auto-generated, custom, format helpers, scope derivation, registry |
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
   provider-level ``Transform`` at query time.

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
        {"wrapper", "my_type"}, {"fastmcp": {"_internal": {"required_scope": "read:repository"}}}),
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
3. Runtime wrapping (`_ToolWrappingTransform`) — validation, labels, error handling

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
   If you add a new schema feature, ensure the converter preserves it.

---

## FastMCP Reference

This project uses FastMCP 3.x.  Key APIs:

- `OpenAPIProvider(spec, client)` -- auto-generates tools from OpenAPI spec
- `ResponseCachingMiddleware` -- TTL-based resource caching
- `BM25SearchTransform` -- lazy loading with BM25 search
- `Transform` -- modify tool lists, intercept tool lookups
- `Tool.from_tool(existing, transform_fn=...)` -- wrap existing tools with new behavior

For up-to-date FastMCP docs: https://gofastmcp.com/llms.txt
