"""Resource metadata types and derivation helpers.

Provides the ``ResourceMeta`` dataclass for typed resource metadata and
schema-analysis helpers for auto-deriving ``size_hint`` and ``default_detail``
across all three registration paths (factory, auto-generated, legacy static).

Usage::

    meta = ResourceMeta(
        required_scope="read:repository",
        size_hint="large",
        default_detail="concise",
        optional_params=[{"name": "state", "type": "string"}],
    )
    mcp.resource(uri, meta=meta.to_dict())(handler)

Or with auto-derivation::

    meta = ResourceMeta.for_schema(schema, required_scope="read:repository")
    mcp.resource(uri, meta=meta.to_dict())(handler)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from gitea_mcp_server.tools.schemas import _schema_type_is_array

logger = logging.getLogger(__name__)

# ── Size hint values ──────────────────────────────────────────────────────────

SIZE_TINY = "tiny"
SIZE_SMALL = "small"
SIZE_MEDIUM = "medium"
SIZE_LARGE = "large"

SIZE_HINTS = frozenset({SIZE_TINY, SIZE_SMALL, SIZE_MEDIUM, SIZE_LARGE})

# ── Default detail values ────────────────────────────────────────────────────

DETAIL_FULL = "full"
DETAIL_CONCISE = "concise"

DETAILS = frozenset({DETAIL_FULL, DETAIL_CONCISE})


# ── Schema analysis threshold constants ──────────────────────────────────────

_SMALL_MAX_PROPERTIES = 5
_MEDIUM_MAX_PROPERTIES = 20
_MAX_RECURSION_DEPTH = 5
_DEEP_NESTING_THRESHOLD = 3


# ── ResourceMeta dataclass ───────────────────────────────────────────────────


@dataclass
class ResourceMeta:
    """Typed metadata for a resource, stored in FastMCP's ``meta`` dict.

    All fields are optional with ``None`` default, so missing fields are
    omitted from the serialised dict (backward compatible with agents that
    read ``ResourceEntry`` from ``list_resources`` output).

    Build via the constructor for explicit values::

        ResourceMeta(required_scope="read:repository", size_hint="medium")

    Or via ``for_schema()`` for auto-derived ``size_hint``::

        ResourceMeta.for_schema(schema, required_scope="read:repository")

    ``for_schema`` derives ``size_hint`` from the response schema's structure
    when not provided explicitly, and derives ``default_detail`` from the
    resulting ``size_hint``.  Other fields (``required_scope``, ``cache_ttl``,
    ``optional_params``) pass through untouched — they are configuration, not
    schema-derived.
    """

    required_scope: str | None = None
    cache_ttl: float | None = None
    optional_params: list[dict[str, Any]] | None = None
    size_hint: str | None = None
    default_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, omitting ``None`` values.

        This produces a compact dict that FastMCP stores as the resource's
        ``meta``.  Agents receive it through ``list_resources`` output.
        ``None`` values are omitted so that resources that don't set a field
        remain backward compatible — missing fields are simply absent from
        the dict rather than present with a ``null`` value.

        Note:
            ``response_schema`` and ``format_hint`` are **content-level**
            metadata (stored in ``ResourceContent.meta`` for the display
            pipeline).  They intentionally do **not** appear here —
            registration-level metadata (this class) is what agents discover
            via ``list_resources`` before reading.
        """
        result: dict[str, Any] = {}
        if self.required_scope is not None:
            result["required_scope"] = self.required_scope
        if self.cache_ttl is not None:
            result["cache_ttl"] = self.cache_ttl
        if self.optional_params is not None:
            result["optional_params"] = self.optional_params
        if self.size_hint is not None:
            result["size_hint"] = self.size_hint
        if self.default_detail is not None:
            result["default_detail"] = self.default_detail
        return result

    @classmethod
    def for_schema(  # noqa: PLR0913 — 6 params: cls, schema, +4 optional overrides — all independent
        cls,
        schema: dict[str, Any] | None,
        *,
        required_scope: str | None = None,
        cache_ttl: float | None = None,
        optional_params: list[dict[str, Any]] | None = None,
        size_hint: str | None = None,
        default_detail: str | None = None,
    ) -> ResourceMeta:
        """Build metadata for a resource backed by *schema*, auto-deriving ``size_hint``.

        Only ``size_hint`` and ``default_detail`` are derived from the schema:
        ``size_hint`` is derived from the response schema's property count,
        array-ness, and nesting depth via :func:`derive_size_hint_from_schema`.
        ``default_detail`` is then derived from ``size_hint``.

        The remaining fields (``required_scope``, ``cache_ttl``,
        ``optional_params``) pass through as-is — they are configuration,
        not schema-derived.  This avoids callers having to construct two
        separate metadata dicts.

        Use explicit overrides when the auto-derived values don't fit
        (e.g. a resource with few object properties that can still produce
        large output).
        """
        resolved_size = size_hint or derive_size_hint_from_schema(schema)
        resolved_detail = default_detail or default_detail_for(resolved_size)
        return cls(
            required_scope=required_scope,
            cache_ttl=cache_ttl,
            optional_params=optional_params,
            size_hint=resolved_size,
            default_detail=resolved_detail,
        )


# ── Schema analysis ──────────────────────────────────────────────────────────


def _count_schema_properties(schema: dict[str, Any] | None) -> int:
    """Count the number of properties in a JSON Schema object.

    Handles both plain schemas and ``$ref`` wrappers (returns 0 for
    schemas that are pure ``$ref`` since we can't count their properties
    without resolution).
    """
    if not schema or not isinstance(schema, dict):
        return 0
    props = schema.get("properties")
    if isinstance(props, dict):
        return len(props)
    # Single $ref — can't count without resolution
    if "$ref" in schema:
        return 0
    return 0


# _is_array_schema is deliberately imported from tools/schemas rather than
# duplicated here.  Both modules need it; one canonical implementation.


def _estimate_nesting_depth(schema: dict[str, Any] | None, _depth: int = 0) -> int:
    """Estimate the maximum nesting depth of a schema.

    Used to determine whether a resource has deep object hierarchies
    that would make its output expensive to render.
    """
    if not schema or not isinstance(schema, dict) or _depth > _MAX_RECURSION_DEPTH:
        return _depth
    depths = [_depth]
    props = schema.get("properties")
    if isinstance(props, dict):
        for prop in props.values():
            if isinstance(prop, dict):
                depths.append(_estimate_nesting_depth(prop, _depth + 1))
    items = schema.get("items")
    if isinstance(items, dict):
        depths.append(_estimate_nesting_depth(items, _depth + 1))
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        depths.append(_estimate_nesting_depth(additional, _depth + 1))
    return max(depths)


def derive_size_hint_from_schema(schema: dict[str, Any] | None) -> str:
    """Analyse a response schema to estimate the resource's size.

    Returns one of ``"tiny"``, ``"small"``, ``"medium"``, or ``"large"``.

    Priority order (first match wins)::

        Nesting depth >= 3           → large
        Array type                   → large
        No schema or no properties   → tiny
        > 20 properties              → large
        > 5 properties               → medium
        ≤ 5 properties               → small
    """
    if not schema or not isinstance(schema, dict):
        return SIZE_TINY

    props_count = _count_schema_properties(schema)
    is_array = _schema_type_is_array(schema) if isinstance(schema, dict) else False
    depth = _estimate_nesting_depth(schema)

    # Deep nesting or array → large: both signal potentially expensive output.
    # Arrays have unknown item count — even a schema with zero direct
    # properties (e.g. ``{"type": "array", "items": {"type": "string"}}``)
    # could produce hundreds of rows in practice.
    if depth >= _DEEP_NESTING_THRESHOLD or is_array:
        return SIZE_LARGE

    # Empty/scalar
    if props_count == 0:
        return SIZE_TINY

    # Determine size from property count
    if props_count > _MEDIUM_MAX_PROPERTIES:
        return SIZE_LARGE

    if props_count > _SMALL_MAX_PROPERTIES:
        return SIZE_MEDIUM

    return SIZE_SMALL


def default_detail_for(size_hint: str) -> str:
    """Derive the recommended ``default_detail`` from a ``size_hint``.

    ``large`` resources should default to ``concise`` to avoid token
    bloat; everything else defaults to ``full`` for maximum detail.

    Raises:
        ValueError: If ``size_hint`` is not one of ``SIZE_HINTS``.
    """
    if size_hint not in SIZE_HINTS:
        msg = f"Unknown size_hint: {size_hint!r}. Must be one of {sorted(SIZE_HINTS)}."
        raise ValueError(msg)
    return DETAIL_CONCISE if size_hint == SIZE_LARGE else DETAIL_FULL


__all__ = [
    "DETAILS",
    "DETAIL_CONCISE",
    "DETAIL_FULL",
    "SIZE_HINTS",
    "SIZE_LARGE",
    "SIZE_MEDIUM",
    "SIZE_SMALL",
    "SIZE_TINY",
    "ResourceMeta",
    "default_detail_for",
    "derive_size_hint_from_schema",
]
