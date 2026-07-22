"""Tool output schema derivation and $ref resolution."""

from typing import Any, cast

from gitea_mcp_server.openapi_converter import _resolve_spec_ref as _resolve_ref
from gitea_mcp_server.openapi_types import OpenAPISpec


def _collect_refs(schema: Any) -> set[str]:
    """Recursively collect all ``$ref`` type names referenced in a schema.

    Walks ``properties``, ``items``, ``additionalProperties``,
    ``allOf``/``oneOf``/``anyOf``, plus JSON Schema applicators
    ``not``/``if``/``then``/``else``, to find every ``$ref`` pointer,
    then extracts the simple type name from each (e.g. ``"User"``
    from ``"#/components/schemas/User"``).

    .. note::

        This function is used by ``type_info.py`` for building the
        type index, but lives here because it is a general-purpose
        schema walking utility.

    Args:
        schema: A JSON Schema dict (may contain ``$ref`` pointers).

    Returns:
        Set of referenced type names.
    """
    refs: set[str] = set()
    if not isinstance(schema, dict):
        return refs

    if "$ref" in schema and isinstance(schema.get("$ref"), str):
        refs.add(schema["$ref"].rsplit("/", 1)[-1])

    # properties: values are individual property schemas
    props = schema.get("properties")
    if isinstance(props, dict):
        for prop_schema in props.values():
            if isinstance(prop_schema, dict):
                refs |= _collect_refs(prop_schema)

    # items/additionalProperties + JSON Schema applicator keywords:
    # not, if, then, else all take a single schema object (which may contain $ref).
    for key in ("items", "additionalProperties", "not", "if", "then", "else"):
        val = schema.get(key)
        if isinstance(val, dict):
            refs |= _collect_refs(val)

    arr_keys: tuple[str, ...] = ("allOf", "oneOf", "anyOf")
    for key in arr_keys:
        items = schema.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    refs |= _collect_refs(item)

    return refs


def _deep_resolve_schema(
    schema: Any,
    openapi_spec: OpenAPISpec,
    _seen: set[str] | None = None,
) -> dict[str, Any]:
    """Recursively resolve all $ref pointers in a schema against the spec.

    Args:
        schema: Schema tree (individual JSON Schema node, typed ``Any``
                because property names are dynamic).
        openapi_spec: Post-conversion OpenAPI 3.1 spec (typed as
                      ``OpenAPISpec``), used for ``$ref`` resolution.
        _seen: Set of already-resolved refs to prevent circular loops.

    Returns:
        Resolved schema dict with all ``$ref`` pointers expanded.
    """
    if not isinstance(schema, dict):
        return {}
    result: dict[str, Any] = {}
    _seen = _seen or set()

    for key, value in schema.items():
        if key == "$ref" and isinstance(value, str):
            if value in _seen:
                result[key] = value
                continue
            _seen.add(value)
            resolved = _resolve_ref(openapi_spec, value)
            if isinstance(resolved, dict):
                deep = _deep_resolve_schema(resolved, openapi_spec, _seen)
                result.update(deep)
            else:
                result[key] = value
        elif key in ("properties",):
            result[key] = {
                k: _deep_resolve_schema(v, openapi_spec, _seen) if isinstance(v, dict) else v
                for k, v in value.items()
            }
        elif key in ("items", "additionalProperties"):
            result[key] = (
                _deep_resolve_schema(value, openapi_spec, _seen)
                if isinstance(value, dict)
                else value
            )
        elif key in ("allOf", "oneOf", "anyOf"):
            result[key] = [
                _deep_resolve_schema(item, openapi_spec, _seen) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, dict):
            result[key] = _deep_resolve_schema(value, openapi_spec, _seen)
        else:
            result[key] = value

    return result


def _is_text_response(openapi_spec: OpenAPISpec, path: str, method: str) -> bool:
    """Check if the response for a given path/method is non-JSON (text/plain, etc.).

    The ``method`` parameter is normalised to lowercase internally.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``).
        path: The API path to check.
        method: The HTTP method to check.

    Returns:
        ``True`` if the response content type is not ``application/json``.
    """
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return False
    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return False
    content_types = operation.get("x-original-content-types")
    if not isinstance(content_types, list):
        return False
    return any(ct.lower().strip() != "application/json" for ct in content_types)


def _response_has_no_content(openapi_spec: OpenAPISpec, path: str, method: str) -> bool:
    """Check if the endpoint's success response has no body content.

    Returns ``True`` when a 2xx success response has no ``content``
    key — e.g. 204 No Content, 205 Reset Content, 202 Accepted with no
    body, or 200/201 responses that reference an empty response definition
    (like Gitea's ``$ref: #/responses/empty``).

    ``$ref`` pointers are resolved before checking for ``content``, so
    shared empty response definitions like Gitea's ``$ref: #/responses/empty``
    are correctly detected.

    The ``method`` parameter is normalised to lowercase internally.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``).
        path: The API path to check.
        method: The HTTP method to check.

    Returns:
        ``True`` if a 2xx response exists without a ``content`` entry.
    """
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return False
    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return False
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return False
    # 200/201 are included because Gitea's spec uses shared empty response
    # definitions via ``$ref`` (e.g. ``$ref: #/responses/empty``) for
    # endpoints like ``POST /repos/{owner}/{repo}/pulls/{index}/merge``.
    # Only ``$ref``-based 200/201 responses are flagged — inline 200/201
    # without a ``content`` key are treated as incomplete specs, not
    # genuine empty-body endpoints (the converter would have added
    # ``content`` if a schema were present).
    #
    # 203 (Non-Authoritative Information) and 206 (Partial Content)
    # are intentionally excluded: 203 always mirrors a 200 body, and
    # 206 only makes sense for range requests on endpoints that also
    # define 200 — both would have ``content`` in practice.
    for code in ("200", "201", "202", "204", "205"):
        response = responses.get(code)
        if not isinstance(response, dict):
            continue
        has_ref = "$ref" in response
        if has_ref:
            resolved = _resolve_ref(openapi_spec, response["$ref"])
            if not isinstance(resolved, dict):
                continue
            response = resolved
        content = response.get("content", {})
        if not content:
            # For 200/201, only flag empty content when the response
            # uses ``$ref`` (Gitea's explicit empty-body idiom).
            # Inline 200/201 without content are spec gaps, not
            # genuine empty-body endpoints.
            if code in ("200", "201") and not has_ref:
                continue
            return True
    return False


def _get_success_schema(  # noqa: PLR0911 - many early returns for guard clauses
    openapi_spec: OpenAPISpec,
    path: str,
    method: str,
    resolve: bool = True,
) -> dict[str, Any] | None:
    """Extract the 200/201 response schema for a path and method.

    The ``method`` parameter is normalised to lowercase internally,
    consistent with :func:`_is_text_response` and
    :func:`_response_has_no_content`.

    When ``resolve=True`` (default), all nested ``$ref`` pointers are
    expanded via ``_deep_resolve_schema``.  When ``resolve=False``, the
    schema is returned with ``$ref`` intact - used by the compact example
    generator to emit type names instead of inlining referenced schemas.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``).
        path: The API path to inspect.
        method: The HTTP method to inspect.
        resolve: If ``True`` (default), deep-resolve all ``$ref`` pointers.
                 If ``False``, return the schema with ``$ref`` intact.

    Returns:
        The response schema (resolved or raw), or ``None`` if no JSON
        response is found.
    """
    method = method.lower()
    if _is_text_response(openapi_spec, path, method):
        return None

    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return None
    operation = path_item.get(method)
    if not isinstance(operation, dict):
        return None
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return None

    for code in ("200", "201"):
        response = responses.get(code)
        if not isinstance(response, dict):
            continue

        if "$ref" in response:
            resolved = _resolve_ref(openapi_spec, response["$ref"])
            if not isinstance(resolved, dict):
                continue
            response = resolved

        content = response.get("content", {})
        if not isinstance(content, dict):
            continue
        json_content = content.get("application/json", {})
        if not isinstance(json_content, dict):
            continue
        schema = json_content.get("schema")
        if not isinstance(schema, dict):
            continue

        if resolve:
            return _deep_resolve_schema(schema, openapi_spec)
        return schema

    return None


def derive_output_schema(
    route: Any,
    openapi_spec: OpenAPISpec | None,
) -> dict[str, Any] | None:
    """Derive a resolved output schema from the route's success response.

    Args:
        route: FastMCP route object.
        openapi_spec: Post-conversion OpenAPI 3.1 spec (typed as
                      ``OpenAPISpec``), or ``None`` to return ``None``.

    Returns:
        The resolved output schema, or ``None`` for text/non-JSON endpoints
        or when no spec is available.
    """
    if openapi_spec is None:
        return None

    method = getattr(route, "method", "").lower()
    return _get_success_schema(openapi_spec, route.path, method)


def _schema_type_is_array(schema: dict[str, Any]) -> bool:
    """Check whether a schema dict has type 'array' (string or list form)."""
    t = schema.get("type")
    if isinstance(t, str):
        return t == "array"
    if isinstance(t, list):
        return "array" in t
    return False


def _unwrap_result_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract the inner schema from a ``{"result": ...}`` wrapped response schema.

    The OpenAPI converter wraps all response schemas in a ``{"result": ...}``
    envelope (via :func:`_wrap_success_response_schemas`) to satisfy FastMCP's
    ``type: object`` requirement for output validation.

    Consumers that need a schema matching the *actual API response shape*
    (data collapse, example generation) must use the inner schema.  This
    function is the single place where that unwrapping logic lives.

    When the schema is not wrapped (no ``result`` property), it is returned
    unchanged — the function is idempotent for inner and non-wrapped schemas.

    Args:
        schema: A JSON Schema dict, possibly wrapped in ``{"result": ...}``,
                or ``None``.

    Returns:
        The inner schema (``properties.result``), or ``schema`` unchanged
        if there is no ``result`` property or if ``schema`` is ``None``.
    """
    if schema and isinstance(schema, dict) and schema.get("type") == "object":
        inner = schema.get("properties", {}).get("result")
        if isinstance(inner, dict):
            return inner
    return schema


__all__ = [
    "_collect_refs",
    "_deep_resolve_schema",
    "_get_success_schema",
    "_is_text_response",
    "_resolve_ref",
    "_response_has_no_content",
    "_schema_type_is_array",
    "_unwrap_result_schema",
    "derive_output_schema",
]
