"""Schema-to-example and tool serialization utilities."""

from typing import Any

from fastmcp.tools.base import Tool


def _example_object(
    schema: dict[str, Any],
    depth: int,
    max_depth: int,
    max_properties: int,
) -> dict[str, Any]:
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
        )
    return example


def _example_array(
    schema: dict[str, Any],
    depth: int,
    max_depth: int,
    max_properties: int,
) -> list[Any]:
    items = schema.get("items", {})
    if isinstance(items, dict) and items:
        return [_schema_to_example(items, depth, max_depth, max_properties)]
    return []


def _example_string(schema: dict[str, Any]) -> str:
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
    return "text"


def _schema_to_example(
    schema: dict[str, Any],
    depth: int = 0,
    max_depth: int = 3,
    max_properties: int = 15,
) -> Any:
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict) and opt.get("type") != "null":
                    return _schema_to_example(opt, depth, max_depth, max_properties)

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for t in schema_type:
            if t != "null":
                schema_type = t
                break
        else:
            schema_type = "null"
    if not isinstance(schema_type, str):
        schema_type = None

    if "example" in schema:
        return schema["example"]

    _dispatch: dict[str, Any] = {
        "object": lambda: _example_object(schema, depth, max_depth, max_properties),
        "array": lambda: _example_array(schema, depth, max_depth, max_properties),
        "string": lambda: _example_string(schema),
        "integer": lambda: 0,
        "number": lambda: 0.0,
        "boolean": lambda: True,
        "null": lambda: None,
    }
    if schema_type is None:
        return None
    handler = _dispatch.get(schema_type)
    if handler is not None:
        return handler()
    return None


def _serialize_tool_schema(tool: Tool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.parameters,
    }
    if tool.output_schema is not None:
        inner = tool.output_schema.get("properties", {}).get("result", {})
        data["output_example"] = _schema_to_example(inner)
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
    "_example_array",
    "_example_object",
    "_example_string",
    "_schema_to_example",
    "_serialize_tool_schema",
]
