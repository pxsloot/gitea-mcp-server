---
audience: developer
type: reference
covers: Five test zones with their patterns, examples, and coverage targets — plus the testing frameworks used
---

# Test Zones

## What this doc is NOT

This doc defines the five test zones (layers) and lists the testing
frameworks used by the project.  If you need:

| Topic | See |
|-------|-----|
| Detailed how-to patterns and code examples for each layer | `testing/PATTERNS.md` |
| Fixture patterns, SimpleConfig, typed OpenAPI spec fixtures | `testing/FIXTURES.md` |
| Mocking rules and server-level mocking policy | `testing/MOCKING.md` |
| Live test architecture (Zone 5) | `testing/LIVE.md` |

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
