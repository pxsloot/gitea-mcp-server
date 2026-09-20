"""Unit tests for gitea_mcp_server/ref_resolver.py.

``resolve_ref_chain`` resolves a schema's root ``$ref`` chain to a concrete
schema — shared by the concise collapse (``format.collapse_data``) and the
compact example generator (``tools.examples.schema_to_compact_example``).
"""

from __future__ import annotations

from gitea_mcp_server.ref_resolver import resolve_ref_chain
from tests.helpers.spec_fixtures import make_openapi_spec


class TestResolveRefChain:
    """Root ``$ref`` chain resolution (shared payload-``$ref`` resolver)."""

    def test_no_spec_returns_none(self) -> None:
        assert resolve_ref_chain({"$ref": "#/components/schemas/User"}, None) is None

    def test_inline_schema_returns_none(self) -> None:
        """An inline schema has no ``$ref`` chain to resolve."""
        assert resolve_ref_chain({"type": "object"}, make_openapi_spec()) is None

    def test_resolves_bare_ref(self) -> None:
        spec = make_openapi_spec(
            components={"schemas": {"User": {"type": "object", "properties": {}}}}
        )
        resolved = resolve_ref_chain({"$ref": "#/components/schemas/User"}, spec)
        assert resolved == {"type": "object", "properties": {}}

    def test_chases_alias_chain(self) -> None:
        """A ref whose target is itself a ref is chased to the concrete schema."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Alias": {"$ref": "#/components/schemas/User"},
                    "User": {"type": "object", "properties": {"id": {"type": "integer"}}},
                }
            }
        )
        resolved = resolve_ref_chain({"$ref": "#/components/schemas/Alias"}, spec)
        assert resolved == {"type": "object", "properties": {"id": {"type": "integer"}}}

    def test_combinator_wrapped_ref_resolved(self) -> None:
        spec = make_openapi_spec(
            components={"schemas": {"User": {"type": "object", "properties": {}}}}
        )
        resolved = resolve_ref_chain({"allOf": [{"$ref": "#/components/schemas/User"}]}, spec)
        assert resolved == {"type": "object", "properties": {}}

    def test_cycle_returns_none(self) -> None:
        """A self-referential alias hits the hop cap and gives up."""
        spec = make_openapi_spec(
            components={"schemas": {"Loop": {"$ref": "#/components/schemas/Loop"}}}
        )
        assert resolve_ref_chain({"$ref": "#/components/schemas/Loop"}, spec) is None

    def test_missing_pointer_returns_none(self) -> None:
        spec = make_openapi_spec(components={"schemas": {}})
        assert resolve_ref_chain({"$ref": "#/components/schemas/Nope"}, spec) is None
