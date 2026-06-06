"""Tool output schema derivation and $ref resolution."""

from typing import Any

from gitea_mcp_server.openapi_converter import _resolve_spec_ref as _resolve_ref


def _deep_resolve_schema(
    schema: Any,
    openapi_spec: dict[str, Any],
    _seen: set[str] | None = None,
) -> dict[str, Any]:
    """Recursively resolve all $ref pointers in a schema against the spec."""
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
            result[key] = _deep_resolve_schema(value, openapi_spec, _seen) if isinstance(value, dict) else value
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


def _is_text_response(openapi_spec: dict[str, Any], path: str, method: str) -> bool:
    """Check if the response for a given path/method is non-JSON (text/plain, etc.)."""
    paths = openapi_spec.get("paths", {})
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return False
    operation = path_item.get(method)
    if not isinstance(operation, dict):
        return False
    content_types = operation.get("x-original-content-types")
    if not isinstance(content_types, list):
        return False
    return any(
        ct.lower().strip() != "application/json" for ct in content_types
    )


def _get_success_schema(
    openapi_spec: dict[str, Any],
    path: str,
    method: str,
) -> dict[str, Any] | None:
    """Extract the resolved 200/201 response schema for a path and method."""
    if _is_text_response(openapi_spec, path, method):
        return None

    paths = openapi_spec.get("paths", {})
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

        return _deep_resolve_schema(schema, openapi_spec)

    return None


def derive_output_schema(
    route: Any,
    openapi_spec: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Derive a resolved output schema from the route's success response."""
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


__all__ = [
    "_deep_resolve_schema",
    "_get_success_schema",
    "_is_text_response",
    "_resolve_ref",
    "_schema_type_is_array",
    "derive_output_schema",
]
