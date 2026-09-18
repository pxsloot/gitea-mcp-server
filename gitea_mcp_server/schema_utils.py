"""Shared JSON Schema utility functions.

Flat utility module that breaks the circular import between
``openapi_converter/`` (low-level spec processing) and ``tools/``
(higher-level tool metadata processing).  All schema-type-related
helpers live here so any layer can import them without creating
import cycles.

Following the same pattern as :mod:`gitea_mcp_server.scope` (a flat
module that breaks a circular import between ``tools/`` and
``resources/``).
"""

from typing import Any


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
    "extract_type_name",
    "extract_type_ref",
    "get_schema_type",
    "schema_type_matches",
]
