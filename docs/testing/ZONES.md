---
audience: developer
type: reference
covers: Five test zones with patterns, examples, coverage targets — plus testing frameworks, what to test, and how-to patterns
---

# Test Zones and Patterns

## Test Layering

This project has five distinct test zones. Each demands a different approach.

### Zone 1: Schema Transformation (openapi_converter)

**What it tests**: Swagger 2.0 → OpenAPI 3.1 conversion, `$ref` resolution, response schema wrapping, content-type handling.

**Pattern**: Pure function tests. Feed a dict in, assert dict structure out. No mocking needed.

**Coverage target**: 95%+.

```python
from gitea_mcp_server.openapi_types import SwaggerV2Spec


def test_array_response_wrapped_in_result(self):
    spec: SwaggerV2Spec = {
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

See `testing/LIVE.md` for the full live test architecture, design decisions, and how-to guide.

---

## Testing Frameworks and Tools

- **pytest**: Primary test runner
- **pytest-asyncio**: Async test support (`asyncio_mode = "auto"`)
- **pytest-mock**: Mocking via `mocker` fixture
- **pytest-cov**: Coverage measurement
- **pytest-xdist**: Parallel test execution. Live tests are sequential within
  each worker and isolated across workers.
- **respx**: HTTP request mocking for `httpx.AsyncClient`
- **jsonschema**: Schema validation for OpenAPI 3.1 output
- **hypothesis**: Property-based testing for converter invariants — ``$ref`` resolution, vendor-extension stripping, response wrapping, round-trip completeness, parameter preservation, and crash-safety on malformed input

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
- **labels.py**: String "bug" → integer 1 conversion, schema augmentation
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
- Resource handler error handling: 404 → `ResourceError`, validation errors,
  unexpected exception passthrough

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

#### Verifying fail-open stubs in fixture teardown

When a fixture stubs API calls that are **fail-open** in the SUT (calls
where the SUT silently falls back to defaults on a non-intercepted request),
capture the route objects and verify ``.called`` in the fixture teardown:

```python
@pytest.fixture(autouse=True)
def stub_startup_calls(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setattr(...)
    respx.start()
    r_user = respx.get("https://git.example.com/api/v1/user").respond(
        200, json={"login": "test"},
    )
    r_version = respx.get("https://git.example.com/api/v1/version").respond(
        200, json={"version": "1.0.0"},
    )
    try:
        yield
        assert r_user.called, (
            "GET /api/v1/user was not called — respx route did not intercept. "
            "The SUT would silently use fallback values and the test would pass."
        )
        assert r_version.called, (
            "GET /api/v1/version was not called — respx route did not intercept. "
            "The SUT would silently use fallback values and the test would pass."
        )
    finally:
        respx.stop(clear=True, reset=True)
```

This catches URL mismatches, environment changes, or respx start/stop
refactors that break interception without failing the test.  Verify
**only** fail-open stubs — stubs whose data is asserted in test bodies
are already self-verifying.

### Testing with Server In-Memory

Use `server.call_tool()` and `server.list_tools()` directly for full round-trips without stdio.

```python
async def test_tool_call_round_trip(self, mcp_server):
    result = await mcp_server.call_tool("gitea_issue_list_issues", {"owner": "o", "repo": "r"})
    from tests.helpers.mcp_results import extract_text_content
    assert extract_text_content(result.content)
```

### Testing Error Handling in Resource Handlers

Resource endpoint functions are registered via
``_register_endpoint_resource`` and its helpers in
``resources/factory.py``. Error handling (404, validation,
unexpected exceptions) is baked into the handler loop — see
``test_resource_factory.py`` for coverage of all error paths:

- ``test_handler_returns_json_resource_result_for_dict_response``
- ``test_404_raises_resource_error_not_found``
- ``test_non_404_api_error_raises_api_error``
- ``test_unexpected_exception_raises_internal_error``


### Testing MCP Tool Call Results

Tool results come back as ``ToolResult`` with a ``.content`` list of
``TextContent | ImageContent | ...``.  Use the helpers in
``tests/helpers/mcp_results.py`` to narrow the unions and avoid mypy
``union-attr`` errors:

```python
from tests.helpers.mcp_results import extract_text_content, parse_json_content

result = await mcp.call_tool("gitea_issue_list_issues", {"owner": "o", "repo": "r"})

# Extract text from the first content item
text = extract_text_content(result.content)

# Parse JSON content
data = parse_json_content(result)
assert data["result"]  # always wrapped in result
```

### Property-Based Testing (hypothesis)

Some invariants are better expressed as properties than as example-based tests.
The converter (``openapi_converter/core.py``) is a pure function — ideal for
hypothesis-driven property tests.

**When to use**: Pure or nearly-pure transformation functions where you can
express invariants that must hold for all inputs (e.g., "no ``$ref`` is ever
left unresolved", "all JSON responses are wrapped in ``result``").

**Pattern** — ``tests/unit/openapi_converter/test_converter_properties.py``:

```python
from hypothesis import assume, given, strategies as st

@given(schema=swagger_schema(max_depth=2))
def test_every_json_200_response_wrapped(self, schema):
    """Every 200 with application/json must have a result wrapper."""
    assume(isinstance(schema, dict))
    spec = _make_spec(paths={"/r": {"get": {
        "operationId": "get",
        "responses": {"200": {"description": "OK", "schema": schema}},
    }}})
    result = convert_swagger_to_openapi_v3(spec)
    resp_schema = result["paths"]["/r"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert _has_result_wrapper(resp_schema)
```

**Strategies**: Build minimal specs from focused ``@st.composite`` strategies
rather than trying to model the full Swagger spec.  Each invariant gets its
own targeted strategy (schema generation, ``$ref`` generation, vendor-extension
generation).

**Guidelines**:
- Use ``assume()`` to filter out invalid combinations, not ``if/continue``
- Keep ``max_depth`` small (2–3) to avoid exponential blowup in nested strategies
- Add a deterministic regression test alongside the property test for known edge cases
- Mark with ``@pytest.mark.slow`` if the hypothesis test takes >1s to find counterexamples
