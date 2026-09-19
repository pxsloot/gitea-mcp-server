"""Shared JSON Schema utility functions.

Flat utility module that breaks the circular import between
``openapi_converter/`` (low-level spec processing) and ``tools/``
(higher-level tool metadata processing).  All schema-type-related
helpers live here so any layer can import them without creating
import cycles.

It also owns the canonical **agent-facing ``$ref`` marker** contract
(:func:`ref_marker` / :func:`is_ref_marker` / :func:`ref_marker_label`):
"this object/list is represented by its type name" is one dict shape, emitted
by the concise collapse (``format.collapse_data``) and the compact example
generator (``tools.examples.schema_to_compact_example``) and read by every
formatter through these helpers (#763).

Following the same pattern as :mod:`gitea_mcp_server.scope` (a flat
module that breaks a circular import between ``tools/`` and
``resources/``).
"""

from typing import Any, TypeGuard


def schema_type_matches(schema: dict[str, Any], expected: str) -> bool:
    """Check if a schema's type matches the expected type name.

    JSON Schema permits the ``type`` field as either a string
    (e.g. ``"object"``) **or** a list (e.g. ``["object", "null"]``).
    Many places in the codebase use ``schema.get("type") == "object"``
    which silently fails on the list form.  This helper handles both.

    Args:
        schema: A JSON Schema dict.
        expected: The expected type name (e.g. ``"object"``, ``"array"``,
            ``"string"``, ``"file"``).

    Returns:
        ``True`` if the schema's ``type`` is the expected string or,
        when ``type`` is a list, contains the expected value.

    Examples:
        >>> schema_type_matches({"type": "object"}, "object")
        True
        >>> schema_type_matches({"type": ["array", "null"]}, "array")
        True
        >>> schema_type_matches({"type": "string"}, "object")
        False
        >>> schema_type_matches({}, "object")
        False
    """
    t = schema.get("type")
    if isinstance(t, str):
        return t == expected
    if isinstance(t, list):
        return expected in t
    return False


def extract_type_ref(schema: Any) -> str | None:
    """Return the first ``$ref`` pointer at a schema's root or in a combinator.

    Checks the schema itself and any ``anyOf``/``oneOf``/``allOf`` options
    for a ``$ref`` pointer.  Returns the full pointer (e.g.
    ``"#/components/schemas/Repository"``) or ``None`` if no ``$ref`` is
    found.

    The lookup is deliberately shallow: it does not resolve the pointer or
    follow alias chains.  Callers that need the referenced schema resolve it
    against the spec.  This is the shared root-ref notion used by the
    converter's pre-wrap response-type stamp and the display layer's
    type-bound formatter dispatch, so the two can never disagree about
    whether a schema *is* a reference.

    Args:
        schema: A JSON Schema fragment (may be ``None`` or non-dict).

    Returns:
        The ``$ref`` pointer string or ``None``.
    """
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref
    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict):
                    option_ref = option.get("$ref")
                    if isinstance(option_ref, str):
                        return option_ref
    return None


def extract_type_name(schema: Any) -> str | None:
    """Extract a type name from a schema via :func:`extract_type_ref`.

    Args:
        schema: A JSON Schema fragment (may be ``None``).

    Returns:
        The last path segment of the first ``$ref`` found (e.g.
        ``"Repository"``) or ``None`` when the schema carries no reference.
    """
    ref = extract_type_ref(schema)
    return ref.rsplit("/", 1)[-1] if ref else None


# ---------------------------------------------------------------------------
# Agent-facing ``$ref`` marker
# ---------------------------------------------------------------------------
# "This object/list is represented by its type name" is encoded as one small
# dict on the agent-facing surface — the single marker shape:
#
#     {"$ref": "User"}                 # an object position
#     {"$ref": "Label", "count": 2}    # a collapsed list position
#
# Two producers emit it — the concise-detail collapse
# (``format.collapse_data``) and the compact example/type-summary generator
# (``tools.examples.schema_to_compact_example``) — and every consumer
# (formatters, ``resolve_type``, agents) reads it through these helpers, so
# the shape is defined exactly once.  The marker is deliberately not a plain
# string (``"$ref:User"`` would be ambiguous with real string data) and not a
# JSON Schema ``$ref`` pointer (it carries a bare type name, not a pointer).
REF_MARKER_KEY = "$ref"
"""Key holding the referenced type name inside a marker."""

REF_COUNT_KEY = "count"
"""Optional key holding the collapsed-list item count inside a marker."""


def ref_marker(type_name: str, count: int | None = None) -> dict[str, Any]:
    """Build the canonical agent-facing ``$ref`` marker.

    Args:
        type_name: The referenced type name (e.g. ``"User"``) — a bare name,
            not a JSON pointer, so it can be passed straight to
            ``resolve_type``.
        count: When the marker stands in for a collapsed *list*, the number of
            items collapsed.  ``None`` for an object position.

    Returns:
        ``{"$ref": type_name}`` for an object position, or
        ``{"$ref": type_name, "count": count}`` for a collapsed list.
    """
    marker: dict[str, Any] = {REF_MARKER_KEY: type_name}
    if count is not None:
        marker[REF_COUNT_KEY] = count
    return marker


def is_ref_marker(value: Any) -> TypeGuard[dict[str, Any]]:
    """Return whether *value* is the canonical agent-facing ``$ref`` marker.

    Accepts both marker forms — the object form (single key) and the collapsed
    list form (``count`` beside the type name).  Structurally strict: a real
    payload dict that merely happens to contain a ``$ref`` key is not a marker
    unless it is exactly the marker shape.

    Typed as a :data:`~typing.TypeGuard` so callers narrow the value to the
    marker dict when the check passes (the marker renderer takes a dict).

    Args:
        value: Any candidate value.

    Returns:
        ``True`` for ``{"$ref": "User"}`` and
        ``{"$ref": "Label", "count": 2}``; ``False`` otherwise.
    """
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get(REF_MARKER_KEY), str):
        return False
    extra = set(value) - {REF_MARKER_KEY}
    if not extra:
        return True
    return extra == {REF_COUNT_KEY} and isinstance(value[REF_COUNT_KEY], int)


def ref_marker_label(marker: dict[str, Any]) -> str:
    """Render a ``$ref`` marker as its compact markdown/scalar label.

    Args:
        marker: A marker produced by :func:`ref_marker`.

    Returns:
        ``"$ref:User"`` for an object marker, ``"$ref:Label[2]"`` for a
        collapsed list marker.
    """
    type_name = marker[REF_MARKER_KEY]
    count = marker.get(REF_COUNT_KEY)
    if count is None:
        return f"$ref:{type_name}"
    return f"$ref:{type_name}[{count}]"


def get_schema_type(schema: dict[str, Any]) -> str | None:
    """Extract the primary type name from a schema, resolving type-as-list.

    When ``type`` is a list (e.g. ``["object", "null"]``), returns the
    first element that is not ``"null"``.  When ``type`` is a plain
    string, returns it as-is.

    Args:
        schema: A JSON Schema dict.

    Returns:
        The primary type name (first non-null type in a list), or
        ``None`` if the schema has no ``type`` key or only ``"null"``.

    Examples:
        >>> get_schema_type({"type": "object"})
        'object'
        >>> get_schema_type({"type": ["array", "null"]})
        'array'
        >>> get_schema_type({"type": ["null"]})
        'null'
        >>> get_schema_type({}) is None
        True
    """
    t = schema.get("type")
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        for item in t:
            if isinstance(item, str) and item != "null":
                return item
        return str(t[0]) if t else None
    return None


__all__ = [
    "REF_COUNT_KEY",
    "REF_MARKER_KEY",
    "extract_type_name",
    "extract_type_ref",
    "get_schema_type",
    "is_ref_marker",
    "ref_marker",
    "ref_marker_label",
    "schema_type_matches",
]
