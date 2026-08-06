---
audience: developer
type: reference
covers: Testing principles, quality gates, coverage policy, sub-doc index
---

# Testing Standards

This document defines the testing principles and quality gates for the Gitea
MCP Server project.  It is a living document — update it when patterns or
conventions change.  For specific topics (layout, zones, fixtures, mocking,
etc.) see the sub-doc index below.

## What this doc is NOT

This doc covers testing principles and quality standards. If you need:

| Topic | See |
|-------|-----|
| Developer checklists, project conventions, how-to workflows | `docs/SKILL.md` |
| Design decisions, the pipeline, the module map | `docs/ARCHITECTURE.md` |
| Environment setup, adding customizations, adding resources | `docs/DEVELOPMENT.md` |
| How token scopes gate tool visibility (and testing scope behavior) | `docs/SCOPE_MODEL.md` |
| Documentation set structural rules (de-duplication, audience split, Diátaxis) | `docs/DOCUMENTATION_STANDARDS.md` |

## Testing doc set

| Doc | Type | Covers |
|-----|------|--------|
| `testing/LAYOUT.md` | reference | Directory layout, naming conventions, source-to-test mapping, cross-cutting test files |
| `testing/ZONES.md` | reference | Five test zones with patterns, examples, coverage targets — plus testing frameworks, what to test, and how-to patterns |
| `testing/FIXTURES.md` | reference | Shared fixtures, typed OpenAPI spec fixtures, SimpleConfig, module-level, inline, async |
| `testing/MOCKING.md` | reference | Mocking rules (good/bad), server-level policy, GiteaClient, mock helpers, result narrowing |
| `testing/CONTEXTVAR.md` | reference | ContextVar lifecycle in tests — safety net, local cleanup, why it matters |
| `testing/ASSERTIONS.md` | reference | Assertion best practices — general principles, examples, validation testing |
| `testing/INTEGRATION.md` | how-to | Integration testing, running tests, markers, parallel/sequential design, timeout safety |
| `testing/LIVE.md` | reference | Zone 5: World, dependency graph, Workflow, RepoState need_* design, quality contracts |
| `testing/LINTING.md` | reference | Test linting, type checking, anti-patterns, coverage enforcement |
| `testing/CI.md` | how-to | CI live tests — Forgejo service container, admin provisioning, test execution, cleanup |

## Coverage Policy

The project enforces two tiers:

- **Tool-enforced minimum**: ``fail_under = 95`` in ``pyproject.toml``.
  CI fails if overall coverage drops below 95%.
- **Aspirational target**: **100%** — every line is tested or
  pragma-justified. This has been achieved and should be maintained.
  Pragma annotations must carry an inline comment explaining *why* the
  line is untestable (see existing examples in ``cache_invalidation.py``,
  ``server.py``, ``config.py``).

Rationale: coverage is a quality signal, not a goal in itself. 100%
is achievable for this codebase (every module is testable), and maintaining
it prevents the slow accumulation of untested branches that erodes
confidence in the suite.

Some nuanced guards (``except Exception`` in ``main_async``, malformed
spec guards in the converter) are legitimately marked ``# pragma: no cover``
with rationale comments. Follow the same pattern: if a line cannot be
tested, explain *why not* in the pragma comment.

### Running Coverage

```bash
# Run tests with coverage
uv run pytest --cov=gitea_mcp_server

# Show missing lines
uv run pytest --cov=gitea_mcp_server --cov-report=term-missing

# Generate HTML report
uv run pytest --cov=gitea_mcp_server --cov-report=html
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
5. Update the relevant sub-doc if introducing new testing patterns
6. Don't copy-paste `SimpleConfig` — use or extend the canonical version
7. Run `uv run pytest` before pushing — all tests should pass
