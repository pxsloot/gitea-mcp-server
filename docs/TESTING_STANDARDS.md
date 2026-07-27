---
audience: developer
type: reference
covers: Test layout, zones, fixtures, mocking rules, coverage targets
---

# Testing Standards

This document defines the testing standards and best practices for the Gitea MCP Server project.
It is a living document — update it when patterns or conventions change.

## Test Structure and Organization

### Directory Layout

```
tests/
├── __init__.py
├── conftest.py                             # Infrastructure: SimpleConfig, event_loop, OTel
├── helpers/
│   ├── __init__.py
│   ├── mock_tool.py                        # make_mock_tool, make_mock_route
│   ├── tool_names.py                       # extract_tool_names
│   └── spec_fixtures.py                    # base_spec, minimal_spec
├── schemas/
│   ├── openapi_3.1_schema.json             # JSON Schema for validating converted specs
│   └── openapi_3.1.1_schema.json
├── swagger.v1.json                         # Extracted subset of Gitea's Swagger spec (test fixture)
├── manual_test_cache_invalidation.py       # Standalone manual verification script
├── unit/
│   ├── __init__.py
│   ├── openapi_converter/                  # Tests for Swagger 2.0 → OpenAPI 3.1 conversion
│   │   ├── test_definitions.py
│   │   ├── test_email_date_handling.py
│   │   ├── test_operation_id_normalization.py
│   │   ├── test_parameters.py
│   │   ├── test_paths.py
│   │   ├── test_responses.py
│   │   ├── test_swagger_to_openapi.py
│   │   └── test_utils.py
│   ├── test_cache_invalidation.py
│   ├── test_client.py
│   ├── test_client_gitea_api.py
│   ├── test_client_http_transport.py
│   ├── test_config.py
│   ├── test_constants.py
│   ├── test_display.py
│   ├── test_docs_tools.py
│   ├── test_exceptions.py
│   ├── test_extensions_metadata.py
│   ├── test_filter_info.py
│   ├── test_format.py
│   ├── test_label_service.py
│   ├── test_label_transform.py
│   ├── test_label_validation.py
│   ├── test_logging_config.py
│   ├── test_mcp_builder.py
│   ├── test_mcp_extensions.py
│   ├── test_mcp_tools.py
│   ├── test_mcp_tools_wrapping.py
│   ├── test_pagination.py
│   ├── test_regression_316_dotfile_paths.py
│   ├── test_resource_factory.py
│   ├── test_resource_meta.py
│   ├── test_resources.py
│   ├── test_schema_utils.py
│   ├── test_scope.py
│   ├── test_search_bm25.py
│   ├── test_spec_loader.py
│   ├── test_tool_customize.py
│   ├── test_tool_display.py
│   ├── test_tool_errors.py
│   ├── test_tool_examples.py
│   ├── test_tool_exclusion.py
│   ├── test_tool_filter.py
│   ├── test_tool_labels.py
│   ├── test_tool_namespace.py
│   ├── test_tool_schemas.py
│   ├── test_tool_search.py
│   ├── test_type_info.py
│   ├── test_unified_search.py
│   ├── test_validation.py
│   └── test_virtual_params.py
├── integration/
│   ├── __init__.py
│   ├── conftest.py          # mcp_server, search_mcp_server, create_test_server
│   ├── test_cache_invalidation.py
│   ├── test_http_transport_server.py
│   ├── test_lazy_loading.py
│   ├── test_mcp_extensions_integration.py
│   ├── test_resources_integration.py
│   ├── test_server.py
│   └── ...
├── live/
│   ├── __init__.py
│   ├── conftest.py         # Dotenv loading, skip-if-unreachable, test data lifecycle
│   └── test_diff_endpoint.py
```

### Source-to-Test Mapping

Every production module in `gitea_mcp_server/` maps to one or more test files.
This table is the canonical reference.  Modules with no dedicated test file
are noted explicitly.

> **Naming convention**: Unit test files follow `test_<module_name>.py` for
> flat modules and `test_<abbrev>_<module>.py` for subpackage modules
> (e.g., `tools/search.py` → `test_tool_search.py`).  Integration tests use
> `test_<feature>.py`.  Cross-cutting or behavioral tests use
> `test_<behavior>.py`.

#### Flat modules (`gitea_mcp_server/`)

| Source module | Test file(s) | Zone | Notes |
|---|---|---|---|
| `__init__.py` | — | — | Package marker (version string only) |
| `cache_invalidation.py` | `test_cache_invalidation.py` | Unit + Integration | Both unit and integration test files |
| `client.py` | `test_client.py`, `test_client_gitea_api.py`, `test_client_http_transport.py` | Unit | Three files cover the main class, `GiteaAPI`, and `HTTPTransport` |
| `config.py` | `test_config.py` | Unit | 95% target |
| `constants.py` | `test_constants.py` | Unit | 100% target |
| `exceptions.py` | `test_exceptions.py` | Unit | 100% target |
| `format.py` | `test_format.py` | Unit | 85% target |
| `label_service.py` | `test_label_service.py`, `test_label_validation.py`, `test_label_transform.py` | Unit | Shared across label-related tests |
| `logging_config.py` | `test_logging_config.py` | Unit | 80% target |
| `models.py` | — | — | TypedDict types only (zero runtime code) |
| `openapi_types.py` | — | — | TypedDict types only (zero runtime code) |
| `pagination.py` | `test_pagination.py` | Unit | Pagination metadata and runner |
| `schema_utils.py` | `test_schema_utils.py` | Unit | Shared JSON Schema utilities |
| `scope.py` | `test_scope.py` | Unit | Re-exported by `resources/scope.py` |
| `search.py` | `test_search_bm25.py` | Unit | BM25 search engine tests. Named ``_bm25`` to disambiguate from ``tools/search.py`` → ``test_tool_search.py`` — see pragmatic deviations below |
| `server.py` | `test_server.py`, `test_server_http.py` | Integration only | No unit test; 70% target |
| `validation.py` | `test_validation.py` | Unit | 95% target |

#### `openapi_converter/` subpackage

| Source module | Test file(s) | Zone | Notes |
|---|---|---|---|
| `openapi_converter/__init__.py` | — | — | Re-exports from `core.py` and `schema.py` |
| `openapi_converter/core.py` | `openapi_converter/test_*.py` (8 files) | Unit | 95% target — spread across all 8 `openapi_converter/` test files |
| `openapi_converter/schema.py` | `openapi_converter/test_definitions.py` | Unit | No dedicated `test_schema.py`; `convert_schema()` tested via package re-export |

#### `resources/` subpackage

| Source module | Test file(s) | Zone | Notes |
|---|---|---|---|
| `resources/__init__.py` | — | — | Package marker |
| `resources/auto.py` | `test_resources.py` | Unit | See #567 (planned split — same file tests multiple resource modules) |
| `resources/custom.py` | `test_resources.py` | Unit | See #567 |
| `resources/factory.py` | `test_resource_factory.py`, `test_resources.py` | Unit | Primarily `test_resource_factory.py` |
| `resources/meta.py` | `test_resource_meta.py` | Unit | 85% target |
| `resources/scope.py` | `test_scope.py` | Unit | 13-line re-export of flat `scope.py` |

#### `server_setup/` subpackage

| Source module | Test file(s) | Zone | Notes |
|---|---|---|---|
| `server_setup/__init__.py` | — | — | Package marker (empty) |
| `server_setup/http_server.py` | — | — | **No test coverage** |
| `server_setup/mcp_builder.py` | `test_mcp_builder.py`, `test_tool_customize.py`, `test_tool_errors.py`, `test_tool_schemas.py`, `test_tool_filter.py` | Unit | 70% target; shared across customization tests |
| `server_setup/mcp_extensions.py` | `test_mcp_extensions.py` | Unit | Extensions loading |
| `server_setup/resource_setup.py` | `test_resources_integration.py` | Integration only | No unit test (patched in integration) |
| `server_setup/spec_loader.py` | `test_spec_loader.py`, `test_tool_exclusion.py`, `test_tool_filter.py` | Unit | Spec loading, conversion orchestration |

#### `tools/` subpackage

| Source module | Test file(s) | Zone | Notes |
|---|---|---|---|
| `tools/__init__.py` | — | — | Package init |
| `tools/customize.py` | `test_tool_customize.py` | Unit | Title, category, hint generation |
| `tools/display.py` | `test_display.py` | Unit | Domain-specific formatter registry |
| `tools/docs_tools.py` | `test_docs_tools.py` | Unit | DocManager, workflow guides |
| `tools/errors.py` | `test_tool_errors.py` | Unit | Error translation, runtime validation runner |
| `tools/examples.py` | `test_tool_examples.py` | Unit | Schema-to-example generation |
| `tools/exclusion.py` | `test_tool_exclusion.py` | Unit | Pattern matching helpers |
| `tools/extensions_metadata.py` | `test_extensions_metadata.py`, `test_mcp_extensions_integration.py` | Unit + Integration | YAML metadata override transform |
| `tools/filter_info.py` | `test_filter_info.py` | Unit | Filter prediction, middleware |
| `tools/labels.py` | `test_tool_labels.py`, `test_label_validation.py` | Unit | Schema-time label conversion |
| `tools/label_transform.py` | `test_label_transform.py` | Unit | Runtime label transform (innermost transform) |
| `tools/mcp_tools.py` | `test_mcp_tools.py`, `test_mcp_tools_wrapping.py` | Unit | Resource access tools, wrapping |
| `tools/namespace.py` | `test_tool_namespace.py` | Unit | Prefix/suffix namespace transform |
| `tools/resource_display.py` | `test_resources.py`, `test_mcp_tools.py` | Unit | No dedicated test file; tested indirectly |
| `tools/schemas.py` | `test_tool_schemas.py` | Unit | Output schema derivation, `$ref` resolution |
| `tools/search.py` | `test_tool_search.py` | Unit | Synthetic tools, lazy loading, BM25 search integration |
| `tools/tool_display.py` | `test_tool_display.py` | Unit | Tool result formatting entry point |
| `tools/type_info.py` | `test_type_info.py` | Unit | Type resolution and cross-reference tracking |
| `tools/unified_search.py` | `test_unified_search.py` | Unit | 90% target; merged search across tools/docs/resources |
| `tools/virtual_params.py` | `test_virtual_params.py` | Unit | Virtual parameter lifecycle (sudo, fetch_all) |

### Naming Conventions

- **Unit test files**: `test_<module_name>.py` for flat modules
  (e.g., `client.py` → `test_client.py`).
  For subpackage modules: `test_<abbrev>_<module>.py`
  (e.g., `tools/search.py` → `test_tool_search.py`).
  For subcomponent classes within a module: `test_<parent>_<component>.py`
  (e.g., `client.GiteaAPI` → `test_client_gitea_api.py`).
- **Integration test files**: `test_<feature>.py`
  (e.g., `test_lazy_loading.py`, `test_cache_invalidation.py`).
- **Cross-cutting / behavioral test files**: `test_<behavior>.py`
  (e.g., `test_mcp_tools_wrapping.py`, `test_label_validation.py`).
- **Regression test files**: `test_regression_<issue_num>_<description>.py`
  (e.g., `test_regression_316_dotfile_paths.py`).
- **Test classes**: `Test<ComponentName>` (PascalCase).
- **Test methods**: `test_<behavior_description>` (snake_case).
- **Test fixtures**: Descriptive names, preferably noun-based.
- **New test files**: Follow the existing convention for the module area.
  When in doubt, refer to the source-to-test mapping table above.
- **Pragmatic deviations**: Strict convention is preferred but not rigidly
  enforced. A name that departs from the rule is acceptable when it provides
  better disambiguation or clarity (e.g., ``search.py`` → ``test_search_bm25.py``
  to distinguish from ``tools/search.py`` tests). Every deviation must be
  documented with a brief rationale in the source-to-test mapping table.

## Test Layering

This project has four distinct test zones. Each demands a different approach.

### Zone 1: Schema Transformation (openapi_converter)

**What it tests**: Swagger 2.0 → OpenAPI 3.1 conversion, `$ref` resolution, response schema wrapping, content-type handling.

**Pattern**: Pure function tests. Feed a dict in, assert dict structure out. No mocking needed.

**Coverage target**: 95%+.

```python
def test_array_response_wrapped_in_result(self):
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "basePath": "/api",
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {"type": "array", "items": {"type": "object"}},
                        }
                    }
                }
            }
        },
    }
    result = convert_swagger_to_openapi_v3(spec)
    schema = result["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["type"] == "object"
    assert "result" in schema["properties"]
    assert schema["properties"]["result"]["type"] == "array"
```

### Zone 2: Customization Stack (tools/)

**What it tests**: The pipeline: annotations, error handling, labels, validation, caching, filtering, exclusions, search, namespace, examples, schemas.

**Pattern**: Compose a mock tool with a known spec, apply one transform at a time, assert the tool's metadata changed correctly. For runtime behavior, wrap a mock `run` function and assert it's called (or not) with the right arguments.

**Coverage target**: 90%+ for individual transforms, 80%+ for composition/integration.

```python
def test_customize_metadata_sets_labels(self):
    route = MagicMock(path="/repos/{owner}/{repo}/issues", summary="Create issue", operation_id="create_issue", method="POST")
    tool = MagicMock(spec=OpenAPITool)
    tool.name = "issue_create_issue"
    tool.annotations = None
    tool.tags = set()
    tool.parameters = {"properties": {"labels": {"type": "array", "items": {"type": "integer"}}}}
    tool.output_schema = None
    tool.description = "Create an issue"
    tool.meta = {}
    _customize_metadata(route, tool, openapi_spec={})
    assert "Available labels" in tool.description
```

### Zone 3: Resource System (resources/)

**What it tests**: Auto-generated resource registration, custom resource formatters, scope derivation, resource registration operations, Markdown formatting.

**Pattern**: Unit-test formatters with known input dicts. Verify registration by inspecting `mcp.resource()` call args. Test error paths (404, missing fields) with controlled inputs.

**Coverage target**: 85%+.

### Zone 4: Server Wiring (server_setup/, server.py)

**What it tests**: Composition of all components — spec loading, provider creation, resource setup, permission wiring, logging config.

**Pattern**: Integration tests using `respx` to mock the Gitea API and `FastMCP` in-memory transport. Create a full server, inspect its tool/resource listings, call tools and assert results.

**Coverage target**: 70%+ (wiring is inherently harder to unit test, and changes less frequently).

```python
async def test_server_creates_tools_from_spec(self):
    server = await create_mcp_server(config=SimpleConfig())
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]
    assert "gitea_issue_list_issues" in tool_names
```

### Zone 5: Live End-to-End (tests/live/)

**What it tests**: The full production path — real Gitea instance, real MCP server
binary over stdio, real MCP transport. Exercises transport-level output
validation that in-memory ``server.call_tool()`` bypasses.

**Pattern**: Tests in ``tests/live/`` connect to a real Gitea/Forgejo instance,
launch the MCP server binary over stdio, and call tools through
``fastmcp.Client`` or the raw MCP SDK. Test data (user, repo, branch, PR) is
created via the admin API and cleaned up on teardown.

**Prerequisite**: A running Gitea instance with credentials in
``.env.dev.local`` (written by ``gitea_dev_start.sh``).

**Skip behaviour**: Tests are marked with ``@pytest.mark.live`` and skip
gracefully when no Gitea instance is reachable (checked at collection time).

```bash
# Requires .env.dev.local from gitea_dev_start.sh
uv run pytest tests/live/ -v

# Automatically skips when Gitea is not running
uv run pytest tests/live/ -v    # → 3 skipped
```

**Coverage target**: Not enforced (requires external service).

## Testing Frameworks and Tools

- **pytest**: Primary test runner
- **pytest-asyncio**: Async test support (`asyncio_mode = "auto"`)
- **pytest-mock**: Mocking via `mocker` fixture
- **pytest-cov**: Coverage measurement
- **pytest-xdist**: Parallel test execution (`-n auto --dist loadscope` enabled by default)
- **respx**: HTTP request mocking for `httpx.AsyncClient`
- **jsonschema**: Schema validation for OpenAPI 3.1 output

## What to Test (Per Layer)

### Schema Transformation

Write focused dict-in/dict-out tests. Cover:
- Each type of response schema (object, array, primitive, $ref)
- Each content type (application/json, text/plain, multipart)
- Each parameter type (path, query, body, header)
- Edge cases: empty spec, 204 No Content, missing produces, invalid swagger version
- The `_wrap_success_response_schemas` function independently (not just through convert)
- The `tests/swagger.v1.json` subset for a real-world end-to-end conversion + schema validation
- OperationId normalization (strips `repo`, `org`, `user` prefixes)

### Tool Customization (tools/)

For each transform in the pipeline:
- **customize.py**: Title inference, categorization, description hints
- **errors.py**: HTTP status → user-friendly error message mapping
- **exclusion.py**: Pattern matching (exact name, glob, tag: prefix), include overrides exclude, prefix-aware matching for prefixed/unprefixed names, filtering for tools/resources/templates, config file loading (missing, empty, malformed YAML)
- **labels.py**: String "bug" → integer 1 conversion, guidance text injection
- **validation.py**: Each validator with valid + invalid inputs (use `parametrize`)
- **cache_invalidation.py**: URI pattern computation for each tool type
- **spec_loader.py**: Spec-level filtering — excluded-routes computation (deprecated + scope + config-excluded) from the spec
- **search.py**: Indexing, ranking, lazy loading, synthetic tool output schema
- **namespace.py**: Prefix application (verify `gitea_` prefix), resource passthrough
- **examples.py**: Schema → example generation for all types (arrays, objects, enums, type lists, anyOf)
- **schemas.py**: `$ref` resolution, output schema derivation, array response detection
- **type_info.py**: Type index building, `$ref` type name resolution, cross-reference tracking

### Resources

- Auto-generation: correct URIs, proper docstrings, skip non-GET endpoints
- Custom resources: error handling (404, missing fields, API errors), Markdown formatting
- Registry: CRUD operations for resource metadata
- Scopes: correct mapping from HTTP method + tag → required scope
- Resource handler decorator: test that `@resource_handler` wraps errors correctly,
  formats `resource_id` and `error_message` from function parameters, and re-raises
  non-404 exceptions transparently

### Server Setup / Wiring

- Server creation succeeds with valid config
- Tools and resources are registered
- Lazy loading reduces visible tool count
- Search works before and after lazy load
- HTTP transport serves health endpoint, MCP endpoint, CORS headers
- YAML extensions propagate to tool annotations
- Permission filtering hides/shows tools based on token

## How to Test Patterns

### Testing a Transformation Chain

Don't re-test the whole pipeline in every test. Test each transform independently, then add a small number of integration tests for the composition.

```python
# Unit test for one transform
def test_adds_category_from_tag(self):
    tool = MockTool(operation_id="issue_list", tags=["Issue"])
    result = categorize_tool(tool)
    assert result.category == "issue"

# Integration test for composition — one test, not one per transform
async def test_full_customization_pipeline(self):
    route = MagicMock(path="/repos/{owner}/{repo}/issues", summary="List issues", operation_id="list_issues", method="GET")
    tool = MagicMock(spec=OpenAPITool)
    tool.name = "issue_list_issues"
    tool.annotations = None
    tool.tags = set()
    tool.parameters = {"properties": {}}
    tool.output_schema = None
    tool.description = ""
    tool.meta = {}
    _customize_metadata(route, tool, openapi_spec={})
    assert tool.name == "issue_list_issues"  # actually "gitea_issue_list_issues" after namespace
    assert "issue" in tool.tags
```

### Testing Runtime Behavior

When testing that a transform affects runtime behavior (not just metadata), wrap the tool's `run` method:

```python
async def test_validation_rejects_bad_input(self):
    original_run = AsyncMock(return_value={"result": "ok"})
    tool = Tool.from_tool(base_tool, transform_fn=lambda t: setattr(t, '_run', original_run))
    with pytest.raises(ValidationError):
        await tool.run(owner="", repo="bad/name")
    original_run.assert_not_called()
```

### Testing with respx

Always scope mocks with context managers. Never leak mocked routes between tests.

```python
async def test_fetch_user(self, config):
    async with respx.mock:
        route = respx.get("https://git.example.com/api/v1/user").respond(200, json={"login": "test"})
        client = GiteaClient(config)
        result = await client.request("GET", "/user")
        assert result["login"] == "test"
        assert route.called
```

### Testing with Server In-Memory

Use `server.call_tool()` and `server.list_tools()` directly for full round-trips without stdio.

```python
async def test_tool_call_round_trip(self, mcp_server):
    result = await mcp_server.call_tool("gitea_issue_list_issues", {"owner": "o", "repo": "r"})
    assert result[0].text
```

### Testing the `resource_handler` Decorator

The `@resource_handler(resource_type, id_format, error_message)` decorator wraps
custom resource functions to eliminate the 10× try/except pattern. Test three
scenarios:

1. **Success path**: function returns normally, decorator passes through
2. **404 error**: `_handle_not_found` converts it to `ResourceError` with correct fields
3. **Non-404 error**: decorator re-raises the original exception

The `id_format` and `error_message` templates use `str.format()` with the
function's parameters (both positional and keyword). The decorator resolves
parameter names via `inspect.signature`.

```python
async def test_success_path(self):
    @resource_handler("repo", "{owner}/{repo}", "Not found: {owner}/{repo}")
    async def my_resource(owner: str, repo: str):
        return f"OK: {owner}/{repo}"

    result = await my_resource("user", "my-repo")
    assert result == "OK: user/my-repo"

async def test_404_converted(self, config):
    client = GiteaClient(config)
    @resource_handler("repo", "{owner}/{repo}", "Repository '{owner}/{repo}' not found.")
    async def my_resource(owner: str, repo: str, gitea_client: GiteaClient):
        return await gitea_client.request("GET", f"/repos/{owner}/{repo}")

    async with respx.mock:
        respx.get("https://git.example.com/api/v1/repos/user/missing").respond(404)
        with pytest.raises(ResourceError) as exc:
            await my_resource("user", "missing", client)
        assert exc.value.data["resource_type"] == "repo"

async def test_non_404_re_raised(self):
    @resource_handler("repo", "{owner}/{repo}", "Repository '{owner}/{repo}' not found.")
    async def my_resource(owner: str, repo: str):
        raise ValueError("something else")

    with pytest.raises(ValueError, match="something else"):
        await my_resource("user", "my-repo")
```


### Testing MCP Tool Call Results

Tool results come back as lists of `ToolResult` or `TextContent`. Test both the text content and the structure.

```python
result = await mcp.call_tool("gitea_issue_list_issues", {"owner": "o", "repo": "r"})
text_content = result[0]
import json
data = json.loads(text_content.text)
assert data["result"]  # always wrapped in result
```

## Fixture Patterns

### Shared Fixtures & Helpers

Put truly shared fixtures in `tests/conftest.py`:

- `SimpleConfig` — canonical test config stub
- `swagger_spec_fixture` — loads `tests/swagger.v1.json` for tests that need a real spec
- `event_loop` — session-scoped default event loop
- `trace_exporter` — OpenTelemetry InMemorySpanExporter (cleared between tests)
- `temp_workspace` — temporary workspace directory for file-based tests

Put domain-specific helper functions in ``tests/helpers/``:

- `tests/helpers/mock_tool.py` — `make_mock_tool`, `make_mock_route`
- `tests/helpers/tool_names.py` — `extract_tool_names`
- `tests/helpers/spec_fixtures.py` — `base_spec`, `minimal_spec`

### Module-Level Fixtures

Keep fixtures close to where they're used. Define them in the test class or file, not in conftest, unless ≥3 files use them.

### The SimpleConfig Pattern

The canonical `SimpleConfig` lives in `tests/conftest.py` and supports all config fields.
Import it in tests that need a standard config. If a test file needs unique defaults
(e.g., HTTP transport tests), pass keyword arguments at construction time.

```python
# Good — canonical fixture from conftest.py
@pytest.fixture
def simple_config():
    return SimpleConfig(url="https://git.example.com", token="test_token")
```

### Inline Data Fixtures

For small, test-specific data, define it inline in the test method. Don't extract shared fixtures for data used once.

```python
def test_converts_minimal_spec(self):
    spec = {
        "swagger": "2.0",
        "info": {"title": "Test", "version": "1"},
        "basePath": "/api",
        "paths": {},
    }
    ...
```

### Async Fixtures

Async fixtures for integration tests:

```python
@pytest.fixture
async def mcp_server():
    server = await create_mcp_server(config=SimpleConfig())
    yield server
```

## Mocking Guidelines

### Good

- Mock external HTTP calls with `respx` (scoped, not leaked)
- Mock the Gitea API, not the httpx transport layer
- Use `AsyncMock` for async methods, `MagicMock` for sync
- Set explicit `return_value` or `side_effect` on every mock
- Verify calls when interaction matters: `mock.assert_called_once_with(...)`

### Mocking GiteaClient

When mocking `GiteaClient` in tests, always set both the `_config` attribute and
the public `config` property (the real class has a `@property` that returns `_config`):

```python
# Good
AsyncMock(
    _config=config,
    config=config,       # public property needed by create_mcp_server et al.
    request=AsyncMock(return_value={}),
    close=AsyncMock(),
)
```

### Bad

- Mocking internals of the module under test
- Leaking mock state between tests (always scope `respx` with context manager)
- Over-mocking: if you mock everything, you're testing your mocks, not your code
- Shared mutable fixtures — fixtures should be fresh for each test
- Using `respx` without a context manager in async tests
- Forgetting to set `config=config` when mocking GiteaClient — the mock won't have
  the public `config` property, causing `AttributeError` or returning a stray AsyncMock

```python
# Good — isolated context (use when tests don't need module-level respx.get())
async with respx.mock() as mock:
    mock.get(...).respond(200, json={})
    result = await client.request(...)

# Also good — start/stop with try/finally (use when tests rely on module-level
# respx.get(), which delegates to a global MockRouter singleton)
respx.start()
try:
    respx.get(...).respond(200, json={})
    result = await client.request(...)
finally:
    respx.stop(clear=True, reset=True)

# Bad — no cleanup at all
respx_mock = respx.mock()
respx_mock.get(...).respond(...)
# Forgot stop() — routes leak to next test
```

## Anti-Patterns / Red Flags

These fail code review. Don't do them.

| Anti-pattern | Why it's bad |
|---|---|
| Testing with the full 4.6 MB `swagger.v1.json` | Slow, fragile, drowns assertion output. Use the 68 KB `tests/swagger.v1.json` subset instead. |
| Deep dict comparison of full conversion output | Brittle — breaks on any schema change. Assert specific keys/paths only. |
| Testing internals instead of behavior | Testing `_wrap_success_response_schemas` directly is fine. Testing `_private_helper` that's an implementation detail is not. Public API changes slower. |
| Shared mutable fixtures | If one test mutates a fixture, other tests see it. Use factory fixtures or `copy.deepcopy`. |
| Copy-pasting `SimpleConfig` | Was duplicated in 4 files; now consolidated to canonical `tests/conftest.py`. All test files import from conftest — don't reintroduce copies. |
| Skipped tests without explanation | Use `pytest.mark.skip(reason="...")`, not bare `pytest.skip()`. Always document why. |
| `asyncio_mode = "auto"` without await | If a test is `async def` but forgets `await`, it passes trivially. Always await async calls. |
| Tests that import from other test files | Each test file should be independently runnable. No shared import chains between test files. |
| `time.sleep()` in tests | Use `asyncio.sleep()` + `pytest-asyncio`, or mock the timer. Never block the event loop. |
| Assertions without messages | `assert result, "expected result to be truthy"` is easier to debug than bare `assert result`. |

## Coverage Configuration

Coverage is configured in `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["gitea_mcp_server"]
omit = ["*/migrations/*", "*/tests/*"]

[tool.coverage.report]
fail_under = 85
show_missing = true
skip_covered = false
exclude_also = [
    "if TYPE_CHECKING:",
    "@(abc\\.)?abstractmethod"
]
```

### Minimum Coverage by Module Area

| Area | Minimum | Notes |
|------|---------|-------|
| `openapi_converter.py` | 95% | Core schema transformation — all branches matter |
| `tools/exclusion.py` | 90% | Exclusion pattern matching (consumed by spec-level filtering); config loading moved to `spec_loader.py` |
| `validation.py` | 95% | Security-sensitive input validation |
| `resources/*.py` (each) | 85% | Formatters, resource registration, scope derivation |
| `resources/custom.py` | 80% | Error paths matter; some formatting is visual |
| `exceptions.py` | 100% | Trivial — exception classes only |
| `constants.py` | 100% | Constants only — values are coverage |
| `tools/unified_search.py` | 90% | Name-match + BM25 merging logic |
| `config.py` | 95% | Configuration parsing |
| `client.py` | 90% | HTTP client with retry logic |
| `server.py` | 70% | Wiring — integration-tested, not unit-friendly |
| `server_setup/*.py` (each) | 70% | Startup orchestration |
| `logging_config.py` | 80% | Formatting + redaction |
| `format.py` | 85% | Shared markdown formatters |

### Running Coverage

```bash
# Run tests with coverage
uv run pytest --cov=gitea_mcp_server

# Show missing lines
uv run pytest --cov=gitea_mcp_server --cov-report=term-missing

# Generate HTML report
uv run pytest --cov=gitea_mcp_server --cov-report=html
```

## Assertion Best Practices

### General Principles

1. **Be specific**: Use exact equality checks or precise assertions
2. **Test one behavior per test**: Each test should validate one specific outcome
3. **Use appropriate assertions**:
   - `assert value == expected` for equality
   - `assert in` for membership
   - `pytest.raises()` for exceptions
   - `assert isinstance(obj, Class)` for type checking

### Examples

```python
# Good
assert result["id"] == 42
assert len(items) == 3
assert "admin" in tool.tags

# Better — with descriptive messages
assert result["id"] == 42, f"Expected ID 42, got {result['id']}"
assert len(items) == 3, f"Expected 3 items, got {len(items)}"
```

### Validation Testing

When testing validation logic (e.g., in `validation.py`):

- **Test each validator** with both valid and invalid inputs. Use `pytest.mark.parametrize` to cover many cases.
- **Test schema augmentation**: verify that the tool's JSON schema gets the expected constraints (`minLength`, `maxLength`, `pattern`, `enum`, etc.).
- **Test runtime wrapper**: wrap a mock tool, call with invalid arguments, and assert `ValidationError` is raised *before* the tool's `run` method executes. Use `AsyncMock` for the original run.
- **Coverage**: Aim for >95% coverage of validation modules.

```python
@pytest.mark.parametrize("owner,repo,should_pass", [
    ("valid-owner", "valid-repo", True),
    ("", "repo", False),
    ("owner", "", False),
    ("a" * 256, "repo", False),
    ("owner", "../repo", False),
])
def test_validate_owner_repo(owner, repo, should_pass):
    if should_pass:
        validate_owner_repo(owner, repo)  # should not raise
    else:
        with pytest.raises(ValidationError):
            validate_owner_repo(owner, repo)
```

## Integration Testing

Integration tests are placed in `tests/integration/` and test real interactions between components.

- Use `respx` to mock the Gitea API (never make real HTTP calls)
- Use `SimpleConfig` from conftest (or define once per file if unique defaults needed)
- Use `create_mcp_server()` to build a full server instance
- Use in-memory inspection (`server.list_tools()`, `server.call_tool()`) rather than stdio
- For HTTP transport tests, use `uvicorn` with a temporary port (see `test_http_transport_server.py`)
- Clean up resources in teardown (close clients, stop servers)
- Each integration test file should be independently runnable

```python
async def test_tool_call_via_server(self):
    config = SimpleConfig(url="https://git.example.com", token="test_token")
    async with respx.mock:
        respx.get("https://git.example.com/api/v1/repos/owner/repo").respond(200, json={"name": "repo"})
        server = await create_mcp_server(config=config)
        result = await server.call_tool("gitea_repo_get", {"owner": "owner", "repo": "repo"})
        assert len(result) > 0
```

## Test Data and Fixtures

- Use pytest fixtures for reusable test data
- Keep widely-shared fixtures in `tests/conftest.py`
- Keep module-specific fixtures in the test module or class
- Use descriptive fixture names that indicate their purpose
- Fixtures should be idempotent and independent
- For spec-related tests, prefer inline dict fixtures over file loads (faster, self-contained)
- Use `tests/swagger.v1.json` only for end-to-end conversion + schema validation tests

```python
@pytest.fixture
def minimal_spec():
    """Return a minimal valid Swagger 2.0 spec."""
    return {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "basePath": "/api/v1",
        "paths": {},
    }
```

## Running Tests

```bash
# Run all tests (parallel by default: -n auto --dist loadscope)
uv run pytest

# Run sequentially (disable parallel workers for debugging)
uv run pytest -n 0

# Run with verbose output
uv run pytest -v

# Run specific test file (parallel still active; single file goes to one worker)
uv run pytest tests/unit/test_client.py

# Run specific test by name
uv run pytest -k "test_async_operation"

# Run with coverage
uv run pytest --cov=gitea_mcp_server

# Stop on first failure (kills all workers)
uv run pytest -x

# Run a specific module area
uv run pytest tests/unit/openapi_converter/
uv run pytest tests/integration/
```

### Parallel Execution with pytest-xdist

The test suite runs with ``-n auto --dist loadscope`` by default (configured
in ``pyproject.toml``). This distributes tests across CPU cores while keeping
session-scoped fixtures in one worker per module:

- ``--dist loadscope`` groups tests by module scope, so session-scoped
  fixtures (HTTP server, OTel exporter) are created once per worker instead
  of once per test.
- Each worker gets its own event loop via the session-scoped
  ``event_loop`` fixture in ``tests/conftest.py``.
- To debug a single file without the parallel overhead, pass ``-n 0``:
  ``uv run pytest tests/unit/test_foo.py -xvs -n 0``.

Design notes for session-scoped fixtures under xdist:

- **Avoid inter-worker state**: Session fixtures must not depend on state
  shared across workers (files, ports, global singletons). The ``http_port=0``
  pattern (OS-assigned port) keeps each worker's HTTP server isolated.
- **Avoid module-level imports** with side effects that execute at import
  time — each worker re-imports the test modules.
- **``asyncio_default_fixture_loop_scope = "session"``**: This setting in
  ``pyproject.toml`` matches the session-scoped ``event_loop`` fixture in
  ``tests/conftest.py``. Without it, xdist workers would each create
  per-function event loops, defeating the session-scoped loop. Async
  fixtures that need a per-function loop should set
  ``loop_scope="function"`` explicitly.

### Timeout Safety

The suite uses ``pytest-timeout`` with ``--timeout=120 --timeout_method=thread``
(configured in ``pyproject.toml``). Any test hanging longer than 2 minutes is
killed automatically, preventing a single stuck test from blocking the whole
run (especially important with xdist workers).

Use ``@pytest.mark.timeout(N)`` to override per-test — shorter for
known-fast tests, longer for slow ones. The ``thread`` method is compatible
with xdist and asyncio (``fork`` would break the event loop).

```python
# Override timeout for a specific test
@pytest.mark.timeout(30)
def test_slow_operation(self):
    ...

# Disable timeout entirely (use sparingly)
@pytest.mark.timeout(None)
def test_unbounded(self):
    ...
```

### Test Markers

| Marker | Description | Fast iteration |
|--------|-------------|----------------|
| `live` | Requires a real Gitea/Forgejo instance (skipped if unreachable). Defined in `tests/live/conftest.py`. | — |
| `slow` | Tests taking >1s (HTTP server startup, retry timeouts). Skip with `-m 'not slow'`. | `uv run pytest -m 'not slow'` |

```bash
# Fast iteration — skip HTTP server startup and retry timeout tests
uv run pytest -m 'not slow'

# Run only slow tests (e.g. before CI)
uv run pytest -m slow
```

## Quality Standards

- **All tests must be deterministic**: No random behavior without fixed seeds
- **Tests should be isolated**: No shared state between tests, no order dependencies
- **Tests should be fast**: Aim for <100ms per test on average, <50ms for unit tests
- **No external dependencies**: Tests should not require network access or external services (except test fixtures committed to the repo)
- **Proper cleanup**: Use fixtures with proper setup/teardown. Context managers for `respx`, clients, servers.
- **No skipped tests**: A skipped test is either dead code (delete it) or a deferred bug (fix it). Exception: platform-specific tests that genuinely cannot run on certain OSes.
- **No test file imports another test file**: Each test file must be independently runnable.
- **Conditional skips must use `@pytest.mark.skipif`**: Not inline `pytest.skip()` in the test body — the skip must be visible at collection time.

## When Adding New Tests

1. Follow existing patterns in the codebase for the same module area
2. Add docstrings explaining what is being tested (one sentence is enough)
3. Ensure tests fail without the implementation (TDD approach)
4. Place tests in appropriate directory (unit vs integration):
   - **unit**: A single function/class, mocked dependencies
   - **integration**: Multiple components wired together, real server creation
5. Update this document if introducing new testing patterns
6. Don't copy-paste `SimpleConfig` — use or extend the canonical version
7. Run `uv run pytest` before pushing — 1219+ tests should all pass

## Test Code Linting

Test code must comply with the project's ruff lint rules. The
``[tool.ruff.lint.per-file-ignores]`` section in ``pyproject.toml`` documents
the few intentional exemptions (e.g. unused arguments in fixtures, bare asserts,
magic numbers in test data).

Enforcement:
- ``make test`` runs ``ruff check tests/`` before pytest
- CI runs a dedicated ``lint-tests`` job

## Coverage Enforcement

The project enforces a minimum coverage of 85% overall. This means:

- `pytest` with coverage will fail if total coverage falls below 85%
- New code should meet or exceed its module area's minimum (see table above)
- When fixing bugs, add regression tests first
- Coverage excludes: type checking blocks, abstract methods
- Module-level minimums are guidelines, not hard enforcement — but falling below them should be justified in the PR

## Related Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [respx](https://github.com/lundberg/respx)
- [pytest-mock](https://pytest-mock.readthedocs.io/)
- [FastMCP documentation](https://gofastmcp.com/llms.txt)
- [OpenAPI Specification](https://spec.openapis.org/oas/v3.1.1)
