"""Shared ``$ref`` chain resolution for schema consumers.

Resolves a schema's root ``$ref`` — bare or combinator-wrapped
(``allOf``/``anyOf``/``oneOf``) — to the concrete schema it points at, chasing
alias chains up to a hop cap.  Both producers of the agent-facing surface use
it to resolve a *payload* ``$ref`` one level, so the concise collapse
(``format.collapse_data``) and the compact example generator
(``tools.examples.schema_to_compact_example``) can never disagree about a
referenced type's shape.

It is a ``payload-resolution``-tier module: it sits above
:mod:`gitea_mcp_server.openapi_converter` (it calls the converter's
``resolve_spec_ref``) and below :mod:`gitea_mcp_server.format` (which consumes
it).  It cannot live in :mod:`gitea_mcp_server.schema_utils`: the converter
imports ``schema_utils``, so putting the resolver there would make the
converter import back into itself.  The layer contract
(``tests/unit/test_layer_contract.py``) governs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gitea_mcp_server.openapi_converter.core import resolve_spec_ref
from gitea_mcp_server.schema_utils import extract_type_ref

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec

# Alias-chasing cap: a ``$ref`` whose target is itself a reference — bare or
# combinator-wrapped — is followed at most this many hops before resolution
# gives up.  The cap also breaks reference cycles.
_MAX_REF_ALIAS_HOPS = 5


def resolve_ref_chain(
    schema: dict[str, Any] | None,
    openapi_spec: OpenAPISpec | None,
) -> dict[str, Any] | None:
    """Resolve a schema's root ``$ref`` chain to a concrete schema.

    A ``$ref`` whose target is itself a reference — bare or
    combinator-wrapped — is chased up to :data:`_MAX_REF_ALIAS_HOPS` hops.
    Both producers use this to resolve a *payload* item schema one level: the
    concise collapse (:func:`~gitea_mcp_server.format.collapse_data`) and the
    compact example generator
    (``tools.examples.schema_to_compact_example``), so the two can never
    disagree about a referenced type's shape (#763, #759).

    Returns the concrete schema (one with no root ``$ref``), or ``None`` when
    there is no spec, no ``$ref`` at the schema root, or the chain is broken
    or cyclic.
    """
    if openapi_spec is None:
        return None
    ref = extract_type_ref(schema)
    if ref is None:
        return None
    for _ in range(_MAX_REF_ALIAS_HOPS):
        resolved = resolve_spec_ref(openapi_spec, ref)
        if not isinstance(resolved, dict):
            return None
        nxt = extract_type_ref(resolved)
        if nxt is None:
            return resolved  # concrete schema (properties / inline combinator)
        ref = nxt  # alias (bare or combinator-wrapped) — chase one hop
    return None


__all__ = ["resolve_ref_chain"]
