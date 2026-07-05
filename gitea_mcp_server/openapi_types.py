"""TypedDicts for OpenAPI spec navigation spine.

Defines TypedDict types for the well-defined parts of the OpenAPI 3.1 and
Swagger 2.0 specifications.  The deep recursive parts (``$ref`` chains, nested
schemas, schema properties) stay ``dict[str, Any]`` since their keys are
inherently dynamic.

All TypedDicts use ``total=False`` to match the existing ``.get()`` guard
patterns throughout the codebase.  No logic changes are needed — the
type-checker validates all ``.get()`` and ``[]`` access against the known keys.

Two spec shapes are defined:
  * ``SwaggerV2Spec`` — pre-conversion Swagger 2.0 input
  * ``OpenAPISpec`` — post-conversion OpenAPI 3.1 (used by tools,
    server_setup, and resources modules)
"""

from typing import Any, TypedDict


class OpenAPIInfo(TypedDict, total=False):
    """OpenAPI info object — ``title``, ``version``, ``description``."""

    title: str
    version: str
    description: str


class OpenAPIParameter(TypedDict, total=False):
    """OpenAPI parameter object.

    Note: the ``in`` field is accessed via ``param["in"]`` (reserved word).
    """

    name: str
    # in: str  -- reserved word, accessed via param["in"]
    required: bool
    schema: dict[str, Any]  # stays Any (recursive schema tree)
    description: str


class OpenAPIResponse(TypedDict, total=False):
    """OpenAPI response object — ``description``, ``content``."""

    description: str
    content: dict[str, Any]  # media-type keys are dynamic


# ── Pre-conversion: Swagger 2.0 ─────────────────────────────────────────


class SwaggerV2Spec(TypedDict, total=False):
    """Swagger 2.0 input spec — used only in converter entry point.

    Has ``swagger``, ``basePath``, ``definitions``, ``securityDefinitions``
    — no ``openapi``, ``components``, or ``servers``.
    """

    swagger: str
    info: OpenAPIInfo
    basePath: str
    host: str
    schemes: list[str]
    paths: dict[str, Any]  # paths stay Any; sub-ops vary by shape
    definitions: dict[str, Any]
    parameters: dict[str, Any]
    responses: dict[str, Any]
    securityDefinitions: dict[str, Any]
    consumes: list[str]
    produces: list[str]


# ── Post-conversion: OpenAPI 3.1 ────────────────────────────────────────


class OpenAPIOperation(TypedDict, total=False):
    """OpenAPI 3.1 operation object.

    ``parameters`` is a mixed list — callers that need typed access should
    narrow via ``isinstance(p, dict)``.  ``responses`` stays ``dict[str, Any]``
    because status-code keys are dynamic strings.
    """

    operationId: str
    summary: str
    description: str
    parameters: list[dict[str, Any] | OpenAPIParameter]
    responses: dict[str, Any]  # status-code keys are dynamic
    tags: list[str]
    deprecated: bool
    requestBody: dict[str, Any]
    x_original_content_types: list[str]  # preserved before wrap


class OpenAPIPathItem(TypedDict, total=False):
    """OpenAPI 3.1 path item — HTTP method keys point to operations.

    Path-level ``parameters`` are included for typed access where available.
    """

    parameters: list[dict[str, Any] | OpenAPIParameter]
    get: OpenAPIOperation
    post: OpenAPIOperation
    put: OpenAPIOperation
    delete: OpenAPIOperation
    patch: OpenAPIOperation
    options: OpenAPIOperation
    head: OpenAPIOperation
    trace: OpenAPIOperation


class OpenAPISpec(TypedDict, total=False):
    """Post-conversion OpenAPI 3.1 spec — used by tools, server_setup, resources.

    Has ``openapi``, ``info``, ``paths``, ``components``, ``servers``.
    ``paths`` values are ``OpenAPIPathItem``; path *keys* remain dynamic URL
    strings.  ``components`` sub-keys (schemas, responses, parameters) are
    dynamic and stay ``dict[str, Any]``.
    """

    openapi: str
    info: OpenAPIInfo
    paths: dict[str, OpenAPIPathItem]  # URL-path keys are dynamic strings
    components: dict[str, Any]  # schema/response components stay Any
    servers: list[dict[str, Any]]


__all__ = [
    "OpenAPIInfo",
    "OpenAPIOperation",
    "OpenAPIParameter",
    "OpenAPIPathItem",
    "OpenAPIResponse",
    "OpenAPISpec",
    "SwaggerV2Spec",
]
