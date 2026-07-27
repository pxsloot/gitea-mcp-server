"""Shared Swagger/OpenAPI spec fixtures for tests.

Provides reusable spec dictionaries at two granularities:

- ``base_spec``: minimal valid Swagger 2.0 spec with **no endpoints**.
  Used as a server configuration template for integration tests.
- ``minimal_spec``: simplest possible spec with **one endpoint**.
  Used for converter unit tests that verify basic conversion behaviour.

Use these instead of defining inline spec dicts in test files, unless the
test truly needs a unique spec shape that doesn't fit either level.
"""


def base_spec() -> dict:
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


def minimal_spec() -> dict:
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
