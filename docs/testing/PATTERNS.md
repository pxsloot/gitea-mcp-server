---
audience: developer
type: reference
covers: How-to test patterns with code examples — what to test per layer, transformation chains, respx, in-memory server, MCP tool call results, property-based testing
---

# Test Patterns

## What this doc is NOT

This doc provides detailed how-to test patterns with code examples.  If you need:

| Topic | See |
|-------|-----|
| Definitions of the five test zones (layers) and coverage targets | `testing/ZONES.md` |
| Fixture patterns, SimpleConfig, typed OpenAPI spec fixtures | `testing/FIXTURES.md` |
| Mocking rules and server-level mocking policy | `testing/MOCKING.md` |
| Assertion best practices and validation testing | `testing/ASSERTIONS.md` |

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
