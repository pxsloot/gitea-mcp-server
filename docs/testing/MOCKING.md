---
audience: developer
type: reference
covers: Mocking rules — good/bad patterns, server-level policy, GiteaClient, mock helpers, result narrowing, respx patterns
---

# Mocking Guidelines

## Good

- Mock external HTTP calls with `respx` (scoped, not leaked)
- Mock the Gitea API, not the httpx transport layer
- Use `AsyncMock` for async methods, `MagicMock` for sync
- Set explicit `return_value` or `side_effect` on every mock
- Verify calls when interaction matters: `mock.assert_called_once_with(...)`

## Server-level mocking policy

For tests around `server.py` and `main_async()`, prefer the least invasive
mocking tier that still isolates the behavior under test:

1. **Real infrastructure first**: use `SimpleConfig` and a real
   `GiteaClient`; use `respx` when HTTP needs to be controlled.
2. **Patch specific behavior second**: patch only the operation being forced,
   such as `create_mcp_server` or `run_stdio_async`.
3. **Mock the complete dependency last**: mock classes such as
   `GiteaClient` only when the first two tiers cannot isolate the behavior.

Never use an unrestricted `MagicMock` for `Config`. Its chained attributes can
make tests pass while hiding changes to the configuration contract. The
`main_async` transport and shutdown tests in
`tests/integration/test_server.py` are the reference pattern: they return an
explicit `SimpleConfig`, construct the real client, and patch only server
creation plus the transport behavior under test.

## Mocking GiteaClient

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

## Bad

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

## Mock helpers for spec'd mocks

When you pass ``spec=SomeClass`` to ``AsyncMock`` or ``MagicMock``, mypy
narrows the mock to the spec type, losing access to mock attributes
(``.return_value``, ``.side_effect``, ``.assert_called_once_with``, etc.).
Use the helpers in ``tests/helpers/mock_tool.py`` to avoid this:

- **`make_async_mock(SomeClass)`** — returns an ``AsyncMock`` typed as
  ``AsyncMock`` (not the spec type).  Mock method attributes are fully
  accessible:
  ```python
  from tests.helpers.mock_tool import make_async_mock

  svc = make_async_mock(LabelService)
  svc.validate_and_convert.return_value = [1, 2]
  svc.validate_and_convert.assert_called_once_with(...)
  ```
- **`make_magic_mock(some_callable)`** — same for synchronous ``MagicMock``:
  ```python
  from tests.helpers.mock_tool import make_magic_mock

  fn = make_magic_mock(resolve_label_names)
  fn.return_value = [1, 2]
  fn.assert_called_once_with(...)
  ```

Always prefer these over bare ``AsyncMock(spec=X)`` / ``MagicMock(spec=X)``
when the spec's type is a class or callable whose mock attributes you need
to access.  Both helpers are tested in ``tests/unit/test_mock_helpers.py``.

## Result narrowing helpers

When testing MCP tool calls, the return types are unions (``TextContent |
ImageContent | ...``, ``dict[str, Any] | None``).  Use the helpers in
``tests/helpers/mcp_results.py`` to narrow these unions and avoid mypy
``union-attr`` errors.  See the "Testing MCP Tool Call Results" section
in `testing/ZONES.md` for usage and ``tests/unit/test_mcp_results_helpers.py``
for tests.
