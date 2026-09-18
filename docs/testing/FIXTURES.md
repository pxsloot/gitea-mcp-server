---
audience: developer
type: reference
covers: Fixture patterns — shared fixtures, typed OpenAPI spec fixtures, SimpleConfig, module-level, inline, async
---

# Fixture Patterns

## Shared Fixtures & Helpers

Put truly shared fixtures in `tests/conftest.py`:

- `SimpleConfig` — canonical test config stub
- `swagger_spec_fixture` — loads `tests/swagger.v1.json` for tests that need a real spec
- `event_loop` — session-scoped default event loop
- `trace_exporter` — OpenTelemetry InMemorySpanExporter (cleared between tests)
- `temp_workspace` — temporary workspace directory for file-based tests
- `isolate_from_project_dotenv` — autouse fixture that changes CWD to ``tmp_path``
  and resets ``Config._instance`` before every test, preventing the project's
  ``.env`` file from leaking into test configs.  Applies suite-wide; no test file
  needs its own copy.

Put domain-specific helper functions in ``tests/helpers/``.
**Always use these helpers when the pattern fits** — they eliminate common
mypy errors and keep test code consistent:

- `tests/helpers/mock_tool.py` — `make_mock_tool`, `make_mock_route`,
  `make_async_mock`, `make_magic_mock`
- `tests/helpers/tool_names.py` — `extract_tool_names`
- `tests/helpers/spec_fixtures.py` — `base_spec`, `minimal_spec`, `make_openapi_spec`
- `tests/helpers/mcp_results.py` — `extract_text_content`, `assert_call_success`,
  `get_structured`, `parse_json_content`, low-level MCP helpers

## Typed OpenAPI Spec Fixtures

Production functions accept ``OpenAPISpec`` (a TypedDict).  Test code that
passes plain ``dict`` literals to these functions triggers ``arg-type`` mypy
errors.  The suite uses a two-tier strategy:

**Tier 1 — Factory helper**: ``make_openapi_spec(**overrides)`` in
``tests/helpers/spec_fixtures.py`` creates a post-conversion OpenAPI 3.1 spec
typed as ``OpenAPISpec``.  Use this for **every valid spec**, including
custom shapes — ``openapi``, ``info``, ``paths``, ``components``, and
``servers`` are accepted as keyword overrides and replace the factory
defaults:

```python
spec = make_openapi_spec()
result = some_function(openapi_spec=spec)

spec = make_openapi_spec(paths={"/ping": {"get": ...}})
spec = make_openapi_spec(components={"schemas": {"User": {...}}})
```

When a valid spec's *exact key set* matters (an empty spec, or a spec that
deliberately omits a defaulted key such as ``paths``), pass
``include_defaults=False`` and supply only the keys you want — still Tier 1,
no cast:

```python
spec = make_openapi_spec(include_defaults=False)
spec = make_openapi_spec(include_defaults=False, openapi="3.1.1", info={...})
```

**Tier 2 — ``cast()`` for deliberately malformed specs**: Tests that pass
malformed spec values to exercise error paths — strings where dicts are
expected, non-dict path items, unknown operation keys — must wrap in
``cast("OpenAPISpec", ...)`` so the deviation from the convention is
explicit:

```python
spec = cast("OpenAPISpec", {"paths": "not_a_dict"})
spec = cast("OpenAPISpec", {"paths": {0: "bad"}})
result = some_function(openapi_spec=spec)
```

``cast("OpenAPISpec", ...)`` also appears where a *computed* dict or variable
is narrowed to the type (e.g. ``cast("OpenAPISpec", dict(spec))``, or
widening a converted local).  That is a conversion, not spec construction,
and is outside this convention.

Two conventions apply project-wide:

- ``cast()`` always uses the **string form** ``cast("OpenAPISpec", ...)``
  (not bare ``cast(OpenAPISpec, ...)``) to satisfy ruff TC006.
- Imports of ``OpenAPISpec`` are placed in ``if TYPE_CHECKING:`` blocks
  when only needed for type annotations or string casts (see TC001).  Files
  that use ``OpenAPISpec`` exclusively in annotations add ``from __future__
  import annotations`` to make lazy strings.

No test may build a spec as an inline dict literal — annotated
(``spec: OpenAPISpec = {...}``) or passed as a keyword argument
(``fn(openapi_spec={...})``).  Both bypass the factory's single ``cast()``
and let the suite drift.  This is locked by
``tests/unit/test_spec_fixture_convention.py``, an AST guard that reports
``file:line`` for every violation.  Prefer Tier 1; use Tier 2 only when
testing deliberately malformed shapes.

## Module-Level Fixtures

Keep fixtures close to where they're used. Define them in the test class or file, not in conftest, unless ≥3 files use them.

## The SimpleConfig Pattern

The canonical `SimpleConfig` lives in `tests/conftest.py` and supports all config fields.
Import it in tests that need a standard config. If a test file needs unique defaults
(e.g., HTTP transport tests), pass keyword arguments at construction time.

`SimpleConfig` satisfies the ``ConfigProtocol`` defined in ``gitea_mcp_server/config.py``
via structural subtyping (PEP 544).  Production functions that accept ``ConfigProtocol``
accept ``SimpleConfig`` automatically — no import of the real ``Config`` or Pydantic
dependency is needed in test code.

```python
# Good — canonical fixture from conftest.py
@pytest.fixture
def simple_config():
    return SimpleConfig(url="https://git.example.com", token="test_token")
```

If a production function's signature changes from ``Config`` to ``ConfigProtocol``,
all existing test call sites continue to work without modification because
``SimpleConfig`` already satisfies the protocol structurally.

**Maintenance**: when adding or renaming a config field, update *both*
``Config`` (the Pydantic ``BaseSettings``) and ``ConfigProtocol`` (the
structural protocol) in lockstep — they must stay in sync.  ``SimpleConfig``
will then automatically conform without changes so long as its signature
already mirrors the protocol (it should — that is the whole point of the
pattern).

## Inline Data Fixtures

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

## Async Fixtures

Async fixtures for integration tests:

```python
@pytest.fixture
async def mcp_server():
    server = await create_mcp_server(config=SimpleConfig())
    yield server
```
