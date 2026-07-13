"""General-purpose schema-aware formatters for tools and resources.

Shared formatting utilities used across tools/ and resources/.
Kept at the flat level so neither domain depends on the other.

Public functions:
    format_result - reformat a ToolResult by format (json/markdown/raw).
"""

import json as json_module
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.pagination import PAGINATION_KEYS

logger = logging.getLogger(__name__)

# Length bounds for auto-detecting ISO datetime strings without schema hint
_ISO_DT_MIN_LEN = 20
_ISO_DT_MAX_LEN = 30


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
    if not isinstance(value, str):
        return str(value)
    fmt = schema.get("format") if schema else None
    if fmt == "date-time":
        return _format_datetime(value)
    # Auto-format ISO datetime strings even without schema hint
    if _ISO_DT_MIN_LEN <= len(value) <= _ISO_DT_MAX_LEN and "T" in value:
        formatted = _format_datetime(value)
        if formatted != value:
            return formatted
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
    field_filter: Sequence[str] | None = None,
    item_title_key: str | None = None,
) -> str:
    lines: list[str] = []
    item_schema = schema.get("items") if isinstance(schema, dict) else None
    if not data:
        lines.append(f"{indent}*None*")
    # Flatten lists of {"$ref": "Type"} - render as bulleted $ref:X items.
    elif data and all(isinstance(v, dict) and set(v.keys()) == {"$ref"} for v in data):
        items = [f"$ref:{v['$ref']}" for v in data]
        for item in items:
            lines.append(f"{indent}- {item}")
    elif data and isinstance(data[0], dict):
        for i, item in enumerate(data):
            title: str | None = None
            if item_title_key:
                val = item.get(item_title_key)
                if val is not None:
                    title = str(val)
            if title is None:
                title = f"Item {i + 1}"
            sub = _format_as_markdown(
                item,
                item_schema,
                title=title,
                _depth=0,
                field_filter=field_filter,
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


def _render_flat_table(lines: list[str], flat: list[tuple[str, str]], indent: str) -> None:
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
    field_filter: Sequence[str] | None = None,
) -> str:
    lines: list[str] = []
    combined_schema = _merge_allof_schema(schema)
    properties = (
        combined_schema.get("properties", {})
        if combined_schema and isinstance(combined_schema, dict)
        else {}
    )

    # Determine which keys to iterate
    if field_filter is not None:
        keys = [k for k in field_filter if k in data]
    elif properties:
        keys = list(properties.keys())
    else:
        keys = list(data.keys())

    if not data:
        lines.append(f"{indent}*Empty*")
    elif keys:
        flat: list[tuple[str, str]] = []
        nested: list[tuple[str, str]] = []

        for key in keys:
            prop_schema = properties.get(key) if properties else None
            if prop_schema is not None and not isinstance(prop_schema, dict):
                continue
            effective = _resolve_anyof_schema(prop_schema) if prop_schema else None
            label = _snake_to_title(key)
            raw_val = data.get(key)
            # Flatten {"$ref": "TypeName"} to "$ref:TypeName" for markdown
            # tables - keeps the display compact while signalling that the
            # value is a component reference, not a literal string.
            if isinstance(raw_val, dict) and set(raw_val.keys()) == {"$ref"}:
                raw_val = f"$ref:{raw_val['$ref']}"
            is_nested = isinstance(raw_val, (dict, list))
            if is_nested:
                # Don't propagate field_filter into nested sub-objects -
                # the parent's field names don't apply to child objects.
                sub = _format_as_markdown(
                    raw_val,
                    effective or prop_schema,
                    _depth=_depth + 1,
                )
                if sub.strip():
                    nested.append((label, sub))
            else:
                formatted = _format_scalar(raw_val, prop_schema)
                flat.append((label, formatted))

        _render_flat_table(lines, flat, indent)
        _render_nested_sections(lines, nested, indent, _depth)
    else:
        _render_flat_table(
            lines, [(key, _format_simple_value(val)) for key, val in data.items()], indent
        )

    return "\n".join(lines)


def _format_as_markdown(
    data: Any,
    schema: dict[str, Any] | None = None,
    title: str | None = None,
    _depth: int = 0,
    field_filter: Sequence[str] | None = None,
    item_title_key: str | None = None,
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
        result = _format_list_as_markdown(
            data,
            schema,
            indent,
            field_filter=field_filter,
            item_title_key=item_title_key,
        )
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    if isinstance(data, dict):
        result = _format_dict_as_markdown(data, schema, indent, _depth, field_filter=field_filter)
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    lines.append(f"{indent}{_format_scalar(data, schema)}")
    return "\n".join(lines)


def format_result(
    result: ToolResult,
    fmt: str,
    output_schema: dict[str, Any] | None = None,
) -> ToolResult:
    """Reformat a ``ToolResult`` content by ``fmt`` (``json`` / ``markdown`` / ``raw``).

    ``structured_content`` is always preserved as raw data.
    For non-JSON or binary results, all formats return unchanged.
    """
    if fmt == "raw" or not result.structured_content:
        return result

    data = result.structured_content.get("result")
    if data is None:
        return result

    content: str | None = None

    if fmt == "json":
        content = json_module.dumps(data, indent=2)

    elif fmt == "markdown" and isinstance(data, (dict, list)):
        inner = output_schema.get("properties", {}).get("result", {}) if output_schema else None
        content = _format_as_markdown(data, inner)

        pagination = {
            k: result.structured_content[k]
            for k in PAGINATION_KEYS
            if k in result.structured_content
        }
        if pagination:
            content += "\n\n---\n"
            content += _format_as_markdown(pagination, None)

    else:
        # Intentional pass-through: string results (e.g. diff/patch text) and
        # other non-dict/list types are returned unchanged in markdown mode.
        # Log so the no-op is observable during debugging (see #442 Finding 3).
        logger.debug(
            "format_result: skipping formatting for fmt=%s, data type=%s "
            "(returned unchanged)",
            fmt,
            type(data).__name__,
        )

    if content is not None:
        return ToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content=result.structured_content,
            meta=result.meta,
        )

    return result


__all__ = [
    "_format_as_markdown",
    "_format_datetime",
    "_format_scalar",
    "_format_simple_value",
    "_resolve_anyof_schema",
    "_snake_to_title",
    "format_result",
]
