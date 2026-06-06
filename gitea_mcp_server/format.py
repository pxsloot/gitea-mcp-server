"""General-purpose schema-aware formatters for tools and resources.

Shared formatting utilities used across tools/ and resources/.
Kept at the flat level so neither domain depends on the other.
"""

import json as json_module
from datetime import datetime
from typing import Any


def _snake_to_title(name: str) -> str:
    """Convert snake_case or CamelCase to Title Case with spaces."""
    result = ""
    for i, ch in enumerate(name):
        if ch == "_":
            result += " "
        elif ch.isupper() and i > 0 and name[i - 1].islower():
            result += " " + ch
        elif ch.isupper() and i > 0 and name[i - 1] == " ":
            result += ch.lower()
        else:
            result += ch
    return result.strip().title()


def _format_datetime(dt: str | None) -> str:
    """Format datetime string to human-readable format."""
    if not dt:
        return "N/A"
    try:
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return dt


def _format_scalar(value: Any, schema: dict[str, Any] | None = None) -> str:
    """Format a scalar value as a string, respecting schema format hints."""
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return str(value)
    fmt = schema.get("format") if schema else None
    if fmt == "date-time":
        return _format_datetime(value)
    return value


def _format_simple_value(value: Any) -> str:
    """Format any value as a string (lists, dicts, scalars) without schema."""
    if value is None:
        return "N/A"
    if isinstance(value, list):
        parts = [_format_simple_value(v) for v in value]
        return ", ".join(parts)
    if isinstance(value, dict):
        return json_module.dumps(value, indent=2)
    return str(value)


def _format_list_as_markdown(
    data: list[Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
) -> str:
    lines: list[str] = []
    item_schema = schema.get("items") if isinstance(schema, dict) else None
    if not data:
        lines.append(f"{indent}*None*")
    elif data and isinstance(data[0], dict):
        for i, item in enumerate(data):
            sub = _format_as_markdown(
                item, item_schema, title=f"Item {i + 1}", _depth=0
            )
            lines.append(sub)
    elif item_schema and item_schema.get("type") in ("string", "number", "integer", "boolean"):
        items = [_format_scalar(v, item_schema) for v in data]
        lines.append(f"{indent}{', '.join(items)}")
    else:
        for item in data:
            sub = _format_simple_value(item)
            lines.append(f"{indent}- {sub}")
    return "\n".join(lines)


def _merge_allof_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not schema or "allOf" not in schema:
        return schema
    merged: dict[str, Any] = {"properties": {}}
    for sub_schema in schema["allOf"]:
        if isinstance(sub_schema, dict):
            sub_props = sub_schema.get("properties", {})
            if isinstance(sub_props, dict):
                merged["properties"].update(sub_props)
    return merged


def _resolve_anyof_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve anyOf/oneOf to the first object variant with properties."""
    if not schema:
        return None
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for sub in variants:
                if isinstance(sub, dict) and sub.get("type") == "object" and sub.get("properties"):
                    return sub
    return schema


def _render_flat_table(
    lines: list[str], flat: list[tuple[str, str]], indent: str
) -> None:
    lines.append(f"{indent}| Property | Value |")
    lines.append(f"{indent}|----------|-------|")
    for label, val in flat:
        escaped = val.replace("|", "\\|")
        lines.append(f"{indent}| {label} | {escaped} |")
    lines.append("")


def _render_nested_sections(
    lines: list[str], nested: list[tuple[str, str]], indent: str, _depth: int
) -> None:
    for label, sub in nested:
        if _depth == 0:
            lines.append(f"## {label}")
        else:
            lines.append(f"{indent}**{label}:**")
        lines.append("")
        lines.append(sub)
        lines.append("")


def _format_dict_as_markdown(
    data: dict[str, Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    _depth: int = 0,
) -> str:
    lines: list[str] = []
    combined_schema = _merge_allof_schema(schema)
    properties = (
        combined_schema.get("properties", {})
        if combined_schema and isinstance(combined_schema, dict)
        else {}
    )

    if not data:
        lines.append(f"{indent}*Empty*")
    elif properties:
        flat: list[tuple[str, str]] = []
        nested: list[tuple[str, str]] = []

        for key, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                continue
            effective = _resolve_anyof_schema(prop_schema)
            label = _snake_to_title(key)
            raw_val = data.get(key)
            is_nested = isinstance(raw_val, (dict, list))
            if is_nested:
                sub = _format_as_markdown(raw_val, effective or prop_schema, _depth=_depth + 1)
                if sub.strip():
                    nested.append((label, sub))
            else:
                formatted = _format_scalar(raw_val, prop_schema)
                flat.append((label, formatted))

        _render_flat_table(lines, flat, indent)
        _render_nested_sections(lines, nested, indent, _depth)
    else:
        _render_flat_table(lines, [(key, _format_simple_value(val)) for key, val in data.items()], indent)

    return "\n".join(lines)


def _format_as_markdown(
    data: Any,
    schema: dict[str, Any] | None = None,
    title: str | None = None,
    _depth: int = 0,
) -> str:
    lines: list[str] = []
    indent = "  " * _depth

    if title and _depth == 0:
        lines.append(f"# {title}")
        lines.append("")

    if data is None:
        lines.append(f"{indent}N/A")
        return "\n".join(lines)

    if isinstance(data, list):
        result = _format_list_as_markdown(data, schema, indent)
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    if isinstance(data, dict):
        result = _format_dict_as_markdown(data, schema, indent, _depth)
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    lines.append(f"{indent}{_format_scalar(data, schema)}")
    return "\n".join(lines)


__all__ = [
    "_format_as_markdown",
    "_format_datetime",
    "_format_scalar",
    "_format_simple_value",
    "_resolve_anyof_schema",
    "_snake_to_title",
]
