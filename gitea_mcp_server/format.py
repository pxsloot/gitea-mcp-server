"""General-purpose schema-aware formatters for tools and resources.

Shared formatting utilities used across tools/ and resources/.
Kept at the flat level so neither domain depends on the other.

Public functions:
    _build_server_info_markdown - build server info markdown from
        the OpenAPI spec info block (not a registered domain formatter).
    _collapse_data - walk data+schema, collapse $ref-backed objects at depth>=1
        to labels (``$ref:TypeName``).  Used to shape data before formatting
        so any formatter (json or markdown) receives already-collapsed data.
    apply_format - format data for output (raw/json/markdown), no pagination.
    _format_paginated_result - format paginated list results for display.
        Separates display from data creation: handles page slicing (or
        ``fetch_all`` skip), formatting, and pagination metadata.  Preferred
        over manual ``apply_format()`` + ``apply_pagination()`` composition.
    format_result - reformat a ToolResult by format (json/markdown/raw).
        Prefer ``apply_format`` for new code; ``format_result`` is kept for
        backward compatibility (used by the API tool wrapping transform which
        needs to preserve pagination metadata and ``meta`` from the original
        ``ToolResult``).
    _format_tool_info_markdown - format a ToolSchemaResult as parseable markdown.
    _format_parameter_table - render a JSON Schema parameter table.
    _format_annotations_table - render an annotations table.
    _format_json_section - render a JSON code block section.
"""

from __future__ import annotations

import json as json_module
import logging
from collections.abc import (  # noqa: TC003 - used at runtime, not just type checking
    Callable,
    Sequence,
)
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

if TYPE_CHECKING:
    from gitea_mcp_server.models import ToolSchemaResult
    from gitea_mcp_server.openapi_types import OpenAPISpec

# Note: PAGINATION_KEYS is imported lazily inside format_result() to avoid
# a module-level coupling that only the deprecated function needs.

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


def _extract_type_name(schema: dict[str, Any] | None) -> str | None:
    """Extract a type name from a schema dict via ``$ref``.

    Checks the schema itself and any ``anyOf``/``oneOf``/``allOf``
    options for a ``$ref`` pointer.  Returns the last path segment (the
    type name) or ``None`` if no ``$ref`` is found.

    Args:
        schema: A JSON Schema fragment (may be ``None``).

    Returns:
        The type name (e.g. ``"Repository"``) or ``None``.
    """
    if not schema:
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict):
                    ref = opt.get("$ref")
                    if isinstance(ref, str):
                        return ref.rsplit("/", 1)[-1]
    return None


def _collapse_data(  # noqa: PLR0911 - 7 returns: 2 guard clauses (full detail, no schema), 2 collapse outcomes (dict $ref, list $ref), 2 recursive walks (dict recurse, list recurse), 1 scalar passthrough
    data: Any,
    schema: dict[str, Any] | None = None,
    _depth: int = 0,
    detail: str = "full",
) -> Any:
    """Walk data+schema, collapsing $ref-backed objects at depth>=1 to labels.

    Used to shape data before formatting: when ``detail="concise"`` and
    ``_depth >= 1``, properties whose schema declares a ``$ref`` are
    collapsed to ``"$ref:TypeName"`` (or ``"$ref:TypeName[N]"`` for lists).
    Inline schemas (no ``$ref``) are NOT collapsed — they remain as nested
    dicts/lists for the formatter to render.

    When ``schema`` is ``None`` or ``detail="full"``, the data is returned
    unchanged (the tree is still walked for ``schema=None``, but no
    collapsing occurs).

    Args:
        data: The data to collapse (dict, list, or scalar).
        schema: The JSON Schema describing *data*, or ``None``.
        _depth: Current nesting depth — 0 means top-level (never collapsed).
        detail: ``"full"`` (return unchanged) or ``"concise"`` (collapse at depth>=1).

    Returns:
        Collapsed data (dicts, lists, strings) suitable for JSON serialization
        or markdown rendering.
    """
    if detail == "full":
        return data

    # Without schema context there is nothing to collapse — the data
    # tree stays as-is.  This avoids unnecessary dict/list copies.
    if schema is None:
        return data

    if isinstance(data, dict):
        if _depth >= 1:
            # Use raw schema — _extract_type_name natively handles
            # $ref, allOf, anyOf, oneOf at the top level.
            type_name = _extract_type_name(schema)
            if type_name:
                return f"$ref:{type_name}"
            # No $ref — recurse (inline schemas stay expanded)

        combined = _merge_allof_schema(schema)
        properties = combined.get("properties", {}) if isinstance(combined, dict) else {}

        result: dict[str, Any] = {}
        for k, v in data.items():
            prop_schema = properties.get(k) if properties else None
            if prop_schema is not None and not isinstance(prop_schema, dict):
                prop_schema = None
            effective = _resolve_anyof_schema(prop_schema) if prop_schema else None
            result[k] = _collapse_data(v, effective or prop_schema, _depth + 1, detail)
        return result

    if isinstance(data, list):
        if _depth >= 1:
            items_schema = schema.get("items", {}) if isinstance(schema, dict) else {}
            type_name = _extract_type_name(items_schema)
            if type_name:
                return f"$ref:{type_name}[{len(data)}]"
            # No $ref — recurse

        items_schema = schema.get("items", {}) if isinstance(schema, dict) else {}
        return [_collapse_data(item, items_schema, _depth + 1, detail) for item in data]

    return data


def _collapse_value(raw_val: Any, prop_schema: dict[str, Any] | None) -> str:
    """Render a nested runtime value as a compact type reference string.

    Used when ``detail="concise"`` and the value sits at ``_depth >= 1``.
    Extracts the type name from the schema where possible, otherwise
    falls back to a generic placeholder.

    Args:
        raw_val: The runtime value (dict or list).
        prop_schema: The JSON Schema describing *raw_val*, or ``None``.

    Returns:
        A compact string such as ``"$ref:Repository"`` or ``"$ref:Label[3]"``.
    """
    if isinstance(raw_val, dict):
        type_name = _extract_type_name(prop_schema)
        if type_name:
            return f"$ref:{type_name}"
        return "{...}"
    if isinstance(raw_val, list):
        items_schema = prop_schema.get("items", {}) if isinstance(prop_schema, dict) else {}
        type_name = _extract_type_name(items_schema)
        count = len(raw_val)
        if type_name:
            return f"$ref:{type_name}[{count}]"
        return f"[{count} items]"
    return str(raw_val)


def _format_list_as_markdown(  # noqa: PLR0913 - 6 params justified: data, schema, indent, field_filter, item_title_key, detail
    data: list[Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    field_filter: Sequence[str] | None = None,
    item_title_key: str | None = None,
    detail: str = "full",
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
                detail=detail,
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


def _format_dict_as_markdown(  # noqa: PLR0912 - 14 branches justified: scalar/nested, detail, field_filter, allOf, anyOf
    data: dict[str, Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    _depth: int = 0,
    field_filter: Sequence[str] | None = None,
    detail: str = "full",
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
                if detail == "concise" and _depth >= 1:
                    # Collapse nested objects to compact type references
                    collapsed = _collapse_value(raw_val, prop_schema or effective)
                    flat.append((label, collapsed))
                else:
                    # Don't propagate field_filter into nested sub-objects -
                    # the parent's field names don't apply to child objects.
                    sub = _format_as_markdown(
                        raw_val,
                        effective or prop_schema,
                        _depth=_depth + 1,
                        detail=detail,
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


def _format_as_markdown(  # noqa: PLR0913 - 7 params justified: data, schema, title, _depth, field_filter, item_title_key, detail
    data: Any,
    schema: dict[str, Any] | None = None,
    title: str | None = None,
    _depth: int = 0,
    field_filter: Sequence[str] | None = None,
    item_title_key: str | None = None,
    detail: str = "full",
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
            detail=detail,
        )
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    if isinstance(data, dict):
        result = _format_dict_as_markdown(
            data, schema, indent, _depth, field_filter=field_filter, detail=detail,
        )
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    lines.append(f"{indent}{_format_scalar(data, schema)}")
    return "\n".join(lines)


# ============================================================================
# Tool info markdown formatters (used by tool_info synthetic tool)
# ============================================================================


def _format_type(prop: dict[str, Any]) -> str:
    """Build a user-friendly type string with optional enum or array-item details.

    Enriches the type column of the parameter table so agents can see structural
    information without a second lookup:

    - Enum parameters append allowed values: ``string [merge, rebase, squash]``
    - Array parameters with known item properties list them: ``array of {operation, path, content}``

    Args:
        prop: The JSON Schema property dict for a single parameter.

    Returns:
        A human-readable type string.
    """
    ptype = str(prop.get("type", "any"))

    # 1. Enum — append allowed values right in the type column
    enum_vals = prop.get("enum")
    if enum_vals:
        vals = ", ".join(str(v) for v in enum_vals)
        return f"{ptype} [{vals}]"

    # 2. Array with known item shape — list key property names
    if ptype == "array":
        items = prop.get("items")
        if isinstance(items, dict):
            item_props = items.get("properties")
            if isinstance(item_props, dict):
                keys = ", ".join(item_props.keys())
                return f"array of {{{keys}}}"

    return ptype


def _format_parameter_table(properties: dict[str, Any], required: list[str]) -> str:
    """Render a parameter table from JSON Schema properties."""
    lines = [
        "## Parameters",
        "",
        "| Parameter | Type | Required | Description |",
        "|-----------|------|----------|-------------|",
    ]
    for param_name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        ptype = _format_type(prop)
        preq = "yes" if param_name in required else "no"
        pdesc = prop.get("description", "").replace("|", "\\|")
        lines.append(f"| {param_name} | {ptype} | {preq} | {pdesc} |")
    lines.append("")
    return "\n".join(lines)


def _format_annotations_table(annotations: dict[str, Any]) -> str:
    """Render an annotations table."""
    lines = ["## Annotations", "", "| Hint | Value |", "|------|-------|"]
    for key in ("title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
        val = annotations.get(key)
        if val is not None:
            lines.append(f"| {key} | {json_module.dumps(val)} |")
    lines.append("")
    return "\n".join(lines)


def _format_json_section(title: str, data: Any) -> str:
    """Render a JSON code block section."""
    return f"## {title}\n\n```json\n{json_module.dumps(data, indent=2)}\n```\n"


def _format_tool_info_markdown(schema: ToolSchemaResult) -> str:
    """Format a ``ToolSchemaResult`` as parseable, consistent markdown.

    Produces a predictable structure with a parameter table that agents can
    parse reliably:

    - ``## Parameters`` — table with ``Parameter | Type | Required | Description``
    - ``## Output Example`` — JSON code block
    - ``## Annotations`` — table with ``Hint | Value``
    - ``## Tags`` — comma-separated list
    - ``## Output Schema`` — JSON code block (only when ``output_schema`` present)
    """
    lines: list[str] = []

    name = schema.get("name", "")
    if name:
        lines.append(f"# {name}")
        lines.append("")

    desc = schema.get("description", "")
    if desc:
        lines.append(desc)
        lines.append("")

    params = schema.get("parameters", {})
    if isinstance(params, dict):
        properties = params.get("properties", {})
        if properties:
            lines.append(_format_parameter_table(properties, params.get("required", [])))

    example = schema.get("output_example")
    if example is not None:
        lines.append(_format_json_section("Output Example", example))

    annotations = schema.get("annotations")
    if isinstance(annotations, dict):
        lines.append(_format_annotations_table(annotations))

    tags = schema.get("tags")
    if tags:
        lines.append("## Tags\n")
        lines.append(", ".join(tags))
        lines.append("")

    output_schema = schema.get("output_schema")
    if isinstance(output_schema, dict):
        lines.append(_format_json_section("Output Schema", output_schema))

    return "\n".join(lines).strip()


def apply_format(  # noqa: PLR0913 - 2 required (data, fmt) + 4 keyword-only display options (markdown_formatter, markdown_extras, detail, schema) — all independent display axes
    data: Any,
    fmt: str,
    *,
    markdown_formatter: Callable[[Any], str] | None = None,
    markdown_extras: list[str] | None = None,
    detail: str = "full",
    schema: dict[str, Any] | None = None,
) -> ToolResult:
    """Format data for output. No pagination involvement.

    Produces a ``ToolResult`` with ``structured_content`` carrying the raw
    data (``{"result": data}``) and ``content`` formatted per ``fmt``:

    - ``raw``: structured_content only, no text content.
    - ``json``: text = JSON dump, structured_content = ``{"result": data}``.
    - ``markdown``: text = ``markdown_formatter(data)`` or the generic
      ``_format_as_markdown``.  ``markdown_extras`` are appended
      as additional sections after the main content.

    When ``detail="concise"`` and ``schema`` is provided, data is collapsed
    before formatting — nested ``$ref``-backed objects are replaced with
    ``"$ref:TypeName"`` labels (applies to both json and markdown).

    Args:
        data: The data to format (typically a dict or list).
        fmt: Output format — ``"raw"``, ``"json"``, or ``"markdown"``.
        markdown_formatter: Optional custom markdown renderer.  When omitted,
            the generic ``_format_as_markdown`` is used.
        markdown_extras: Optional list of additional markdown sections to
            append after the main content (only in markdown mode).
        detail: Output detail level — ``"full"`` (default, complete) or
            ``"concise"`` (collapse nested ``$ref`` objects).
        schema: Optional JSON Schema describing *data*, used for schema-aware
            collapsing when ``detail="concise"``.

    Returns:
        A ``ToolResult`` with formatted content and raw structured data.
    """
    _VALID_FORMATS = frozenset({"raw", "json", "markdown"})
    if fmt not in _VALID_FORMATS:
        msg = f"Unsupported format '{fmt}'. Use 'markdown', 'json', or 'raw'."
        raise ValueError(msg)

    if fmt == "raw":
        return ToolResult(structured_content={"result": data})

    if fmt == "json":
        if detail == "concise" and schema is not None:
            data = _collapse_data(data, schema, _depth=0, detail="concise")
        text = json_module.dumps(data, indent=2)
    else:
        text = (
            markdown_formatter(data)
            if markdown_formatter
            else _format_as_markdown(data, schema, detail=detail)
        )
        if markdown_extras:
            text += "\n\n---\n\n" + "\n\n---\n\n".join(markdown_extras)

    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"result": data},
    )


def format_result(
    result: ToolResult,
    fmt: str,
    output_schema: dict[str, Any] | None = None,
    detail: str = "full",
    raw_schema: dict[str, Any] | None = None,
) -> ToolResult:
    """Reformat a ``ToolResult`` content by ``fmt`` (``json`` / ``markdown`` / ``raw``).

    ``structured_content`` is always preserved as raw data.
    For non-JSON or binary results, all formats return unchanged.

    When ``detail="concise"``, the data is collapsed before formatting:
    nested objects whose schema declares a ``$ref`` are replaced with
    ``"$ref:TypeName"`` labels.  For schema-aware collapse to work,
    the **unresolved** schema (``raw_schema``) is preferred because
    the resolved ``output_schema`` has ``$ref`` pointers expanded to
    inline definitions.

    .. note::
        Prefer ``apply_format`` for new code. ``format_result`` is kept for
        the API tool wrapping transform which needs to preserve pagination
        metadata and ``meta`` from the original ``ToolResult``.
    """
    # Deferred import to avoid module-level coupling: PAGINATION_KEYS is
    # only needed by this function (not by apply_format).
    from gitea_mcp_server.pagination import PAGINATION_KEYS  # noqa: PLC0415

    if fmt == "raw" or not result.structured_content:
        return result

    data = result.structured_content.get("result")
    if data is None:
        return result

    # Extract the resolved inner schema — used for markdown rendering and
    # as fallback for collapse.  This is ``output_schema.properties.result``.
    inner: dict[str, Any] | None = None
    if output_schema:
        inner = output_schema.get("properties", {}).get("result", {})

    # For $ref detection during collapse, prefer the UNRESOLVED raw schema.
    # The resolved output_schema has $ref pointers expanded to inline
    # definitions, so _extract_type_name cannot find type names there.
    collapse_inner: dict[str, Any] | None = None
    if raw_schema:
        collapse_inner = raw_schema.get("properties", {}).get("result", {})

    # Collapse data when detail=concise — shape the data tree, then
    # hand off to whichever formatter (json or markdown).  This
    # separates "what to show" from "how to render".
    if detail == "concise" and isinstance(data, (dict, list)):
        data = _collapse_data(data, collapse_inner or inner, _depth=0, detail="concise")

    content: str | None = None

    if fmt == "json":
        content = json_module.dumps(data, indent=2)

    elif fmt == "markdown" and isinstance(data, (dict, list)):
        # Data has already been collapsed for concise — the formatter
        # receives already-shaped data and does NOT need the detail param.
        # NB: `detail` is deliberately omitted here because:
        #   - For detail="concise", data was pre-collapsed by _collapse_data
        #     above — nested $ref dicts are now strings like "$ref:User",
        #     so _format_as_markdown renders them as scalars regardless of
        #     its own detail param.
        #   - For detail="full" (default), _format_as_markdown's own default
        #     matches. If _format_as_markdown's default ever changes, this
        #     call must pass detail=detail explicitly.
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


def _format_paginated_result(  # noqa: PLR0913 - all 7 params are independent display axes (items + pagination state + output config)
    items: list,
    total_count: int,
    fmt: str,
    page: int,
    limit: int,
    fetch_all: bool = False,
    markdown_extras: list[str] | None = None,
) -> ToolResult:
    """Format a paginated list result for display.

    Separates **display** from **data creation**.  Synthetic tool handlers
    produce data (items + total_count) and then call this utility to handle
    the display layer: page slicing (or everything when ``fetch_all=True``),
    formatting, and pagination metadata.

    When ``fetch_all=True`` the page/limit slice is skipped — all items are
    returned with ``has_more=False``.  When ``fetch_all=False`` (default),
    only the requested page of items is returned with proper pagination
    metadata (``has_more``, ``next_offset``, ``total_count``).

    Args:
        items: All matching items (before page slicing).
        total_count: Total number of matching items.
        fmt: Output format — ``"raw"``, ``"json"``, or ``"markdown"``.
        page: Page number to display (1-based).  Ignored when ``fetch_all``
            is True.
        limit: Items per page.  Ignored when ``fetch_all`` is True.
        fetch_all: When True, return all items without slicing (no loop
            needed — data is in-memory).
        markdown_extras: Optional extra markdown sections appended after
            the main content (only used in markdown mode).

    Returns:
        A ``ToolResult`` with formatted content and pagination metadata
        in ``structured_content``.
    """
    # Deferred import to avoid module-level coupling — apply_pagination is
    # only needed here (not by other formatting functions).
    from gitea_mcp_server.pagination import apply_pagination  # noqa: PLC0415

    if fetch_all:
        # Skip slicing — return everything.
        page, limit = 1, total_count or len(items)
        page_items = items
    else:
        start = (page - 1) * limit
        page_items = items[start : start + limit]

    return apply_pagination(
        apply_format(page_items, fmt, markdown_extras=markdown_extras),
        page,
        limit,
        total_count,
    )


def _build_server_info_markdown(openapi_spec: OpenAPISpec) -> str:
    """Build server info markdown from OpenAPI spec info block.

    Unlike registered domain formatters (which follow the
    ``(data, *, detail) -> str`` signature), this function takes the
    raw OpenAPI spec directly.  It lives in ``format.py`` rather than
    ``tools/display.py`` because it is not a registered formatter —
    it is a shared utility used by ``resources/custom.py``.
    """
    info = openapi_spec.get("info", {})
    title = info.get("title", "Unknown")
    version = info.get("version", "Unknown")
    description = info.get("description", "")
    lines = [
        "# Server Information",
        "",
        f"**Server Type**: {title}",
        f"**API Version**: {version}",
        "",
    ]
    if description:
        lines.append("## Description")
        lines.append("")
        lines.append(description)
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "_build_server_info_markdown",
    "_collapse_data",
    "_collapse_value",
    "_extract_type_name",
    "_format_annotations_table",
    "_format_as_markdown",
    "_format_datetime",
    "_format_json_section",
    "_format_paginated_result",
    "_format_parameter_table",
    "_format_scalar",
    "_format_simple_value",
    "_format_tool_info_markdown",
    "_format_type",
    "_resolve_anyof_schema",
    "apply_format",
    "format_result",
]
