"""Tests for ``resources/meta.py`` — ResourceMeta dataclass and schema analysis."""

from __future__ import annotations

from gitea_mcp_server.resources.meta import (
    DETAIL_CONCISE,
    DETAIL_FULL,
    SIZE_LARGE,
    SIZE_MEDIUM,
    SIZE_SMALL,
    SIZE_TINY,
    ResourceMeta,
    default_detail_for,
    derive_size_hint_from_schema,
)

# ── derive_size_hint_from_schema ─────────────────────────────────────────────


class TestDeriveSizeHintFromSchema:
    """Tests for schema-based size estimation."""

    def test_none_schema_is_tiny(self) -> None:
        """None schema should return tiny."""
        assert derive_size_hint_from_schema(None) == SIZE_TINY

    def test_empty_dict_is_tiny(self) -> None:
        """Empty schema dict (no properties, no type) should return tiny."""
        assert derive_size_hint_from_schema({}) == SIZE_TINY

    def test_scalar_schema_is_tiny(self) -> None:
        """Schema with type but no properties (e.g. string) should return tiny."""
        schema = {"type": "string"}
        assert derive_size_hint_from_schema(schema) == SIZE_TINY

    def test_few_properties_is_small(self) -> None:
        """1-5 properties, not an array, should return small."""
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
            },
        }
        assert derive_size_hint_from_schema(schema) == SIZE_SMALL

    def test_five_properties_is_small(self) -> None:
        """Exactly 5 properties, not an array, should return small."""
        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(5)},
        }
        assert derive_size_hint_from_schema(schema) == SIZE_SMALL

    def test_six_properties_is_medium(self) -> None:
        """6-20 properties, not an array, should return medium."""
        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(6)},
        }
        assert derive_size_hint_from_schema(schema) == SIZE_MEDIUM

    def test_twenty_properties_is_medium(self) -> None:
        """Exactly 20 properties, not an array, should return medium."""
        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(20)},
        }
        assert derive_size_hint_from_schema(schema) == SIZE_MEDIUM

    def test_twenty_one_properties_is_large(self) -> None:
        """More than 20 properties should return large."""
        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(21)},
        }
        assert derive_size_hint_from_schema(schema) == SIZE_LARGE

    def test_array_schema_is_large(self) -> None:
        """Array type (even with zero properties) should return large."""
        schema = {"type": "array", "items": {"type": "string"}}
        assert derive_size_hint_from_schema(schema) == SIZE_LARGE

    def test_array_type_list_is_large(self) -> None:
        """Array type expressed as list (e.g. ['array', 'null']) should return large."""
        schema = {"type": ["array", "null"], "items": {"type": "string"}}
        assert derive_size_hint_from_schema(schema) == SIZE_LARGE

    def test_array_schema_with_properties_is_large(self) -> None:
        """Array schema with many properties should still be large (not medium)."""
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {str(i): {"type": "string"} for i in range(3)},
            },
        }
        assert derive_size_hint_from_schema(schema) == SIZE_LARGE

    def test_deeply_nested_is_large(self) -> None:
        """Nesting depth >= 3 should return large regardless of property count."""
        schema = {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {
                                "c": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
        assert derive_size_hint_from_schema(schema) == SIZE_LARGE

    def test_ref_schema_has_no_properties(self) -> None:
        """Schema that is pure $ref has no countable properties → tiny."""
        schema = {"$ref": "#/components/schemas/User"}
        assert derive_size_hint_from_schema(schema) == SIZE_TINY

    def test_array_of_ref_is_large(self) -> None:
        """Array of $ref items should return large (array wins)."""
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Issue"}}
        assert derive_size_hint_from_schema(schema) == SIZE_LARGE


# ── default_detail_for ───────────────────────────────────────────────────────


class TestDefaultDetailFor:
    """Tests for size_hint → default_detail mapping."""

    def test_large_gives_concise(self) -> None:
        assert default_detail_for(SIZE_LARGE) == DETAIL_CONCISE

    def test_medium_gives_full(self) -> None:
        assert default_detail_for(SIZE_MEDIUM) == DETAIL_FULL

    def test_small_gives_full(self) -> None:
        assert default_detail_for(SIZE_SMALL) == DETAIL_FULL

    def test_tiny_gives_full(self) -> None:
        assert default_detail_for(SIZE_TINY) == DETAIL_FULL


# ── ResourceMeta dataclass ──────────────────────────────────────────────────


class TestResourceMeta:
    """Tests for the ResourceMeta dataclass."""

    def test_to_dict_omits_none_values(self) -> None:
        """to_dict should omit all None fields."""
        meta = ResourceMeta()
        result = meta.to_dict()
        assert result == {}

    def test_to_dict_includes_set_fields(self) -> None:
        """to_dict should include non-None fields."""
        meta = ResourceMeta(
            required_scope="read:repository",
            size_hint="large",
            default_detail="concise",
            optional_params=[{"name": "state", "type": "string"}],
            cache_ttl=60.0,
        )
        result = meta.to_dict()
        assert result["required_scope"] == "read:repository"
        assert result["size_hint"] == "large"
        assert result["default_detail"] == "concise"
        assert result["optional_params"] == [{"name": "state", "type": "string"}]
        assert result["cache_ttl"] == 60.0

    def test_to_dict_partial_fields(self) -> None:
        """to_dict should include only the explicitly set non-None fields."""
        meta = ResourceMeta(required_scope="read:issue")
        result = meta.to_dict()
        assert result == {"required_scope": "read:issue"}

    def test_for_schema_derives_size_hint(self) -> None:
        """for_schema should auto-derive size_hint when not explicitly set."""
        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(6)},
        }
        meta = ResourceMeta.for_schema(schema, required_scope="read:repository")
        assert meta.size_hint == SIZE_MEDIUM
        assert meta.required_scope == "read:repository"

    def test_for_schema_explicit_size_hint_overrides(self) -> None:
        """for_schema should use explicit size_hint instead of deriving."""
        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(6)},
        }
        meta = ResourceMeta.for_schema(schema, required_scope="read:repository", size_hint=SIZE_TINY)
        assert meta.size_hint == SIZE_TINY
        assert meta.required_scope == "read:repository"

    def test_for_schema_derives_default_detail_from_size_hint(self) -> None:
        """for_schema should derive default_detail from the (explicit or derived) size_hint."""
        meta = ResourceMeta.for_schema(None, size_hint=SIZE_LARGE)
        assert meta.default_detail == DETAIL_CONCISE

        meta = ResourceMeta.for_schema(None, size_hint=SIZE_SMALL)
        assert meta.default_detail == DETAIL_FULL

    def test_for_schema_explicit_default_detail_overrides(self) -> None:
        """for_schema should use explicit default_detail instead of deriving."""
        meta = ResourceMeta.for_schema(None, size_hint=SIZE_LARGE, default_detail=DETAIL_FULL)
        assert meta.default_detail == DETAIL_FULL

    def test_for_schema_passes_optional_params(self) -> None:
        """for_schema should pass optional_params through."""
        params = [{"name": "state", "type": "string", "values": ["open", "closed"]}]
        meta = ResourceMeta.for_schema(None, optional_params=params)
        assert meta.optional_params == params

    def test_for_schema_passes_cache_ttl(self) -> None:
        """for_schema should pass cache_ttl through."""
        meta = ResourceMeta.for_schema(None, cache_ttl=30.0)
        assert meta.cache_ttl == 30.0

    def test_for_schema_no_schema_derives_tiny(self) -> None:
        """for_schema with None schema should derive size_hint=tiny."""
        meta = ResourceMeta.for_schema(None)
        assert meta.size_hint == SIZE_TINY
        assert meta.default_detail == DETAIL_FULL

    def test_default_detail_for_invalid_size_hint_raises(self) -> None:
        """default_detail_for should raise ValueError for an unknown size_hint."""
        import pytest

        with pytest.raises(ValueError, match="Unknown size_hint"):
            default_detail_for("unknown")
