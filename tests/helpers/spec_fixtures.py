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
which satisfies the type expected by production functions; deliberately
non-conforming specs use ``cast("OpenAPISpec", ...)``.  An inline spec dict
literal — annotated (``spec: OpenAPISpec = {...}``) or passed as a keyword
argument (``fn(openapi_spec={...})``) — is rejected by
``tests/unit/test_spec_fixture_convention.py``.  See ``docs/testing/FIXTURES.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict, Unpack, cast

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPIInfo, OpenAPISpec, SwaggerV2Spec


class _SpecOverrides(TypedDict, total=False):
    """Keyword overrides accepted by :func:`make_openapi_spec`.

    Mirrors the top-level ``OpenAPISpec`` keys so a typo (``pats=``) or a
    wrong value type is caught by mypy at the call site.  Nested values stay
    ``Any`` — that is the factory's deliberate escape hatch for arbitrary
    test shapes.
    """

    openapi: str
    info: OpenAPIInfo
    paths: dict[str, Any]
    components: dict[str, Any]
    servers: list[dict[str, Any]]


def make_openapi_spec(
    *, include_defaults: bool = True, **overrides: Unpack[_SpecOverrides]
) -> OpenAPISpec:
    """Create a post-conversion OpenAPI 3.1 spec for tests.

    Returns a typed ``OpenAPISpec``.  By default it carries the minimal
    valid defaults ``openapi="3.1.0"``, ``info={"title": "Test API",
    "version": "1.0.0"}``, and ``paths={}``; a keyword override replaces the
    matching default key, and any other keyword (e.g. ``components`` or
    ``servers``) is added as-is.  Override *names* and top-level value types
    are checked against ``_SpecOverrides``; nested contents stay ``Any`` and
    are not structurally type-checked — that is the factory's deliberate
    escape hatch.  Use this instead of inline dict literals passed to
    functions expecting ``OpenAPISpec``::

        # Good — typed, no mypy error:
        spec = make_openapi_spec()
        _customize_metadata(route, tool, openapi_spec=spec)

        # Good — with custom paths:
        spec = make_openapi_spec(paths={"/ping": {"get": ...}})

        # Good — exact key set, no defaults:
        spec = make_openapi_spec(include_defaults=False, openapi="3.1.1")

        # Bad — plain dict triggers mypy arg-type:
        spec = {"openapi": "3.1.0", ...}
        _customize_metadata(route, tool, openapi_spec=spec)  # mypy error

    Pass ``include_defaults=False`` for a spec whose exact key set matters
    (empty spec, or a spec that deliberately omits a defaulted key); only the
    ``**overrides`` become keys.  Deliberately malformed specs use
    ``cast("OpenAPISpec", ...)`` instead — see ``docs/testing/FIXTURES.md``.

    The single ``cast()`` is hidden inside this factory rather than repeated
    at every call site.  ``tests/unit/test_spec_fixture_convention.py``
    guards the convention so it cannot re-drift.
    """
    base: dict[str, Any] = {}
    if include_defaults:
        base = {
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
