"""Shared Swagger/OpenAPI spec fixtures for tests.

Provides reusable spec dictionaries at three granularities:

- ``base_spec``: minimal valid Swagger 2.0 spec with **no endpoints**.
  Used as a server configuration template for integration tests.
- ``minimal_spec``: simplest possible spec with **one endpoint**.
  Used for converter unit tests that verify basic conversion behaviour.
- ``make_openapi_spec``: minimal valid post-conversion OpenAPI 3.1 spec.
  Used throughout the test suite where functions expect ``OpenAPISpec``.
  Use this instead of inline dict literals to avoid mypy ``arg-type`` errors.

Prefer ``make_openapi_spec()`` over inline ``dict`` literals for all
post-conversion spec construction.  The factory returns ``OpenAPISpec``,
which satisfies the type expected by production functions.
"""

from typing import Any, cast

from gitea_mcp_server.openapi_types import OpenAPISpec, SwaggerV2Spec


def make_openapi_spec(**overrides: Any) -> OpenAPISpec:
    """Create a minimal valid post-conversion OpenAPI 3.1 spec for tests.

    Returns a typed ``OpenAPISpec`` with sensible defaults that can be
    overridden via keyword arguments.  Use this instead of inline dict
    literals passed to functions expecting ``OpenAPISpec``::

        # Good — typed, no mypy error:
        spec = make_openapi_spec()
        _customize_metadata(route, tool, openapi_spec=spec)

        # Good — with custom paths:
        spec = make_openapi_spec(paths={\"/ping\": {\"get\": ...}})

        # Bad — plain dict triggers mypy arg-type:
        spec = {\"openapi\": \"3.1.0\", ...}
        _customize_metadata(route, tool, openapi_spec=spec)  # mypy error

    The single ``cast()`` is hidden inside this factory rather than
    repeated at every call site across the test suite (~266 occurrences).
    """
    base: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {},
    }
    base.update(overrides)
    return cast("OpenAPISpec", base)


def base_spec() -> SwaggerV2Spec:
    """Minimal valid Swagger 2.0 spec with no endpoints.

    Override in a test class or module to add paths::

        @pytest.fixture
        def base_spec(self, base_spec):
            base_spec["paths"]["/repos/{owner}/{repo}/issues"] = {
                "get": {
                    "operationId": "issueListIssues",
                    "summary": "List issues",
                    "responses": {"200": {"description": "Success"}},
                }
            }
            return base_spec
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Gitea API", "version": "1.0"},
        "basePath": "/api/v1",
        "paths": {},
        "definitions": {},
    }


def minimal_spec() -> SwaggerV2Spec:
    """Simplest possible Swagger 2.0 spec with one endpoint.

    Suitable for converter unit tests that verify basic output
    structure (version, server URL, path preservation) without the
    overhead of a full spec.

    Use as a drop-in replacement for private ``_minimal_spec`` helpers::

        from tests.helpers.spec_fixtures import minimal_spec

        result = convert_swagger_to_openapi_v3(minimal_spec())
        assert result["openapi"] == "3.1.1"
    """
    return {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "basePath": "/api/v1",
        "paths": {"/ping": {"get": {"responses": {"200": {"description": "pong"}}}}},
    }
