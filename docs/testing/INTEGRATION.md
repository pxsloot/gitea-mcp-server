---
audience: developer
type: how-to
covers: Integration testing patterns, running tests, markers, parallel/sequential design, timeout safety
---

# Integration Testing

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

## Running Tests

```bash
# Run all tests (sequential by default)
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/unit/test_client.py

# Run specific test by name
uv run pytest -k "test_async_operation"

# Run with coverage
uv run pytest --cov=gitea_mcp_server

# Stop on first failure
uv run pytest -x

# Run a specific module area
uv run pytest tests/unit/openapi_converter/
uv run pytest tests/integration/

# Live tests (require Gitea instance)
uv run pytest tests/live/

# Live tests via Makefile (same as above plus 300s timeout)
make test-live
```

### Parallel and sequential design

Unit and integration tests are safe to distribute freely. Live tests share
one pooled World per worker and therefore execute sequentially within that
worker; run/worker-specific identities isolate workers from one another.

To run the full suite with parallel workers:

```bash
uv run pytest -n auto
```

For a live-only run with an explicit run namespace:

```bash
GITEA_LIVE_RUN_ID="ci-${CI_PIPELINE_ID:-local}" uv run pytest -n auto -m live
```

### Timeout Safety

The suite uses ``pytest-timeout`` with ``--timeout=120 --timeout_method=thread``
(configured in ``pyproject.toml``). Any test hanging longer than 2 minutes is
killed automatically, preventing a single stuck test from blocking the whole
run.

Use ``@pytest.mark.timeout(N)`` to override per-test — shorter for
known-fast tests, longer for slow ones. The ``thread`` method is compatible
with asyncio (``fork`` would break the event loop).

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
