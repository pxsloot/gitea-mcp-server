"""Schema-to-example and tool serialization utilities."""

from typing import Any

from fastmcp.tools.base import Tool

from gitea_mcp_server.format import resolve_ref_chain
from gitea_mcp_server.marker import ref_marker
from gitea_mcp_server.models import ToolSchemaResult
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.schema_utils import extract_type_name, get_schema_type
from gitea_mcp_server.tools.schemas import unwrap_result_schema

_PROP_EXAMPLE_MAP: dict[str, str] = {
    "name": "example-name",
    "title": "Example Title",
    "body": "Issue body content",
    "description": "A description of the item",
    "content": "File content here",
    "message": "Commit message",
    "ref": "main",
    "sha": "abc123def456",
    "color": "#00aabb",
    "path": "path/to/file",
    "type": "example",
    "status": "active",
    "state": "open",
    "mode": "0644",
    "language": "Python",
    "encoding": "base64",
    "format": "json",
    "key": "example-key",
    "value": "example-value",
    "login": "user",
    "username": "user",
    "full_name": "Full Name",
    "branch": "main",
    "tag": "v1.0.0",
    "label": "bug",
    "visibility": "public",
    "permission": "read",
    "fingerprint": "00:11:22:33:44:55",
    "homepage": "https://example.com",
    "website": "https://example.com",
    "default_branch": "main",
    "filename": "file.txt",
    "text": "Sample text",
    "link": "https://example.com/link",
    "version": "1.0.0",
    "count": "10",
    "url": "https://example.com/path",
}


_SUFFIX_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("_url", "_uri", "_href", "URL", "Url"), "https://example.com/path"),
    (("_name", "Name"), "example-name"),
    (("_sha", "_hash", "SHA"), "abc123def456"),
    (("_id", "ID", "Id"), "example-id"),
    (("_branch", "_head", "Branch"), "main"),
]


def _lookup_string_example(prop_name: str | None) -> str | None:
    """Look up a meaningful example for a string property by name."""
    if prop_name is None:
        return None
    if prop_name in _PROP_EXAMPLE_MAP:
        return _PROP_EXAMPLE_MAP[prop_name]
    for suffixes, value in _SUFFIX_PATTERNS:
        for suffix in suffixes:
            if prop_name.endswith(suffix):
                return value
    return None


def _example_object(
    schema: dict[str, Any],
    depth: int,
    max_depth: int,
    max_properties: int,
) -> dict[str, Any]:
    """Generate an example value from an object schema."""

    if depth >= max_depth:
        return {}
    properties = schema.get("properties", {})
    if not properties:
        return {}
    example: dict[str, Any] = {}
    for prop_name in list(properties.keys())[:max_properties]:
        prop_schema = properties[prop_name]
        example[prop_name] = _schema_to_example(
            prop_schema if isinstance(prop_schema, dict) else {},
            depth + 1,
            max_depth,
            max_properties,
            prop_name=prop_name,
        )
    return example


def _example_array(
    schema: dict[str, Any],
    depth: int,
    max_depth: int,
    max_properties: int,
) -> list[Any]:
    """Generate an example value from an array schema."""
    items = schema.get("items", {})
    if isinstance(items, dict) and items:
        return [_schema_to_example(items, depth, max_depth, max_properties)]
    return []


def _example_string(schema: dict[str, Any], prop_name: str | None = None) -> str:
    """Generate an example value from a string schema (respects format, enum, property name)."""
    fmt = schema.get("format")
    if fmt == "date-time":
        return "2024-01-01T00:00:00Z"
    if fmt == "email":
        return "user@example.com"
    if fmt == "uri":
        return "https://example.com"
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        return str(enum_vals[0])
    mapped = _lookup_string_example(prop_name)
    if mapped is not None:
        return mapped
    return "example"


def _schema_to_example(  # noqa: PLR0911, PLR0912
    schema: dict[str, Any],
    depth: int = 0,
    max_depth: int = 3,
    max_properties: int = 15,
    prop_name: str | None = None,
) -> Any:
    """Generate an example value from any JSON schema (recursive)."""
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict) and get_schema_type(opt) != "null":
                    return _schema_to_example(
                        opt, depth, max_depth, max_properties, prop_name=prop_name
                    )

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for t in schema_type:
            if t != "null":
                schema_type = t
                break
        else:
            schema_type = "null"

    if "example" in schema:
        return schema["example"]

    if schema_type == "object":
        return _example_object(schema, depth, max_depth, max_properties)
    if schema_type == "array":
        return _example_array(schema, depth, max_depth, max_properties)
    if schema_type == "string":
        return _example_string(schema, prop_name=prop_name)
    if schema_type in ("integer", "number", "boolean", "null"):
        return {"integer": 0, "number": 0.0, "boolean": True, "null": None}[schema_type]
    return None


def schema_to_compact_example(  # noqa: PLR0911, PLR0912
    schema: dict[str, Any],
    at_root: bool = True,
    prop_name: str | None = None,
    openapi_spec: OpenAPISpec | None = None,
) -> Any:
    """Generate a compact type-summary from a schema.

    ``at_root`` is the payload/relation distinction, mirroring
    :func:`~gitea_mcp_server.format.collapse_data`:

    - **payload** (``at_root``) — a ``$ref`` is resolved one level (via
      :func:`~gitea_mcp_server.format.resolve_ref_chain`) so the agent sees
      actual field names; a root array's item ``$ref`` is resolved the same
      way so items are summarized.
    - **relation** (below the payload) — a ``$ref`` is represented by the
      canonical marker ``{"$ref": "TypeName"}`` (built by
      :func:`~gitea_mcp_server.marker.ref_marker`), and a nested list of
      ``$ref`` items by ``{"$ref": "TypeName", "count": 1}`` — the same shapes
      the concise collapse produces (#763).

    An unresolvable root-array item type yields an empty list, never an array
    of content-free markers.

    The markdown formatter recognises the marker via ``is_ref_marker`` and
    renders it as ``$ref:TypeName``.  All properties are included (no
    ``max_properties`` truncation).  Leaf types use the same meaningful
    example values as ``_schema_to_example``.

    Designed to be called on the **raw** (unresolved) schema so that ``$ref``
    pointers are encountered naturally and serve as stop-recursion markers.

    Args:
        schema: A JSON Schema dict - ideally pre-resolution (``$ref`` intact).
        at_root: ``True`` for the payload (resolve a root ``$ref``); ``False``
            below it (a ``$ref`` becomes a marker).  Defaults to ``True``.
        prop_name: Property name hint for string example generation.
        openapi_spec: Post-conversion OpenAPI 3.1 spec.  At the payload, the
            root ``$ref`` chain (or a root array's item ``$ref``) is resolved
            to a concrete schema; ``None`` leaves it unresolved.

    Returns:
        A compact representation: the ``{"$ref": "TypeName"}`` marker for
        relations (with ``count`` for a nested list), example values for leaf
        types, dicts/arrays with one level of nesting.
    """
    # A payload $ref is resolved so agents see actual fields; a relation $ref
    # is represented by the canonical marker.
    if "$ref" in schema and isinstance(schema.get("$ref"), str):
        if at_root:
            resolved = resolve_ref_chain(schema, openapi_spec)
            if resolved is not None:
                return schema_to_compact_example(
                    resolved, at_root, prop_name=prop_name, openapi_spec=openapi_spec
                )
            # Fall through to the marker if resolution fails
        return ref_marker(schema["$ref"].rsplit("/", 1)[-1])

    # anyOf/oneOf - pick first non-null option
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict) and get_schema_type(opt) != "null":
                    return schema_to_compact_example(
                        opt, at_root, prop_name=prop_name, openapi_spec=openapi_spec
                    )

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for t in schema_type:
            if t != "null":
                schema_type = t
                break
        else:
            schema_type = "null"

    if "example" in schema:
        return schema["example"]

    if schema_type == "object":
        properties = schema.get("properties", {})
        if not properties:
            return "{...}"
        result: dict[str, Any] = {}
        for prop_name_inner, prop_schema in properties.items():
            if isinstance(prop_schema, dict):
                result[prop_name_inner] = schema_to_compact_example(
                    prop_schema,
                    False,
                    prop_name=prop_name_inner,
                    openapi_spec=openapi_spec,
                )
        return result

    if schema_type == "array":
        items = schema.get("items", {})
        if not (isinstance(items, dict) and items):
            return []
        type_name = extract_type_name(items)
        if type_name and not at_root:
            # A nested list of ``$ref`` items is a collapsed relation: the
            # marker with the example cardinality (one shown element).
            return ref_marker(type_name, 1)
        if type_name and at_root:
            # The root array is the payload.  Resolve the item ``$ref`` one
            # level to summarize it; an unresolvable item type yields no
            # example item rather than an array of content-free markers (#763).
            resolved = resolve_ref_chain(items, openapi_spec)
            if resolved is None:
                return []
            items = resolved
        # An array's items are payload items exactly when the array is.
        return [schema_to_compact_example(items, at_root, openapi_spec=openapi_spec)]

    if schema_type == "string":
        return _example_string(schema, prop_name=prop_name)

    if schema_type in ("integer", "number", "boolean", "null"):
        return {"integer": 0, "number": 0.0, "boolean": True, "null": None}[schema_type]
    return None


def serialize_tool_schema(
    tool: Tool,
    openapi_spec: OpenAPISpec | None = None,
) -> ToolSchemaResult:
    """Serialize a Tool to a compact dict (name, description, parameters, examples, annotations).

    Generates a compact ``output_example`` using the unresolved schema stored
    in ``tool.meta`` (if available) so nested ``$ref`` types show as
    ``{"$ref": "TypeName"}`` instead of inlined schemas.  Falls back to
    ``schema_to_compact_example`` on the resolved schema when the raw
    schema is absent (though this path is rarely taken in practice).

    When ``openapi_spec`` is provided, bare ``$ref`` at the top level of the
    schema will be resolved one level so agents see the type's actual fields
    instead of just a marker for the type name.
    """
    data: ToolSchemaResult = {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.parameters,
    }
    if tool.output_schema is not None:
        # Prefer the raw (unresolved) schema from meta for compact examples.
        # output_schema_raw stores the inner (unwrapped) schema
        # (see mcp_builder._customize_metadata where unwrap_result_schema
        # is applied), so it matches the shape of the tool output data
        # directly — no unwrapping needed.
        raw = (tool.meta or {}).get("output_schema_raw")
        if raw is not None:
            example = schema_to_compact_example(raw, openapi_spec=openapi_spec)
        else:
            # Fallback: generate from the resolved output schema.
            # The resolved schema has no $ref pointers, so openapi_spec
            # won't trigger ref resolution, but passing it through keeps
            # the code path consistent and future-proof.
            # Unwrap the result envelope to get the inner schema.
            inner = unwrap_result_schema(tool.output_schema) or {}
            example = schema_to_compact_example(inner, openapi_spec=openapi_spec)
        # Guard against null results (e.g. empty-body endpoints where the
        # schema has ``type: null`` and ``schema_to_compact_example``
        # returns ``None``).  Silently omit the field — agents can infer
        # from ``output_schema`` that the result is null.
        if example is not None:
            data["output_example"] = example
    if tool.annotations:
        ann = tool.annotations
        data["annotations"] = {
            k: getattr(ann, k)
            for k in ("title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
            if getattr(ann, k, None) is not None
        }
    if tool.tags:
        data["tags"] = list(tool.tags)
    if tool.version:
        data["version"] = tool.version
    return data


__all__ = [
    "schema_to_compact_example",
    "serialize_tool_schema",
]
