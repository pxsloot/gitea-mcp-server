"""General-purpose schema-aware formatters for tools and resources.

Shared formatting utilities used across tools/ and resources/.
Kept at the flat level so neither domain depends on the other.

Public functions:
    build_server_info_markdown - build server info markdown from
        the OpenAPI spec info block (not a registered domain formatter).
    collapse_data - walk data+schema, replace $ref-backed objects at depth>=1
        with the canonical agent-facing ``$ref`` marker
        (``{"$ref": "TypeName"}``; ``{"$ref": "TypeName", "count": N}`` for a
        collapsed list).  Root-list items are *summarized* (scalars kept,
        nested refs marked), never replaced wholesale (resolved one level via
        the OpenAPI spec, #759).  Used to shape data before formatting so any
        formatter (json or markdown) receives already-collapsed data.
    decode_base64_content - decode base64 file content from a Gitea
    ContentsResponse (shared by tools and resources).
    format_tool_info_markdown - format a ToolSchemaResult as parseable markdown.
    _format_parameter_table - render a JSON Schema parameter table.
    _format_annotations_table - render an annotations table.
    _format_json_section - render a JSON code block section.

Formatter contract and registry:
    MarkdownFormatter - the display pipeline's markdown formatter type.  This
        module is its canonical home; other modules point here rather than
        restating the signature.  ``call_markdown_formatter`` is the single
        dispatch point.
    register_formatter / get_formatter / get_formatter_for_type - the formatter
        registry.  Domain formatters live in ``tools/display.py`` and register
        here; the result pipeline resolves through :func:`resolve_formatter`
        without importing the domain module (Result → Format, never Result →
        Display).
    resolve_formatter - the three-tier formatter choice (explicit per-result →
        type-bound domain formatter → generic), the single dispatch policy
        shared by every tool and resource.

The single result pipeline for tools and resources lives in
``tools/result_pipeline.py``; this module provides the shared formatting
primitives it builds on.
"""

from __future__ import annotations

import base64
import functools
import inspect
import json as json_module
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from gitea_mcp_server.marker import is_ref_marker, ref_marker, ref_marker_label
from gitea_mcp_server.openapi_converter.core import resolve_spec_ref
from gitea_mcp_server.schema_utils import (
    extract_type_name,
    extract_type_ref,
    get_schema_type,
)

if TYPE_CHECKING:
    from gitea_mcp_server.models import ToolSchemaResult
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)

# Length bounds for auto-detecting ISO datetime strings without schema hint

# ---------------------------------------------------------------------------
# Shared formatter dispatch
# ---------------------------------------------------------------------------

# The display pipeline's markdown formatter contract — the canonical home for
# this statement; other modules point here rather than restating it.
#
# A formatter is a *pure renderer*: it takes already-shaped data and returns
# markdown.  It may declare a keyword-only ``extra`` parameter (with a
# default) for formatter context; ``call_markdown_formatter`` inspects each
# signature and forwards ``extra`` only to formatters that declare it.  No
# other keyword-only parameter is dispatched.
#
# The type is deliberately ``Callable[..., str]`` — not a tighter
# ``Callable[[Any], str]`` or a ``Protocol``: formatters have heterogeneous
# signatures (``data`` positional, optional keyword-only ``extra``), and
# Python's type system cannot express "may declare this optional keyword"
# without rejecting the common ``def f(data)`` form.  The shape of the
# contract is therefore enforced at runtime by ``call_markdown_formatter``
# and locked by tests, not by the type itself.
#
# ``detail`` is deliberately NOT part of the contract: when ``detail="concise"``
# and a schema is available the pipeline pre-collapses the page, so formatters
# receive already-collapsed data.  Collapsed *fields* arrive as the canonical
# ``$ref`` marker (``{"$ref": "TypeName"}``, or with ``count`` for a collapsed
# list) and render through ``is_ref_marker``/``ref_marker_label``; collapsed
# *items* do not exist — root-list items are summarized (dicts with scalars
# intact and nested refs marked, #759), so formatters must not branch on
# item-level collapsed shapes.
MarkdownFormatter = Callable[..., str]


def _accepted_kwargs(fn: MarkdownFormatter) -> frozenset[str]:
    """Keyword-only params a formatter callable accepts.

    Formatters are pure renderers with heterogeneous signatures: most take
    only ``data``, some take ``extra`` (formatter context) — the contract is
    stated canonically on :data:`MarkdownFormatter`.

    The signature is inspected per call — ``inspect.signature`` is cheap
    (microseconds) next to the HTTP call and the formatting walk, and the
    callables handed to the pipeline are often fresh per call (lambdas,
    ``functools.partial``), so a cache keyed on the callable would never
    hit and would grow without bound on a long-lived server.
    """
    return frozenset(
        name
        for name, param in inspect.signature(fn).parameters.items()
        if param.kind == inspect.Parameter.KEYWORD_ONLY
    )


def call_markdown_formatter(
    fn: MarkdownFormatter,
    data: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> str:
    """Call a markdown formatter with only the kwargs it accepts.

    The single dispatch point for the display pipeline's ``markdown_formatter``
    contract.  Formatters declare only the keyword params they use — most take
    just ``data``, some take ``extra`` (formatter context) — and this helper
    inspects each signature to pass exactly the accepted kwargs.
    This keeps the pipeline's call site uniform while letting formatters drop
    dead params.  The contract itself — including why ``detail`` is not
    dispatched (the pipeline pre-collapses and formatters detect collapsed
    items by shape) — is stated canonically on :data:`MarkdownFormatter`.

    Only ``extra`` is dispatched among keyword-only params, so formatters
    should declare no other keyword-only param (and ``extra`` with a
    default) — a required keyword-only param other than ``extra`` would
    raise ``TypeError``.

    Args:
        fn: A :data:`MarkdownFormatter` — ``(data, *, extra?) -> str``.
        data: The (already-collapsed) data to render.
        extra: Formatter context; passed only if ``fn`` declares it.

    Returns:
        The rendered markdown string.
    """
    kwargs: dict[str, Any] = {}
    accepted = _accepted_kwargs(fn)
    if "extra" in accepted:
        kwargs["extra"] = extra
    return fn(data, **kwargs)


# ---------------------------------------------------------------------------
# Formatter registry and resolution
# ---------------------------------------------------------------------------
#
# The registry lives in the format layer (not in ``tools/display.py``) so the
# result pipeline can resolve a formatter without importing the domain
# formatter module: Result → Format → Display.  Domain formatters
# (``tools/display.py``) are pure plugins — they register here and hold no
# registry state.

_FORMATTERS: dict[str, MarkdownFormatter] = {}

# Response-schema type name -> formatter name.  Populated
# by ``register_formatter(types=...)``; consulted by :func:`resolve_formatter`
# as the middle tier between an explicit per-result formatter and the generic
# fallback.
_TYPE_FORMATTERS: dict[str, str] = {}


def register_formatter(
    name: str,
    *,
    types: Sequence[str] = (),
) -> Callable[[MarkdownFormatter], MarkdownFormatter]:
    """Decorator that registers a domain-specific markdown formatter.

    Domain formatters live in ``tools/display.py``; the registry itself lives
    here so the result pipeline depends on the format layer only.

    Args:
        name: Unique name used as ``format_hint`` in resource metadata.
        types: Response type names (e.g. ``"Issue"``) this formatter renders
            on the tool side.  The result pipeline binds a tool's markdown
            output to this formatter when the result's ``response_type``
            matches one of them.

    Usage::

        @register_formatter("repository", types=["Repository"])
        def _format_repo_markdown(data): ...
    """

    def deco(fn: MarkdownFormatter) -> MarkdownFormatter:
        _FORMATTERS[name] = fn
        for type_name in types:
            existing = _TYPE_FORMATTERS.get(type_name)
            if existing is not None and existing != name:
                logger.warning(
                    "Response type %r already bound to formatter %r; rebinding to %r",
                    type_name,
                    existing,
                    name,
                )
            _TYPE_FORMATTERS[type_name] = name
        return fn

    return deco


def get_formatter(name: str) -> MarkdownFormatter | None:
    """Look up a registered formatter by name.  Returns ``None`` if not found."""
    return _FORMATTERS.get(name)


def get_formatter_for_type(type_name: str) -> MarkdownFormatter | None:
    """Look up the formatter bound to a response type name (``types=``).

    Returns ``None`` for unbound types — callers fall back to the generic
    renderer (the pipeline's third tier, see :func:`resolve_formatter`).
    """
    formatter_name = _TYPE_FORMATTERS.get(type_name)
    if formatter_name is None:
        return None
    return _FORMATTERS.get(formatter_name)


def _type_bound_formatter(response_type: str | None) -> MarkdownFormatter | None:
    """Return the domain formatter bound to a response type name.

    The binding key is the response schema's root type, carried as
    first-class metadata rather than read back out of the schema:

    - the converter stamps the operation-level ``x-response-type`` *before*
      response-schema wrapping inlines the root ``$ref`` (which would erase
      it) — see ``openapi_converter/type_references.py``;
    - the tool/resource registration layers propagate it into
      ``tool.meta["response_type"]`` / resource content meta;
    - the contract spine and the ``read_resource`` executor put it on
      ``ExecutionResult.response_type``.

    It covers array and object responses uniformly (the stamp is the element
    or root type).  Returns ``None`` — and the caller falls back to the
    generic renderer — when there is no type or no formatter registered for
    the type, so unknown types keep today's behavior exactly.
    """
    if not response_type:
        return None
    return get_formatter_for_type(response_type)


def resolve_formatter(
    schema: dict[str, Any] | None,
    explicit: MarkdownFormatter | None = None,
    response_type: str | None = None,
) -> MarkdownFormatter:
    """Return the formatter for a result: explicit, type-bound, or generic.

    Three tiers, consulted in order:

    1. ``explicit`` — an explicit per-result formatter (the resource
       surface's ``format_hint`` resolution).
    2. The type-bound domain formatter for ``response_type``
       (:func:`_type_bound_formatter`) — the same view a resource
       sibling renders, applied to autogen tools and un-hinted resources.
    3. :func:`format_as_markdown`, with ``schema`` bound up front because
       ``call_markdown_formatter`` dispatches only ``extra`` (never
       ``schema`` or ``detail``).

    Centralising the choice here keeps the pipeline's ``_format`` a single,
    uniform formatter call site.  The function lives in the format layer so
    the pipeline never imports the domain formatter module.
    """
    if explicit is not None:
        return explicit
    bound = _type_bound_formatter(response_type)
    if bound is not None:
        return bound
    return functools.partial(format_as_markdown, schema=schema)


# ---------------------------------------------------------------------------
# Shared content transform: base64 decode
# ---------------------------------------------------------------------------


async def decode_base64_content(response: Any) -> str:
    """Decode base64 file content from a Gitea ContentsResponse.

    Gitea's ``/repos/{owner}/{repo}/contents/{path}`` endpoint returns a JSON
    object with ``content`` (base64-encoded) and ``encoding`` ("base64") fields.
    This function extracts and decodes the content for text output.

    Shared by tools (response post-processing in ``_pipeline_with_context``)
    and the ``read_resource`` tool (content detection in
    ``_read_resource_tool``).  When the caller detects a base64-encoded
    ``ContentsResponse`` (via the OpenAPI spec's ``x-response-transform``
    annotation for tools, or runtime JSON parse for resources), it calls this
    to produce plain text.

    Handles four response shapes:
    - ``str``: returned as-is (e.g., error messages from the API)
    - ``dict`` with ``encoding="base64"``: ``content`` is base64-decoded
    - ``dict`` without base64 encoding: ``content`` field returned as-is
    - Any other type: converted to ``str()``

    Args:
        response: Raw API response (str, dict, or other).

    Returns:
        Decoded text content.
    """
    if isinstance(response, str):
        return response
    if isinstance(response, dict) and response.get("encoding") == "base64":
        return base64.b64decode(response.get("content") or "").decode("utf-8")
    if isinstance(response, dict):
        return cast("str", response.get("content", ""))
    return str(response)


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


def _extract_ref(schema: dict[str, Any] | None) -> str | None:
    """Extract a ``$ref`` pointer string from a schema dict.

    Thin delegation to :func:`~gitea_mcp_server.schema_utils.extract_type_ref`
    — the single shared root-ref notion used by the converter's pre-wrap
    response-type stamp, the display layer's collapse walker, and the
    type-bound formatter dispatch, so they can never disagree about whether a
    schema *is* a reference.

    Args:
        schema: A JSON Schema fragment (may be ``None``).

    Returns:
        The ``$ref`` pointer string or ``None``.
    """
    return extract_type_ref(schema)


def _extract_type_name(schema: dict[str, Any] | None) -> str | None:
    """Extract a type name from a schema dict via ``$ref``.

    Thin delegation to
    :func:`~gitea_mcp_server.schema_utils.extract_type_name` — returns the
    last path segment (the type name) or ``None`` if no ``$ref`` is found.

    Args:
        schema: A JSON Schema fragment (may be ``None``).

    Returns:
        The type name (e.g. ``"Repository"``) or ``None``.
    """
    return extract_type_name(schema)


# Alias-chasing cap for root-item resolution: a ``$ref`` whose target is
# itself a reference — bare (e.g. ``CreatePullReviewCommentOptions`` in the
# live Gitea spec) or combinator-wrapped (``allOf``/``anyOf``/``oneOf``) —
# is followed at most this many hops before resolution gives up and the
# caller falls back to whole-item labelling.  The cap also breaks reference
# cycles (including cycles routed through a combinator).
_MAX_REF_ALIAS_HOPS = 5


def _resolve_root_items_schema(
    items_schema: dict[str, Any],
    openapi_spec: OpenAPISpec | None,
) -> dict[str, Any] | None:
    """Resolve a root list's item ``$ref`` for S1-lite collapse (#759).

    Uses :func:`_extract_ref` at every hop — the same ``$ref`` notion the
    collapse walker itself applies (top-level first, then combinator
    options) — so resolver and walker can never disagree about whether a
    schema *is* a reference.  A genuine ``allOf`` merge is chased to its
    first ``$ref`` member; the remaining members' keys then carry no
    property schema and pass through verbatim — a summary that is slightly
    fatter, never emptier, than whole-item labelling.

    Returns the concrete schema the referenced type resolves to, or
    ``None`` when there is no spec, no ``$ref``, or resolution fails
    (missing pointer or a reference chain longer than
    :data:`_MAX_REF_ALIAS_HOPS`).  Callers fall back to whole-item
    labelling on ``None``.
    """
    if openapi_spec is None:
        return None
    ref = _extract_ref(items_schema)
    if ref is None:
        return None
    for _ in range(_MAX_REF_ALIAS_HOPS):
        resolved = resolve_spec_ref(openapi_spec, ref)
        if not isinstance(resolved, dict):
            return None
        nxt = _extract_ref(resolved)
        if nxt is None:
            return resolved  # concrete schema (properties / inline combinator)
        ref = nxt  # alias (bare or combinator-wrapped) — chase one hop
    return None


def collapse_data(  # noqa: PLR0911 - 7 returns: 2 guard clauses (full detail, no schema), 2 collapse outcomes (dict $ref, list $ref), 2 recursive walks (dict recurse, list recurse), 1 scalar passthrough
    data: Any,
    schema: dict[str, Any] | None = None,
    _depth: int = 0,
    detail: str = "full",
    *,
    openapi_spec: OpenAPISpec | None = None,
) -> Any:
    """Walk data+schema, replacing $ref-backed objects with the marker (S1-lite).

    Used to shape data before formatting: when ``detail="concise"``,
    properties whose schema declares a ``$ref`` are replaced by the canonical
    agent-facing marker — ``{"$ref": "TypeName"}`` for an object,
    ``{"$ref": "TypeName", "count": N}`` for a list.  Inline schemas (no
    ``$ref``) are NOT collapsed — they remain as nested dicts/lists for the
    formatter to render.

    **Root items are never marker-replaced** (#759): the root object and
    the items of a root list keep their scalar fields; only their nested
    ``$ref``-backed fields collapse.  A root list whose item schema is a
    ``$ref`` is resolved one level via *openapi_spec* so each item is
    *summarized*; without a spec (or when resolution fails) the
    whole-item marker fallback applies (each root item becomes a marker).

    When ``schema`` is ``None`` or ``detail="full"``, the data is returned
    unchanged (the tree is still walked for ``schema=None``, but no
    collapsing occurs).  Nothing is ever mutated or truncated — collapse
    only replaces ``$ref``-backed subtrees with the canonical marker.

    Args:
        data: The data to collapse (dict, list, or scalar).
        schema: The JSON Schema describing *data*, or ``None``.
        _depth: Current nesting depth — 0 means top-level (never collapsed).
        detail: ``"full"`` (return unchanged) or ``"concise"`` (collapse nested
            ``$ref``-backed fields; root items are summarized, never collapsed).
        openapi_spec: Post-conversion OpenAPI 3.1 spec, used to resolve a
            root list's item ``$ref`` one level (S1-lite item summaries).
            ``None`` (synthetic tools, unit callers) keeps the whole-item
            marker fallback.

    Returns:
        Collapsed data (dicts, lists, markers) suitable for JSON serialization
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
                return ref_marker(type_name)
            # No $ref — recurse (inline schemas stay expanded)

        combined = _merge_allof_schema(schema)
        properties = combined.get("properties", {}) if isinstance(combined, dict) else {}

        result: dict[str, Any] = {}
        for k, v in data.items():
            prop_schema = properties.get(k) if properties else None
            if prop_schema is not None and not isinstance(prop_schema, dict):
                prop_schema = None
            effective = _resolve_anyof_schema(prop_schema) if prop_schema else None
            result[k] = collapse_data(v, effective or prop_schema, _depth + 1, detail)
        return result

    if isinstance(data, list):
        items_schema = schema.get("items", {}) if isinstance(schema, dict) else {}
        if _depth >= 1:
            type_name = _extract_type_name(items_schema)
            if type_name:
                return ref_marker(type_name, len(data))
            # No $ref — recurse
        else:
            # Root list: items are roots — resolve a $ref item schema one
            # level so each item is summarized (scalars kept, nested refs
            # marked) instead of being marker-replaced wholesale (#759).
            # Resolution is consumed here; recursive calls below are all at
            # depth >= 1 and never consult the spec again.
            resolved = _resolve_root_items_schema(items_schema, openapi_spec)
            if resolved is not None:
                items_schema = resolved
        return [collapse_data(item, items_schema, _depth + 1, detail) for item in data]

    return data


def _format_list_as_markdown(
    data: list[Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    field_filter: dict[str, dict] | None = None,
    item_title_key: str | None = None,
) -> str:
    lines: list[str] = []
    item_schema = schema.get("items") if isinstance(schema, dict) else None
    if not data:
        lines.append(f"{indent}_(empty)_")
    # Lists of $ref markers render as bulleted labels.  This is the canonical
    # agent-facing marker, emitted by both producers: the example/type-summary
    # generator (tool_info's output_example, resolve_type's summary) and the
    # collapse's no-spec root-list fallback (each item is a marker).  Detection
    # goes through the shared is_ref_marker predicate — no consumer
    # pattern-matches its own marker variant.
    elif data and all(is_ref_marker(v) for v in data):
        for v in data:
            lines.append(f"{indent}- {ref_marker_label(v)}")
    elif data and isinstance(data[0], dict):
        for i, item in enumerate(data):
            title: str | None = None
            if item_title_key:
                val = item.get(item_title_key)
                if val is not None:
                    title = str(val)
            if title is None:
                title = f"Item {i + 1}"
            sub = format_as_markdown(
                item,
                item_schema,
                title=title,
                _depth=0,
                field_filter=field_filter,
            )
            lines.append(sub)
    elif item_schema and get_schema_type(item_schema) in ("string", "number", "integer", "boolean"):
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
                if isinstance(sub, dict) and sub.get("properties"):
                    t = sub.get("type")
                    if isinstance(t, str) and t == "object":
                        return sub
                    if isinstance(t, list) and "object" in t:
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


def _render_list_as_compact_ref(raw_val: list, template: str) -> str:
    """Render a list of dicts as comma-separated template expansions.

    Each dict item is expanded through *template* via ``str.format(**item)``.
    Non-dict items and template errors fall back to ``str(item)``.
    """
    items: list[str] = []
    for item in raw_val:
        if isinstance(item, dict):
            try:
                items.append(template.format(**item))
            except (KeyError, ValueError, TypeError):
                items.append(str(item))
        else:
            items.append(str(item))
    return ", ".join(items)


def _format_dict_as_markdown(  # noqa: PLR0912 - justified: scalar/nested, field_filter, allOf, anyOf, render hints
    data: dict[str, Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    _depth: int = 0,
    field_filter: dict[str, dict] | None = None,
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

            # Resolve field-level render hint from field_filter dict values.
            field_opts = field_filter.get(key, {}) if field_filter else {}
            render_hint = field_opts.get("render", "expand")

            # The canonical $ref marker is checked before render hints.  A
            # compact_ref template (e.g. owner -> "{login}") has no matching
            # key on a marker and would otherwise fall back to a Python repr;
            # the marker always renders as its compact label.
            if is_ref_marker(raw_val):
                flat.append((label, ref_marker_label(raw_val)))
                continue

            # Render hints override nesting — compact_ref and badge
            # always produce flat table rows regardless of value type.
            if render_hint == "compact_ref" and isinstance(raw_val, dict):
                template = field_opts.get("template", "{id}")
                try:
                    formatted = template.format(**raw_val)
                except (KeyError, ValueError, TypeError):
                    formatted = str(raw_val)
                flat.append((label, formatted))
            elif render_hint == "compact_ref" and isinstance(raw_val, list):
                template = field_opts.get("template", "{name}")
                formatted = _render_list_as_compact_ref(raw_val, template)
                flat.append((label, formatted))
            elif render_hint == "badge":
                flat.append((label, "Yes" if raw_val else "No"))
            elif isinstance(raw_val, (dict, list)):
                # The pipeline pre-collapses nested ``$ref``-backed objects to
                # the canonical marker when ``detail=concise`` (#759), so this
                # formatter only ever renders already-collapsed data — it does
                # not collapse.  Markers were handled above; anything left here
                # is real nested payload.
                # Don't propagate field_filter into nested sub-objects -
                # the parent's field names don't apply to child objects.
                sub = format_as_markdown(
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


def format_as_markdown(
    data: Any,
    schema: dict[str, Any] | None = None,
    title: str | None = None,
    _depth: int = 0,
    field_filter: dict[str, dict] | None = None,
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
        result = _format_dict_as_markdown(
            data,
            schema,
            indent,
            _depth,
            field_filter=field_filter,
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
    ptype = get_schema_type(prop) or "any"

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


def format_tool_info_markdown(schema: ToolSchemaResult) -> str:
    """Format a ``ToolSchemaResult`` as parseable, consistent markdown.

    Produces a predictable structure with a parameter table that agents can
    parse reliably:

    - ``## Parameters`` — table with ``Parameter | Type | Required | Description``
    - ``## Output Example`` — JSON code block
    - ``## Annotations`` — table with ``Hint | Value``
    - ``## Tags`` — comma-separated list
    - ``## Output Schema`` — JSON code block (only when ``output_schema`` present)

    Tool-info output is always full detail — the display pipeline pre-collapses
    only when ``detail=concise``, and this formatter renders fixed-shape output
    that never collapses, so it takes no ``detail`` parameter.
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


def build_server_info_markdown(openapi_spec: OpenAPISpec) -> str:
    """Build server info markdown from OpenAPI spec info block.

    Unlike registered domain formatters (which take ``data`` and optional
    ``extra``), this function takes the raw OpenAPI spec directly.  It lives
    in ``format.py`` rather than ``tools/display.py`` because it is not a
    registered formatter — it is a shared utility used by
    ``resources/custom.py``.
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
    "MarkdownFormatter",
    "build_server_info_markdown",
    "call_markdown_formatter",
    "collapse_data",
    "decode_base64_content",
    "format_as_markdown",
    "get_formatter",
    "get_formatter_for_type",
    "register_formatter",
    "resolve_formatter",
]
