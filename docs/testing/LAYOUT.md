---
audience: developer
type: reference
covers: Directory layout, naming conventions, source-to-test mapping, cross-cutting test files
---

# Test Layout

## Directory Layout

```
tests/
├── __init__.py
├── conftest.py                             # Infrastructure: SimpleConfig, event_loop, OTel
├── helpers/
│   ├── __init__.py
│   ├── mock_tool.py                        # make_mock_tool, make_mock_route
│   ├── tool_names.py                       # extract_tool_names
│   └── spec_fixtures.py                    # base_spec, minimal_spec
├── schemas/
│   ├── openapi_3.1_schema.json             # JSON Schema for validating converted specs
│   └── openapi_3.1.1_schema.json
├── swagger.v1.json                         # Extracted subset of Gitea's Swagger spec (test fixture)
├── manual_test_cache_invalidation.py       # Standalone manual verification script
├── unit/
│   ├── __init__.py
│   ├── openapi_converter/                  # Tests for Swagger 2.0 → OpenAPI 3.1 conversion
│   │   ├── test_definitions.py
│   │   ├── test_email_date_handling.py
│   │   ├── test_operation_id_normalization.py
│   │   ├── test_parameters.py
│   │   ├── test_paths.py
│   │   ├── test_responses.py
│   │   ├── test_swagger_to_openapi.py
│   │   ├── test_converter_properties.py    # hypothesis property-based tests
│   │   └── test_utils.py
│   ├── test_cache_invalidation.py
│   ├── test_client.py
│   ├── test_client_gitea_api.py
│   ├── test_client_http_transport.py
│   ├── test_config.py
│   ├── test_constants.py
│   ├── test_display.py
│   ├── test_docs_tools.py
│   ├── test_exceptions.py
│   ├── test_extensions_metadata.py
│   ├── test_filter_info.py
│   ├── test_format.py
│   ├── test_label_service.py
│   ├── test_label_transform.py
│   ├── test_label_validation.py
│   ├── test_logging_config.py
│   ├── test_mcp_builder.py
│   ├── test_mcp_extensions.py
│   ├── test_mcp_tools.py
│   ├── test_mcp_tools_wrapping.py
│   ├── test_pagination.py
│   ├── test_regression_316_dotfile_paths.py
│   ├── test_resource_auto.py
│   ├── test_resource_custom.py
│   ├── test_resource_display.py
│   ├── test_resource_factory.py
│   ├── test_resource_meta.py
│   ├── test_schema_utils.py
│   ├── test_scope.py
│   ├── test_search_bm25.py
│   ├── test_spec_loader.py
│   ├── test_tool_customize.py
│   ├── test_tool_display.py
│   ├── test_tool_errors.py
│   ├── test_tool_examples.py
│   ├── test_tool_exclusion.py
│   ├── test_tool_filter.py
│   ├── test_tool_labels.py
│   ├── test_tool_namespace.py
│   ├── test_tool_schemas.py
│   ├── test_tool_search.py
│   ├── test_type_info.py
│   ├── test_unified_search.py
│   ├── test_validation.py
│   └── test_virtual_params.py
├── integration/
│   ├── __init__.py
│   ├── conftest.py          # mcp_server, search_mcp_server, create_test_server
│   ├── test_cache_invalidation.py
│   ├── test_http_transport_server.py
│   ├── test_lazy_loading.py
│   ├── test_mcp_extensions_integration.py
│   ├── test_resources_integration.py
│   ├── test_server.py
│   └── ...
├── live/
│   ├── __init__.py
│   ├── conftest.py              # Credentials, worker World, pooled MCP clients
│   ├── helpers.py               # Token creation and repository pre-cleanup
│   ├── identities.py            # User, DEV/PEER/RO/LIMITED, scope constants, org/team names
│   ├── world.py                 # Worker-local World (server pool, bootstrap, graph, lifecycle)
│   ├── state.py                 # RepoState tracker, internal helpers (_is_error, _unwrap, …)
│   ├── conflict.py              # ConflictError, BootstrapVerificationError, RepoRequest
│   ├── dependency_graph.py      # Verified dependency cache
│   ├── workflows.py             # Composable workflow facade
│   ├── quality.py               # Orthogonal result-quality contracts
│   ├── assertions.py            # Shape/content/cross-format helpers
│   ├── test_admin_workflows.py  # Identity, organization, team administration
│   ├── test_workflows.py        # Issue-label and issue-to-PR stories
│   ├── test_repo_workflow.py    # Repository, branch, file, tag, status stories
│   ├── test_issue_workflow.py   # Issue, label, milestone, comment, search stories
│   ├── test_pr_workflow.py      # Pull request and diff stories
│   ├── test_cross_format.py     # Format equivalence concern tests
│   ├── test_discovery.py        # Synthetic discovery concern tests
│   ├── test_resources.py        # Resource concern tests
│   ├── test_scope.py            # Token scope concern tests
│   └── test_errors.py           # Transport error-contract concern tests
```

## Source-to-Test Mapping

Every production module in `gitea_mcp_server/` maps to one or more test files.
The **naming convention** is the source of truth — no hand-maintained table
is needed:

> **Naming convention**: Unit test files follow `test_<module_name>.py` for
> flat modules and `test_<abbrev>_<module>.py` for subpackage modules
> (e.g., `tools/search.py` → `test_tool_search.py`).  Integration tests use
> `test_<feature>.py`.  Cross-cutting or behavioral tests use
> `test_<behavior>.py`.

**CI enforcement**: Run `make check-test-coverage` to verify every production
module has a matching test file.  The check walks all source modules, applies
the naming convention, and reports any gap.  Builds fail if coverage is
incomplete.  Intentional deviations (e.g. ``search.py`` →
``test_search_bm25.py``) are documented in ``scripts/check_test_coverage.py``.

## Naming Conventions

- **Unit test files**: `test_<module_name>.py` for flat modules
  (e.g., `client.py` → `test_client.py`).
  For subpackage modules: `test_<abbrev>_<module>.py`
  (e.g., `tools/search.py` → `test_tool_search.py`).
  For subcomponent classes within a module: `test_<parent>_<component>.py`
  (e.g., `client.GiteaAPI` → `test_client_gitea_api.py`).
- **Integration test files**: `test_<feature>.py`
  (e.g., `test_lazy_loading.py`, `test_cache_invalidation.py`).
- **Cross-cutting / behavioral test files**: `test_<behavior>.py`
  (e.g., `test_mcp_tools_wrapping.py`, `test_label_validation.py`).
- **Regression test files**: `test_regression_<issue_num>_<description>.py`
  (e.g., `test_regression_316_dotfile_paths.py`).
- **Test classes**: `Test<ComponentName>` (PascalCase).
- **Test methods**: `test_<behavior_description>` (snake_case).
- **Test fixtures**: Descriptive names, preferably noun-based.
- **New test files**: Follow the existing convention for the module area.
- **Pragmatic deviations**: Strict convention is preferred but not rigidly
  enforced. A name that departs from the rule is acceptable when it provides
  better disambiguation or clarity (e.g., ``search.py`` → ``test_search_bm25.py``
  to distinguish from ``tools/search.py`` tests). Every deviation must be
  documented in ``scripts/check_test_coverage.py``.

## Cross-Cutting / Smoke Test Files

These files don't map to a single source module — they verify invariants
across the entire module tree:

| Test file | What it verifies | Added in |
|---|---|---|
| `tests/unit/test_module_imports.py` | All modules import cleanly (no circular imports); `__all__` exports match defined names; all exported names are importable. **Must be updated** when a new module is added to any subpackage — add its dotted name to ``ALL_MODULES``. | #552 |

## Test Data and Fixtures

- Use pytest fixtures for reusable test data
- Keep widely-shared fixtures in `tests/conftest.py`
- Keep module-specific fixtures in the test module or class
- Use descriptive fixture names that indicate their purpose
- Fixtures should be idempotent and independent
- For spec-related tests, prefer inline dict fixtures over file loads (faster, self-contained)
- Use `tests/swagger.v1.json` only for end-to-end conversion + schema validation tests

```python
@pytest.fixture
def minimal_spec():
    """Return a minimal valid Swagger 2.0 spec."""
    return {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "basePath": "/api/v1",
        "paths": {},
    }
```
