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

    meta = ResourceMeta.from_schema(schema, required_scope="read:repository")
    mcp.resource(uri, meta=meta.to_dict())(handler)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

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


# ── ResourceMeta dataclass ───────────────────────────────────────────────────


@dataclass
class ResourceMeta:
    """Typed metadata for a resource, stored in FastMCP's ``meta`` dict.

    All fields are optional with ``None`` default, so missing fields are
    omitted from the serialised dict (backward compatible with agents that
    read ``ResourceEntry`` from ``list_resources`` output).

    Build via the constructor for explicit values::

        ResourceMeta(required_scope="read:repository", size_hint="medium")

    Or via ``from_schema()`` for auto-derived values::

        ResourceMeta.from_schema(schema, required_scope="read:repository")
    """

    required_scope: str | None = None
    response_schema: dict[str, Any] | None = None
    format_hint: str | None = None
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
        """
        result: dict[str, Any] = {}
        if self.required_scope is not None:
            result["required_scope"] = self.required_scope
        if self.response_schema is not None:
            result["response_schema"] = self.response_schema
        if self.format_hint is not None:
            result["format_hint"] = self.format_hint
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
    def from_schema(
        cls,
        schema: dict[str, Any] | None,
        *,
        required_scope: str | None = None,
        response_schema: dict[str, Any] | None = None,
        format_hint: str | None = None,
        cache_ttl: float | None = None,
        optional_params: list[dict[str, Any]] | None = None,
        size_hint: str | None = None,
        default_detail: str | None = None,
    ) -> ResourceMeta:
        """Build metadata with optional auto-derivation of ``size_hint``.

        When ``size_hint`` is not explicitly provided, it is derived from
        the response schema via :func:`derive_size_hint_from_schema`.
        When ``default_detail`` is not explicitly provided, it is derived
        from the ``size_hint`` (whether explicit or derived) via
        :func:`default_detail_for`.

        This is the recommended construction path for all registration
        code — it ensures consistent metadata regardless of how the
        resource is registered.
        """
        resolved_size = size_hint or derive_size_hint_from_schema(schema)
        resolved_detail = default_detail or default_detail_for(resolved_size)
        return cls(
            required_scope=required_scope,
            response_schema=response_schema,
            format_hint=format_hint,
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


def _is_array_schema(schema: dict[str, Any] | None) -> bool:
    """Check whether a schema represents an array (list) response."""
    if not schema or not isinstance(schema, dict):
        return False
    if schema.get("type") == "array" or schema.get("type") == ["array"]:
        return True
    # Check for array inside result wrapper (shouldn't happen here
    # since we use the *inner* schema, but defensive check)
    props = schema.get("properties", {})
    if isinstance(props, dict):
        for prop in props.values():
            if isinstance(prop, dict) and (
                prop.get("type") == "array" or prop.get("type") == ["array"]
            ):
                return True
    return False


def _estimate_nesting_depth(schema: dict[str, Any] | None, _depth: int = 0) -> int:
    """Estimate the maximum nesting depth of a schema.

    Used to determine whether a resource has deep object hierarchies
    that would make its output expensive to render.
    """
    if not schema or not isinstance(schema, dict) or _depth > 5:
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

    Rules::

        No schema or no properties  → tiny
        1-5 properties, not an array → small
        6-20 properties              → medium
        20+ properties or array      → large
        Nesting depth >= 3           → large
    """
    if not schema or not isinstance(schema, dict):
        return SIZE_TINY

    props_count = _count_schema_properties(schema)
    is_array = _is_array_schema(schema)
    depth = _estimate_nesting_depth(schema)

    # Deep nesting makes even small schemas expensive
    if depth >= 3:
        return SIZE_LARGE

    # List resources are always at least medium
    if is_array:
        if props_count > _MEDIUM_MAX_PROPERTIES:
            return SIZE_LARGE
        if props_count > _SMALL_MAX_PROPERTIES:
            return SIZE_MEDIUM
        # Shallow list of simple items
        return SIZE_MEDIUM if props_count > 0 else SIZE_SMALL

    # Scalar or empty
    if props_count == 0:
        return SIZE_TINY

    # Object with properties
    if props_count > _MEDIUM_MAX_PROPERTIES:
        return SIZE_LARGE
    if props_count > _SMALL_MAX_PROPERTIES:
        return SIZE_MEDIUM
    return SIZE_SMALL


def default_detail_for(size_hint: str) -> str:
    """Derive the recommended ``default_detail`` from a ``size_hint``.

    ``large`` resources should default to ``concise`` to avoid token
    bloat; everything else defaults to ``full`` for maximum detail.
    """
    return DETAIL_CONCISE if size_hint == SIZE_LARGE else DETAIL_FULL


__all__ = [
    "DETAIL_CONCISE",
    "DETAIL_FULL",
    "DETAILS",
    "SIZE_HINTS",
    "SIZE_LARGE",
    "SIZE_MEDIUM",
    "SIZE_SMALL",
    "SIZE_TINY",
    "ResourceMeta",
    "default_detail_for",
    "derive_size_hint_from_schema",
]
