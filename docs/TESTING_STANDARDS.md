# Testing Standards

This document defines the testing standards and best practices for the Gitea MCP Server project.

## Test Structure and Organization

### Directory Layout

```
tests/
├── conftest.py                 # Shared fixtures and configuration
├── unit/                       # Unit tests
│   ├── openapi_converter/     # Tests for OpenAPI converter module
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
│   ├── test_config.py
│   ├── test_label_validation.py
│   ├── test_mcp_tools.py
│   ├── test_resources.py
│   ├── test_tool_annotations.py
│   └── test_tool_filter.py
└── integration/               # Integration tests
    ├── test_cache_invalidation.py
    ├── test_lazy_loading.py
    ├── test_resources_integration.py
    └── test_server.py
```

### Naming Conventions

- **Test files**: `test_<module_name>.py`
- **Test classes**: `Test<ComponentName>` (PascalCase)
- **Test methods**: `test_<behavior_description>` (snake_case)
- **Test fixtures**: Descriptive names, preferably noun-based

## Testing Frameworks and Tools

- **pytest**: Primary test runner
- **pytest-asyncio**: Async test support (asyncio_mode = "auto")
- **pytest-mock**: Mocking via `mocker` fixture
- **pytest-cov**: Coverage measurement
- **respx**: HTTP request mocking for async HTTP clients
- **jsonschema**: Schema validation (where applicable)

## Unit Testing Guidelines

### Basic Structure

```python
"""Module docstring explaining test coverage."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from my_module import MyClass, my_function


class TestMyClass:
    """Tests for MyClass."""

    @pytest.fixture
    def instance(self):
        """Create a test instance with default config."""
        return MyClass(config={"setting": "value"})

    def test_initialization(self):
        """Test object initialization."""
        obj = MyClass()
        assert obj is not None

    async def test_async_method(self, instance):
        """Test async method behavior."""
        result = await instance.async_method()
        assert result.success is True

    def test_with_mock(self, mocker):
        """Test using mocker fixture."""
        mock_dep = mocker.AsyncMock()
        mock_dep.method.return_value = "mocked"
        result = await my_function(mock_dep)
        assert result == "mocked"
```

### Async Tests

- Use `@pytest.mark.asyncio` decorator or rely on `asyncio_mode = "auto"`
- All async tests must be properly awaited
- Use `AsyncMock` for mocking async methods (not `MagicMock`)

```python
@pytest.mark.asyncio
async def test_async_operation(mocker):
    mock_client = mocker.AsyncMock()
    mock_client.fetch.return_value = {"key": "value"}
    result = await some_async_function(mock_client)
    assert result == {"key": "value"}
```

### Using respx for HTTP Mocking

```python
import respx

@pytest.mark.asyncio
async def test_http_request(config):
    client = GiteaClient(config)
    with respx.mock() as mock:
        mock.get("/api/v1/user").respond(200, json={"name": "test"})
        result = await client.request("GET", "/user")
        assert result["name"] == "test"

@pytest.mark.asyncio
async def test_http_error_handling(config):
    client = GiteaClient(config)
    with respx.mock() as mock:
        mock.get("/api/v1/user").respond(404, json={"message": "Not found"})
        with pytest.raises(GiteaAPIError) as exc_info:
            await client.request("GET", "/user")
        assert exc_info.value.status_code == 404
```

### Mocking Best Practices

- **Use fixtures** for common mock objects
- **Set explicit return values** or side effects
- **Avoid over-mocking**: only mock dependencies, not the object under test
- **Verify calls** when interaction matters:

```python
mock_method.assert_called_once_with(expected_arg)
assert mock_method.call_count == 3
```

## Coverage Configuration

Coverage is configured in `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["gitea_mcp_server"]
omit = ["*/migrations/*", "*/tests/*"]

[tool.coverage.report]
fail_under = 70
show_missing = true
skip_covered = false
exclude_also = [
    "if TYPE_CHECKING:",
    "@(abc\\.)?abstractmethod"
]
```

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

# Better - with descriptive messages
assert result["id"] == 42, "Expected ID to be 42 after creation"
assert len(items) == 3, f"Expected 3 items but got {len(items)}"
```

### Validation Testing

When testing validation logic (e.g., in `validation.py`):

- **Test each validator** with both valid and invalid inputs. Use `pytest.mark.parametrize` to cover many cases.
- **Test schema augmentation**: verify that the tool's JSON schema gets the expected constraints (`minLength`, `maxLength`, `pattern`, `enum`, etc.).
- **Test runtime wrapper**: wrap a mock tool, call with invalid arguments, and assert `ValidationError` is raised *before* the tool's `run` method executes. Use `AsyncMock` for the original run.
- **Coverage**: Aim for >90% coverage of validation modules.

```

## Integration Testing

Integration tests are placed in `tests/integration/` and test real interactions between components.

- Use real fixtures where possible (e.g., temporary databases, files)
- Mock only external services (HTTP APIs) using respx
- Clean up resources in teardown
- Mark with `@pytest.mark.integration` if needed for selective runs

## Test Data and Fixtures

- Use pytest fixtures for reusable test data
- Keep fixtures in `conftest.py` or within test modules
- Use descriptive fixture names that indicate their purpose
- Fixtures should be idempotent and independent

```python
@pytest.fixture
def sample_swagger_spec():
    """Return a minimal valid OpenAPI spec."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {}
    }
```

## Running Tests

```bash
# Run all tests
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

# Show print statements
uv run pytest -s
```

## Quality Standards

- **All tests must be deterministic**: No random behavior without fixed seeds
- **Tests should be isolated**: No shared state between tests
- **Tests should be fast**: Aim for <100ms per test on average
- **No external dependencies**: Tests should not require network access or external services
- **Proper cleanup**: Use fixtures with proper setup/teardown

## When Adding New Tests

1. Follow existing patterns in the codebase
2. Add documentation/docstrings explaining what is being tested
3. Ensure tests fail without the implementation (TDD approach)
4. Place tests in appropriate directory (unit vs integration)
5. Update this document if introducing new testing patterns

## Coverage Enforcement

The project enforces a minimum coverage of 70%. This means:

- `pytest` with coverage will fail if total coverage falls below 70%
- New code should aim to be covered by tests
- When fixing bugs, add regression tests first
- Coverage excludes: type checking blocks, abstract methods

## Related Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [respx](https://github.com/lundberg/respx)
- [pytest-mock](https://pytest-mock.readthedocs.io/)
