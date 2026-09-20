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

:func:`ref_marker` normalises its input, so a caller may pass a raw ``$ref``
pointer (``"#/components/schemas/User"``) or a path (``"a/b"``) and get the
canonical bare-name marker; :func:`is_ref_marker` recognises exactly that
canonical shape.  The two are inverses: ``is_ref_marker(ref_marker(x))`` is
always ``True``.

Kept as a flat module (like :mod:`gitea_mcp_server.scope` and
:mod:`gitea_mcp_server.schema_utils`) so any layer can import it without
creating an import cycle.
"""

from typing import Any, Literal, NotRequired, TypedDict, TypeGuard, cast

REF_MARKER_KEY: Literal["$ref"] = "$ref"
"""Key holding the referenced type name inside a marker."""

REF_COUNT_KEY: Literal["count"] = "count"
"""Optional key holding the collapsed-list item count inside a marker."""

RefMarker = TypedDict("RefMarker", {"$ref": str, "count": NotRequired[int]})
"""The canonical marker dict — a bare type name, with ``count`` for a list."""


def _bare_type_name(ref: str) -> str:
    """Reduce a ``$ref`` pointer or path to its bare last segment.

    ``"#/components/schemas/User"`` and ``"a/b"`` both yield ``"User"`` /
    ``"b"``; a leading ``#`` on a slash-less value (``"#c"``) is stripped too.
    """
    return ref.rsplit("/", 1)[-1].lstrip("#")


def ref_marker(type_name: str, count: int | None = None) -> RefMarker:
    """Build the canonical agent-facing ``$ref`` marker.

    Args:
        type_name: The referenced type name, or any ``$ref`` pointer/path that
            ends in it (e.g. ``"#/components/schemas/User"`` or ``"a/b"``).
            It is reduced to its bare last segment here, so callers pass the
            raw reference and need not ``rsplit`` it themselves.
        count: When the marker stands in for a collapsed *list*, the number of
            items collapsed.  ``None`` for an object position.

    Returns:
        ``{"$ref": "TypeName"}`` for an object position, or
        ``{"$ref": "TypeName", "count": count}`` for a collapsed list.
    """
    marker: dict[str, Any] = {REF_MARKER_KEY: _bare_type_name(type_name)}
    if count is not None:
        marker[REF_COUNT_KEY] = count
    return cast("RefMarker", marker)


def is_ref_marker(value: Any) -> TypeGuard[RefMarker]:
    """Return whether *value* is the canonical agent-facing ``$ref`` marker.

    Accepts both marker forms — the object form (single key) and the collapsed
    list form (``count`` beside the type name).  Structurally strict: a real
    payload dict that merely happens to contain a ``$ref`` key is not a marker
    unless it is exactly the marker shape *and* its ``$ref`` value is a bare
    type name — the canonical output of :func:`ref_marker`.  A JSON Schema
    pointer (e.g. ``"#/components/schemas/User"``) is therefore rejected, so a
    schema fragment passing through a formatter is never mistaken for a
    marker.

    Typed as a :data:`~typing.TypeGuard` so callers narrow the value to
    :class:`RefMarker` when the check passes (the marker renderer takes one).

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


def ref_marker_label(marker: RefMarker) -> str:
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
    "RefMarker",
    "is_ref_marker",
    "ref_marker",
    "ref_marker_label",
]
