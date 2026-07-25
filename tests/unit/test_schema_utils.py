"""Unit tests for gitea_mcp_server/schema_utils.py.

Covers both exported functions:
- ``schema_type_matches`` — type-as-list-aware matching
- ``get_schema_type`` — extract primary type from type-as-list
"""

from gitea_mcp_server.schema_utils import get_schema_type, schema_type_matches


class TestSchemaTypeMatches:
    """Tests for schema_type_matches — type-as-list-aware matching."""

    # --- String form ---

    def test_string_type_matches(self):
        """Schema with ``type: "object"`` matches ``"object"``."""
        assert schema_type_matches({"type": "object"}, "object") is True

    def test_string_type_mismatch(self):
        """Schema with ``type: "string"`` does not match ``"object"``."""
        assert schema_type_matches({"type": "string"}, "object") is False

    # --- List form ---

    def test_list_type_contains_matches(self):
        """Schema with ``type: ["array", "null"]`` matches ``"array"``."""
        assert schema_type_matches({"type": ["array", "null"]}, "array") is True

    def test_list_type_mismatch(self):
        """Schema with ``type: ["string", "null"]`` does not match ``"array"``."""
        assert schema_type_matches({"type": ["string", "null"]}, "array") is False

    def test_list_type_single_element(self):
        """Schema with ``type: ["array"]`` matches ``"array"``."""
        assert schema_type_matches({"type": ["array"]}, "array") is True

    def test_list_type_multiple_non_null(self):
        """Schema with ``type: ["object", "array"]`` matches both."""
        assert schema_type_matches({"type": ["object", "array"]}, "object") is True
        assert schema_type_matches({"type": ["object", "array"]}, "array") is True

    # --- Edge cases ---

    def test_no_type_key(self):
        """Schema without ``type`` key does not match any type."""
        assert schema_type_matches({}, "object") is False

    def test_type_is_none(self):
        """Schema with ``type: None`` does not match."""
        assert schema_type_matches({"type": None}, "object") is False

    def test_type_is_non_string_non_list(self):
        """Schema with ``type: 42`` (unexpected, but defensive) does not match."""
        assert schema_type_matches({"type": 42}, "object") is False

    # --- Specific type checks ---

    def test_file_type_string(self):
        """Schema with ``type: "file"`` matches ``"file"`` (Swagger 2.0 file upload)."""
        assert schema_type_matches({"type": "file"}, "file") is True

    def test_file_type_list(self):
        """Schema with ``type: ["file", "null"]`` matches ``"file"`` (defensive)."""
        assert schema_type_matches({"type": ["file", "null"]}, "file") is True

    def test_null_type_string(self):
        """Schema with ``type: "null"`` matches ``"null"``."""
        assert schema_type_matches({"type": "null"}, "null") is True

    def test_null_type_in_list(self):
        """Schema with ``type: ["null"]`` matches ``"null"``."""
        assert schema_type_matches({"type": ["null"]}, "null") is True


class TestGetSchemaType:
    """Tests for get_schema_type — primary type extraction."""

    def test_string_type(self):
        """Plain string type is returned as-is."""
        assert get_schema_type({"type": "object"}) == "object"

    def test_list_type_picks_first_non_null(self):
        """Type list picks the first non-null element."""
        assert get_schema_type({"type": ["object", "null"]}) == "object"
        assert get_schema_type({"type": ["array", "null"]}) == "array"

    def test_list_type_single_element(self):
        """Single-element list is returned."""
        assert get_schema_type({"type": ["string"]}) == "string"

    def test_list_type_all_null(self):
        """All-null type list returns ``"null"`` (the first element)."""
        assert get_schema_type({"type": ["null"]}) == "null"

    def test_list_type_multiple_non_null_returns_first(self):
        """Multiple non-null types — returns the first one."""
        assert get_schema_type({"type": ["object", "string"]}) == "object"

    def test_no_type_key_returns_none(self):
        """No type key returns None."""
        assert get_schema_type({}) is None

    def test_type_is_none_returns_none(self):
        """``type: None`` returns None."""
        assert get_schema_type({"type": None}) is None

    def test_type_is_non_string_non_list_returns_none(self):
        """Unexpected type value returns None."""
        assert get_schema_type({"type": 42}) is None
