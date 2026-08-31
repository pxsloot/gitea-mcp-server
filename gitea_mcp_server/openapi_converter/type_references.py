"""Type-reference analysis for cache invalidation (pre-wrap).

Runs inside ``convert_swagger_to_openapi_v3`` after ``$ref`` fixup and
*before* response-schema wrapping (which inlines top-level ``$ref``s and
would lose type names).  Stamps two operation-level extensions:

* ``x-resource-types`` on every GET operation — the set of schema types its
  response references, transitively through ``$ref``s (e.g. the issues
  resource reports ``Issue`` *and* every type Issue references: ``Label``,
  ``Milestone``, ``User``, ...).
* ``x-modifies-type`` on every write operation — the resource type it
  modifies, taken from its own response schema (POST/PUT/PATCH return the
  created/updated object) with a fallback to the GET sibling at the same
  path for 204/empty responses (DELETE).

The cache-invalidation derivation reads these stamps to find cross-tree
resources whose content a write can change — e.g. label writes change
issues/pulls because the Issue schema references Label.  The relationship
is spec-derived, so it can never drift from the resource surface.

This module is deliberately self-contained (no imports from ``core``) so
``core`` can import ``stamp_type_references`` without a circular import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from gitea_mcp_server.constants import HTTP_METHODS_ALL

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec


def _resolve_ref(spec: OpenAPISpec, ref: str) -> dict[str, Any] | None:
    """Resolve a ``$ref`` pointer (e.g. ``#/components/schemas/Foo``) in a spec.

    Walks the spec tree using string path segments.  Returns ``None`` if
    any segment is missing (handles malformed refs gracefully).
    """
    parts = ref.lstrip("#/").split("/")
    current: Any = spec
    try:
        for part in parts:
            current = current[part]
    except (KeyError, TypeError):
        return None
    return current if isinstance(current, dict) else None


def _collect_refs(schema: Any) -> set[str]:
    """Recursively collect the direct ``$ref`` type names in a schema.

    Walks ``properties``, ``items``, ``additionalProperties``,
    ``allOf``/``oneOf``/``anyOf``, plus JSON Schema applicators
    ``not``/``if``/``then``/``else``, and extracts the simple type name
    from each ``$ref`` (e.g. ``"User"`` from
    ``"#/components/schemas/User"``).  Does not follow the referenced
    schemas — see :func:`_collect_transitive_refs`.
    """
    refs: set[str] = set()
    if not isinstance(schema, dict):
        return refs
    if isinstance(schema.get("$ref"), str):
        refs.add(schema["$ref"].rsplit("/", 1)[-1])
    props = schema.get("properties")
    if isinstance(props, dict):
        for prop_schema in props.values():
            if isinstance(prop_schema, dict):
                refs |= _collect_refs(prop_schema)
    for key in ("items", "additionalProperties", "not", "if", "then", "else"):
        val = schema.get(key)
        if isinstance(val, dict):
            refs |= _collect_refs(val)
    for key in ("allOf", "oneOf", "anyOf"):
        items = schema.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    refs |= _collect_refs(item)
    return refs


def _collect_transitive_refs(
    schema: Any,
    openapi_spec: OpenAPISpec,
    _seen: set[str] | None = None,
) -> set[str]:
    """Collect every type name referenced by a schema, transitively.

    Unlike :func:`_collect_refs` (direct references only), this follows
    each referenced schema's own references, so a resource whose response
    is ``[Issue]`` reports ``Issue`` *and* every type Issue references
    (``Label``, ``Milestone``, ``User``, ...).  The invalidation
    derivation uses this to find cross-tree resources: a label write
    (type ``Label``) changes the issues resource because ``Label`` is
    reachable from ``Issue``.

    Args:
        schema: A JSON Schema dict (may contain ``$ref`` pointers).
        openapi_spec: Post-conversion OpenAPI 3.1 spec for resolution.
        _seen: Set of already-visited type names (cycle guard).

    Returns:
        Set of all transitively referenced type names.
    """
    if not isinstance(schema, dict):
        return set()
    direct = _collect_refs(schema)
    result = set(direct)
    _seen = _seen or set()
    for ref in direct:
        if ref in _seen:
            continue
        _seen.add(ref)
        resolved = _resolve_ref(openapi_spec, f"#/components/schemas/{ref}")
        if isinstance(resolved, dict):
            result |= _collect_transitive_refs(resolved, openapi_spec, _seen)
    return result


def _success_schema(openapi_spec: OpenAPISpec, path: str, method: str) -> dict[str, Any] | None:
    """Return the raw 200/201 response schema (pre-wrap, ``$ref`` intact).

    The ``method`` parameter is normalised to lowercase internally.
    Response-level ``$ref``s (e.g. ``$ref: #/components/responses/empty``)
    are resolved before reading ``content``.
    """
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return None
    operation = path_item.get(method.lower())
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
        if isinstance(schema, dict):
            return schema
    return None


def _primary_type(schema: dict[str, Any] | None) -> str | None:
    """Extract the primary (element) type name from a response schema.

    Handles array responses (``items.$ref``) and object responses
    (``$ref``).  Returns ``None`` when the schema has no single primary
    type (text responses, empty bodies, inline schemas without a ``$ref``).
    """
    if not schema:
        return None
    type_ = schema.get("type")
    if type_ == "array" or (isinstance(type_, list) and "array" in type_):
        items = schema.get("items")
        ref = items.get("$ref") if isinstance(items, dict) else None
        if isinstance(ref, str):
            return ref.rsplit("/", 1)[-1]
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    return None


def stamp_type_references(openapi_spec: OpenAPISpec) -> None:
    """Stamp ``x-resource-types`` on GET ops and ``x-modifies-type`` on write ops.

    Mutates the spec in place.  GET operations get the transitive set of
    types their response schema references; write operations get the
    resource type they modify (from their own response schema, falling
    back to the GET sibling at the same path for 204/empty responses).

    Must run *before* ``_wrap_success_response_schemas`` — the wrapping
    inlines top-level ``$ref``s, which would lose the type names.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (pre-wrap, ``$ref``
            intact).  Mutated in place.
    """
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS_ALL or not isinstance(operation, dict):
                continue
            if method == "get":
                schema = _success_schema(openapi_spec, path, "GET")
                if schema is not None:
                    types = _collect_transitive_refs(schema, openapi_spec)
                    if types:
                        operation["x-resource-types"] = sorted(types)
            else:
                modified = _primary_type(_success_schema(openapi_spec, path, method))
                if modified is None:
                    modified = _primary_type(_success_schema(openapi_spec, path, "GET"))
                if modified:
                    operation["x-modifies-type"] = modified


__all__ = [
    "stamp_type_references",
]
