---
audience: developer
type: reference
covers: Test linting, type checking, anti-patterns, coverage enforcement
---

# Test Linting, Type Checking, and Anti-Patterns

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

## Test Code Linting

Test code must comply with the project's ruff lint rules. The
``[tool.ruff.lint.per-file-ignores]`` section in ``pyproject.toml`` documents
the few intentional exemptions (e.g. unused arguments in fixtures, bare asserts,
magic numbers in test data).

Enforcement:
- ``make test`` runs ``ruff check tests/`` before pytest
- CI runs a dedicated ``lint-tests`` job

## Code Formatting

The project's formatter is ``ruff format`` (line length 100, matching the
``[tool.ruff]`` config). All production and test code must be
``ruff format``-clean — the whole tree, not just new files.

Enforcement:
- ``make test`` runs ``ruff format --check .`` before pytest
- CI runs a dedicated ``format-check`` job/step

To apply or verify formatting:

```bash
make format        # ruff format . — reformat the whole tree
make format-check  # ruff format --check . — verify only (fails if dirty)
```

Run ``make format`` before committing when ``make format-check`` (or
``make test``) reports unformatted files. The baseline reformat is
excluded from ``git blame`` via ``.git-blame-ignore-revs``.

## Test Code Type Checking

Test code is type-checked with the same strict ``pyproject.toml`` mypy
configuration as production code. No per-file overrides are needed — test
code passes the full strict rule set including ``disallow_untyped_defs``,
``disallow_incomplete_defs``, and ``warn_return_any``.

### Running locally

```bash
# Run type checks on both production and test code
make test-types

# Or directly:
uv run mypy gitea_mcp_server/
uv run mypy tests/
```

### What's checked

- **Return type annotations**: Every test function must have a return type
  annotation (``-> None`` for void tests, ``-> dict[str, Any]`` for helpers).
- **MCP result narrowing**: Tool results return as ``ToolResult`` with
  ``TextContent | ImageContent | ...`` unions. Use the helpers in
  ``tests/helpers/mcp_results.py`` to narrow these unions and avoid
  ``union-attr`` errors:
  ```python
  from tests.helpers.mcp_results import extract_text_content, parse_json_content
  result = await mcp.call_tool("gitea_issue_list_issues", {...})
  text = extract_text_content(result.content)
  data = parse_json_content(result)
  ```
- **OpenAPI spec typing**: Test fixtures that construct specs should use
  the ``make_openapi_spec()`` factory (returns typed ``OpenAPISpec``).
  See `testing/FIXTURES.md` for the three-tier strategy.
- **Mock helpers**: Use ``make_async_mock(SomeClass)`` and
  ``make_magic_mock(some_callable)`` from ``tests/helpers/mock_tool.py``
  to avoid mypy narrowing mocked objects to their spec type (which hides
  ``.return_value``, ``.side_effect``, ``.assert_called_once_with``).

### Enforcement

- ``make test`` runs ``mypy tests/`` before pytest, alongside production type checks
- Forgejo CI (``.gitea/workflows/ci.yml``): dedicated ``typecheck-tests`` job

## Coverage Enforcement

The project enforces a minimum coverage of 95% overall (configured as
``fail_under = 95`` in ``pyproject.toml``).  CI fails if overall coverage
drops below 95%.

The aspirational target is **100%** — every line is tested or pragma-justified.
Pragma annotations must carry an inline comment explaining *why* the line is
untestable (see existing examples in ``cache_invalidation.py``, ``server.py``,
``config.py``).

Rationale: coverage is a quality signal, not a goal in itself. 100% is
achievable for this codebase, and maintaining it prevents the slow accumulation
of untested branches that erodes confidence in the suite.

Some nuanced guards (``except Exception`` in ``main_async``, malformed spec
guards in the converter) are legitimately marked ``# pragma: no cover`` with
rationale comments. Follow the same pattern: if a line cannot be tested,
explain *why not* in the pragma comment.
