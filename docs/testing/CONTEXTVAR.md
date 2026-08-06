---
audience: developer
type: reference
covers: ContextVar lifecycle in tests — safety net, local cleanup, why it matters
---

# ContextVar Testing Patterns

The project uses module-level ``contextvars.ContextVar`` instances as
side channels between httpx event hooks and the tool wrapping pipeline:

- ``pagination_ctx`` (``gitea_mcp_server/pagination.py``): carries ``total_count``
  from Gitea's ``X-Total-Count`` response header.
- ``sudo_context`` (``gitea_mcp_server/tools/virtual_params.py``): carries the
  sudo username for admin operations.

This is an intentional design choice that avoids coupling to FastMCP internals,
but it means tests that simulate the event hook must handle ContextVar lifecycle.

## Safety Net

A suite-level autouse fixture in ``tests/conftest.py`` resets both ContextVars
before every test:

```python
@pytest.fixture(autouse=True)
def _reset_module_contexts():
    pagination_ctx.set({})
    sudo_context.set(None)
```

This provides a deterministic reset for every test regardless of sync/async
status, making the suite robust against future ``asyncio_default_test_loop_scope``
changes.

## Local Cleanup Pattern

Tests that set a ContextVar to a non-default value should wrap the test body
in ``try/finally`` for local robustness, even though the suite-level fixture
and ``asyncio_default_test_loop_scope = "function"`` both provide isolation:

```python
# Good — try/finally for local cleanup
pagination_ctx.set({"total_count": 42})
try:
    result = await some_function()
    assert result == expected
finally:
    pagination_ctx.set({})

# Also good — autouse fixture in a test class (scoped to the class)
@pytest.fixture(autouse=True)
def reset_pagination_ctx(self):
    pagination_ctx.set({})

# Avoid — inline cleanup after assertions (skipped on test failure)
pagination_ctx.set({"total_count": 42})
result = await some_function()
assert result == expected
pagination_ctx.set({})  # ❌ skipped if assert fails
```

## Why This Matters

With ``asyncio_default_test_loop_scope = "function"`` (current default), each
async test gets its own event loop, so ``contextvars.Context`` values never
leak between async tests.  The safety net and ``try/finally`` patterns exist
for:

1. **Sync tests** that share the main thread's context.
2. **Future loop-scope changes** — if the scope were widened to ``session``
   or ``module``, ContextVar leakage would reappear.
3. **Documenting intent** — the ``try/finally`` pattern makes the ContextVar
   lifecycle explicit at the point of use.
