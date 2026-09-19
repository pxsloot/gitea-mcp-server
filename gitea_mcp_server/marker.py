"""The canonical agent-facing ``$ref`` marker contract.

"This object/list is represented by its type name" is encoded as one small
dict on the agent-facing surface — the single marker shape::

    {"$ref": "User"}  # an object position
    {"$ref": "Label", "count": 2}  # a collapsed list position

Two producers emit it — the concise-detail collapse
(``format.collapse_data``) and the compact example/type-summary generator
(``tools.examples.schema_to_compact_example``, which also backs ``tool_info``
and ``resolve_type``) — and every consumer (formatters, agents) reads it
through these helpers, so the shape is defined exactly once.  The marker is
deliberately not a plain string (``"$ref:User"`` would be ambiguous with real
string data) and not a JSON Schema ``$ref`` pointer (it carries a bare type
name, not a pointer).

Kept as a flat module (like :mod:`gitea_mcp_server.scope` and
:mod:`gitea_mcp_server.schema_utils`) so any layer can import it without
creating an import cycle.
"""

from typing import Any, TypeGuard

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
    unless it is exactly the marker shape *and* its ``$ref`` value is a bare
    type name.  A JSON Schema pointer (e.g.
    ``"#/components/schemas/User"``) is rejected, so a schema fragment passing
    through a formatter is never mistaken for a marker.

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
    ref = value.get(REF_MARKER_KEY)
    if not isinstance(ref, str) or not ref:
        return False
    # A marker carries a *bare type name* (see the contract above), never a
    # JSON Schema pointer.  Rejecting ``#``/``/`` keeps a schema ``$ref`` from
    # being mistaken for a marker by the display path.
    if ref.startswith("#") or "/" in ref:
        return False
    extra = set(value) - {REF_MARKER_KEY}
    if not extra:
        return True
    if extra != {REF_COUNT_KEY}:
        return False
    count = value[REF_COUNT_KEY]
    # ``bool`` is an ``int`` subclass; a boolean count is not a count.
    return isinstance(count, int) and not isinstance(count, bool)


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


__all__ = [
    "REF_COUNT_KEY",
    "REF_MARKER_KEY",
    "is_ref_marker",
    "ref_marker",
    "ref_marker_label",
]
