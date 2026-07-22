"""Tests for gitea_mcp_server/format.py.

Covers all functions in __all__:
- _snake_to_title, _format_datetime, _format_scalar, _format_simple_value
- _resolve_anyof_schema, _format_as_markdown, _format_parameter_table, _format_type
"""

import json

import pytest
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.format import (
    _collapse_data,
    _collapse_value,
    _extract_type_name,
    _format_as_markdown,
    _format_datetime,
    _format_paginated_result,
    _format_parameter_table,
    _format_scalar,
    _format_simple_value,
    _format_type,
    format_result,
    _resolve_anyof_schema,
    _snake_to_title,
)
from gitea_mcp_server.pagination import PAGINATION_KEYS


class TestSnakeToTitle:
    def test_simple_snake_case(self):
        assert _snake_to_title("hello_world") == "Hello World"

    def test_single_word(self):
        assert _snake_to_title("hello") == "Hello"

    def test_camelcase_boundary(self):
        assert _snake_to_title("helloWorld") == "Hello World"

    def test_multiple_underscores(self):
        assert _snake_to_title("get_user_by_id") == "Get User By Id"

    def test_mixed_case_with_underscores(self):
        result = _snake_to_title("issue_list_labels")
        assert result == "Issue List Labels"

    def test_empty_string(self):
        assert _snake_to_title("") == ""

    def test_already_title_cased(self):
        assert _snake_to_title("Created") == "Created"

    def test_with_numbers(self):
        result = _snake_to_title("repo_2fa")
        assert result == "Repo 2Fa"

    def test_space_before_uppercase(self):
        """Names with embedded space before uppercase converts to lowercase."""
        result = _snake_to_title("get URL")
        assert result == "Get Url"


class TestFormatDatetime:
    def test_valid_iso_datetime(self):
        result = _format_datetime("2024-01-15T10:30:00Z")
        assert result == "2024-01-15 10:30:00 UTC"

    def test_none_input(self):
        assert _format_datetime(None) == "N/A"

    def test_empty_string(self):
        assert _format_datetime("") == "N/A"

    def test_invalid_string_passthrough(self):
        assert _format_datetime("not-a-date") == "not-a-date"

    def test_timezone_aware_iso(self):
        result = _format_datetime("2024-06-15T14:30:00+00:00")
        assert result == "2024-06-15 14:30:00 UTC"

    def test_epoch_zero(self):
        result = _format_datetime("1970-01-01T00:00:00Z")
        assert result == "1970-01-01 00:00:00 UTC"


class TestFormatScalar:
    def test_none_returns_na(self):
        assert _format_scalar(None) == "N/A"

    def test_boolean_true(self):
        assert _format_scalar(True) == "True"

    def test_boolean_false(self):
        assert _format_scalar(False) == "False"

    def test_integer(self):
        assert _format_scalar(42) == "42"

    def test_float(self):
        assert _format_scalar(3.14) == "3.14"

    def test_zero_float(self):
        assert _format_scalar(0.0) == "0.0"

    def test_string_passthrough(self):
        assert _format_scalar("hello") == "hello"

    def test_non_string_no_schema(self):
        assert _format_scalar(["a"]) == "['a']"

    def test_datetime_format_with_schema(self):
        schema = {"format": "date-time"}
        result = _format_scalar("2024-01-01T00:00:00Z", schema)
        assert result == "2024-01-01 00:00:00 UTC"

    def test_string_with_schema_no_date_format(self):
        schema = {"format": "email"}
        result = _format_scalar("user@example.com", schema)
        assert result == "user@example.com"

    def test_int_with_schema(self):
        schema = {"format": "int64"}
        assert _format_scalar(123, schema) == "123"


class TestFormatSimpleValue:
    def test_none_returns_na(self):
        assert _format_simple_value(None) == "N/A"

    def test_list_of_strings(self):
        assert _format_simple_value(["a", "b", "c"]) == "a, b, c"

    def test_list_of_mixed_types(self):
        assert _format_simple_value([1, "two", True]) == "1, two, True"

    def test_empty_list(self):
        assert _format_simple_value([]) == ""

    def test_dict(self):
        result = _format_simple_value({"key": "val"})
        assert '"key": "val"' in result

    def test_nested_dict(self):
        result = _format_simple_value({"a": {"b": "c"}})
        assert '"a"' in result

    def test_string(self):
        assert _format_simple_value("plain text") == "plain text"

    def test_integer(self):
        assert _format_simple_value(42) == "42"

    def test_boolean(self):
        assert _format_simple_value(True) == "True"


class TestExtractTypeName:
    """Tests for _extract_type_name — extracts type name from $ref in schemas."""

    def test_direct_ref(self):
        """Direct $ref on the schema itself."""
        schema = {"$ref": "#/components/schemas/Repository"}
        assert _extract_type_name(schema) == "Repository"

    def test_ref_in_anyof(self):
        """$ref nested inside anyOf."""
        schema = {"anyOf": [{"$ref": "#/components/schemas/User"}, {"type": "null"}]}
        assert _extract_type_name(schema) == "User"

    def test_ref_in_oneof(self):
        """$ref nested inside oneOf."""
        schema = {"oneOf": [{"$ref": "#/components/schemas/Label"}, {"type": "string"}]}
        assert _extract_type_name(schema) == "Label"

    def test_no_ref_returns_none(self):
        """Schema without $ref returns None."""
        schema = {"type": "string"}
        assert _extract_type_name(schema) is None

    def test_none_schema_returns_none(self):
        """None input returns None without error."""
        assert _extract_type_name(None) is None

    def test_anyof_without_ref_returns_none(self):
        """anyOf with no $ref options returns None."""
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        assert _extract_type_name(schema) is None

    def test_list_of_non_dict_anyof(self):
        """anyOf containing non-dict options is handled gracefully."""
        schema = {"anyOf": ["string", "integer"]}
        assert _extract_type_name(schema) is None

    def test_ref_in_allof(self):
        """$ref nested inside allOf (common OpenAPI pattern)."""
        schema = {"allOf": [{"$ref": "#/components/schemas/User"}]}
        assert _extract_type_name(schema) == "User"

    def test_allof_combined_anyof(self):
        """allOf with anyOf ref — first found wins."""
        schema = {"allOf": [{"$ref": "#/components/schemas/Repository"}, {"type": "object"}]}
        assert _extract_type_name(schema) == "Repository"


class TestCollapseValue:
    """Tests for _collapse_value — collapses runtime values to compact $ref strings."""

    def test_dict_with_ref(self):
        """Dict with matching $ref schema collapses to $ref:TypeName."""
        schema = {"$ref": "#/components/schemas/User"}
        assert _collapse_value({"id": 1, "login": "me"}, schema) == "$ref:User"

    def test_dict_with_allof_ref(self):
        """Dict with allOf $ref (common OpenAPI pattern) collapses correctly."""
        schema = {"allOf": [{"$ref": "#/components/schemas/Repository"}]}
        assert _collapse_value({"name": "repo"}, schema) == "$ref:Repository"

    def test_dict_with_anyof_ref(self):
        """Dict with anyOf $ref schema collapses to $ref:TypeName."""
        schema = {"anyOf": [{"$ref": "#/components/schemas/Repository"}, {"type": "null"}]}
        assert _collapse_value({"name": "repo"}, schema) == "$ref:Repository"

    def test_dict_without_schema_uses_placeholder(self):
        """Dict with no schema falls back to placeholder."""
        assert _collapse_value({"id": 1}, None) == "{...}"

    def test_dict_without_ref_uses_placeholder(self):
        """Dict with schema but no $ref falls back to placeholder."""
        schema = {"type": "object", "properties": {}}
        assert _collapse_value({"id": 1}, schema) == "{...}"

    def test_list_with_ref(self):
        """List with items.$ref collapses to $ref:TypeName[count]."""
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Label"}}
        assert _collapse_value([{"id": 1}, {"id": 2}], schema) == "$ref:Label[2]"

    def test_list_without_ref_shows_count(self):
        """List without $ref items falls back to count."""
        schema = {"type": "array", "items": {"type": "object"}}
        assert _collapse_value([{"x": 1}], schema) == "[1 items]"

    def test_list_with_none_schema(self):
        """List with no schema falls back to count."""
        assert _collapse_value([1, 2, 3], None) == "[3 items]"

    def test_list_with_ref_in_anyof_items(self):
        """Array items with anyOf $ref collapses correctly."""
        schema = {
            "type": "array",
            "items": {"anyOf": [{"$ref": "#/components/schemas/Issue"}, {"type": "null"}]},
        }
        assert _collapse_value([{"title": "bug"}], schema) == "$ref:Issue[1]"

    def test_scalar_passthrough(self):
        """Non-dict, non-list values are stringified."""
        assert _collapse_value("hello", None) == "hello"
        assert _collapse_value(42, None) == "42"


class TestCollapseData:
    """Tests for _collapse_data — walk data+schema, collapse $ref objects at depth>=1.

    This function shapes data for any formatter (json or markdown).
    """

    def test_full_detail_returns_unchanged(self):
        """detail='full' returns data unchanged regardless of schema."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"owner": {"$ref": "#/components/schemas/User"}}}
        result = _collapse_data(data, schema, _depth=0, detail="full")
        assert result is data

    def test_depth_0_no_collapse(self):
        """At depth=0, no collapse occurs even with detail='concise'."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"owner": {"$ref": "#/components/schemas/User"}}}
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        # Depth=0 is never collapsed — the top-level dict stays
        assert isinstance(result, dict)
        assert "owner" in result
        assert "login" not in result  # should have been collapsed

    def test_depth_1_dict_with_ref_collapses(self):
        """At depth>=1, a dict with $ref schema collapses to $ref:TypeName."""
        data = {"user": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"user": {"$ref": "#/components/schemas/User"}}}
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert result["user"] == "$ref:User"

    def test_depth_1_list_with_ref_collapses(self):
        """At depth>=1, a list with $ref items collapses to $ref:TypeName[N]."""
        data = {"labels": [{"id": 1, "name": "bug"}, {"id": 2, "name": "feature"}]}
        schema = {
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"$ref": "#/components/schemas/Label"}},
            },
        }
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert result["labels"] == "$ref:Label[2]"

    def test_inline_schema_not_collapsed(self):
        """Inline schemas (no $ref) are NOT collapsed — they remain as nested dicts."""
        data = {"config": {"host": "localhost", "port": 8080}}
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"host": {"type": "string"}, "port": {"type": "integer"}},
                },
            },
        }
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert isinstance(result["config"], dict)
        assert result["config"]["host"] == "localhost"

    def test_allof_ref_collapses(self):
        """allOf with $ref is resolved and collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {"owner": {"allOf": [{"$ref": "#/components/schemas/User"}]}},
        }
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert result["owner"] == "$ref:User"

    def test_anyof_ref_collapses(self):
        """anyOf with $ref is resolved and collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {
                "owner": {"anyOf": [{"$ref": "#/components/schemas/User"}, {"type": "null"}]},
            },
        }
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert result["owner"] == "$ref:User"

    def test_none_no_collapse(self):
        """schema=None means no collapse occurs (data passed through)."""
        data = {"owner": {"id": 1, "login": "user1"}}
        result = _collapse_data(data, None, _depth=0, detail="concise")
        assert result is data

    def test_nested_mixed(self):
        """Mixed $ref and inline schemas: only $ref properties collapse."""
        data = {
            "meta": {
                "owner": {"id": 1, "login": "user1"},
                "description": "a repo",
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {
                        "owner": {"$ref": "#/components/schemas/User"},
                        "description": {"type": "string"},
                    },
                },
            },
        }
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        meta = result["meta"]
        assert meta["owner"] == "$ref:User"
        assert meta["description"] == "a repo"

    def test_list_at_depth_0_no_collapse(self):
        """Top-level list is not collapsed."""
        data = [{"id": 1, "login": "user1"}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/User"}}
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert isinstance(result, list)
        assert len(result) == 1


class TestResolveAnyOfSchema:
    def test_none_returns_none(self):
        assert _resolve_anyof_schema(None) is None

    def test_anyof_with_object_returns_first_object(self):
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "object", "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == "object"
        assert "id" in result["properties"]

    def test_anyof_only_scalars_returns_original(self):
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_oneof_with_object_returns_first_object(self):
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "object", "properties": {"name": {"type": "string"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == "object"

    def test_anyof_object_no_properties_skipped(self):
        schema = {
            "anyOf": [
                {"type": "object"},
                {"type": "object", "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert "id" in result["properties"]

    def test_no_anyof_or_oneof(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_empty_list(self):
        schema = {"anyOf": []}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_only_non_dict_items(self):
        schema = {"anyOf": ["string", 42]}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_object_with_null_properties(self):
        schema = {
            "anyOf": [
                {"type": "object", "properties": None},
                {"type": "object", "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == "object"
        assert "id" in result["properties"]


class TestFormatAsMarkdown:
    def test_none_input(self):
        result = _format_as_markdown(None)
        assert result == "N/A"

    def test_none_input_with_title(self):
        result = _format_as_markdown(None, title="Test")
        assert "Test" in result
        assert "N/A" in result

    def test_scalar_value(self):
        result = _format_as_markdown("hello")
        assert result == "hello"

    def test_integer_scalar(self):
        result = _format_as_markdown(42)
        assert result == "42"

    def test_empty_list(self):
        result = _format_as_markdown([])
        assert "*None*" in result

    def test_list_of_scalars_with_schema(self):
        schema = {"type": "array", "items": {"type": "string"}}
        result = _format_as_markdown(["a", "b", "c"], schema)
        assert "a, b, c" in result

    def test_list_of_scalars_no_schema(self):
        result = _format_as_markdown(["a", "b"])
        assert "- a" in result
        assert "- b" in result

    def test_list_of_dicts(self):
        data = [{"name": "Alice"}, {"name": "Bob"}]
        result = _format_as_markdown(data)
        assert "Alice" in result
        assert "Bob" in result
        assert "| Name |" in result

    def test_list_of_dicts_with_schema(self):
        data = [{"id": 1, "name": "Foo"}]
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        }
        result = _format_as_markdown(data, schema)
        assert "Foo" in result
        assert "1" in result or "1" in result

    def test_empty_dict(self):
        result = _format_as_markdown({})
        assert "*Empty*" in result

    def test_dict_with_properties_renders_table(self):
        data = {"name": "test", "count": 3}
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
        }
        result = _format_as_markdown(data, schema)
        assert "| Property | Value |" in result
        assert "Name" in result or "name" in result
        assert "test" in result
        assert "Count" in result or "count" in result
        assert "3" in result

    def test_dict_with_nested_dict_section(self):
        data = {"profile": {"age": 30}}
        schema = {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "object",
                    "properties": {"age": {"type": "integer"}},
                }
            },
        }
        result = _format_as_markdown(data, schema)
        assert "**Profile:**" in result or "Profile" in result

    def test_dict_with_nested_list(self):
        data = {"items": [{"x": 1}, {"x": 2}]}
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"x": {"type": "integer"}}},
                }
            },
        }
        result = _format_as_markdown(data, schema)
        assert "Items" in result or "items" in result

    def test_title_at_top_level(self):
        result = _format_as_markdown("hello", title="MyTitle")
        assert "MyTitle" in result
        assert "hello" in result

    def test_title_at_top_level_for_scalar(self):
        result = _format_as_markdown(42, title="The Answer")
        assert "The Answer" in result
        assert "42" in result

    def test_title_with_dict(self):
        result = _format_as_markdown({"key": "val"}, title="MyTitle")
        assert "# MyTitle" in result
        assert "| Key | val |" in result
        assert "val" in result

    def test_title_with_list(self):
        result = _format_as_markdown(["a", "b"], title="MyTitle")
        assert "# MyTitle" in result
        assert "a" in result
        assert "b" in result

    def test_allof_merged_schema(self):
        data = {"title": "Issue", "body": "Text"}
        schema = {
            "type": "object",
            "allOf": [
                {"properties": {"title": {"type": "string"}}},
                {"properties": {"body": {"type": "string"}}},
            ],
        }
        result = _format_as_markdown(data, schema)
        assert "Title" in result
        assert "Body" in result

    def test_allof_without_properties(self):
        data = {"a": 1}
        schema = {"type": "object", "allOf": [{"type": "object"}]}
        result = _format_as_markdown(data, schema)
        assert "a" in result or "A" in result

    def test_properties_without_schema_flat(self):
        data = {"key": "val"}
        result = _format_as_markdown(data)
        assert "|" in result

    def test_datetime_property_formatted(self):
        data = {"created_at": "2024-01-01T12:00:00Z"}
        schema = {
            "type": "object",
            "properties": {
                "created_at": {"type": "string", "format": "date-time"}
            },
        }
        result = _format_as_markdown(data, schema)
        assert "2024-01-01" in result
        assert "12:00:00" in result

    def test_anyof_resolved_in_properties(self):
        data = {"owner": {"login": "user"}}
        schema = {
            "type": "object",
            "properties": {
                "owner": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "object", "properties": {"login": {"type": "string"}}},
                    ]
                }
            },
        }
        result = _format_as_markdown(data, schema)
        assert "user" in result

    def test_nested_section_with_depth(self):
        """Nested section at depth>0 uses indent-bold format."""
        data = {
            "config": {
                "database": {
                    "host": "localhost",
                    "port": 5432,
                }
            }
        }
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "database": {
                            "type": "object",
                            "properties": {
                                "host": {"type": "string"},
                                "port": {"type": "integer"},
                            },
                        }
                    },
                }
            },
        }
        result = _format_as_markdown(data, schema)
        # Should contain the bold label format at depth > 0
        assert "Host" in result or "Port" in result or "database" in result

    def test_dict_concise_collapses_nested_at_depth(self):
        """detail='concise' collapses nested objects at depth>=1 to $ref:TypeName.

        The collapse triggers when a property VALUE is a dict or list AND
        the current nesting depth (_depth) is >= 1.  Top-level properties
        (_depth=0) are always expanded — they become sections.
        """
        # Outer wrapper pushes 'owner' and 'repo' to _depth=1
        data = {
            "details": {
                "owner": {"id": 1, "login": "user1"},
                "repo": {"id": 10, "name": "my-repo"},
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "details": {
                    "type": "object",
                    "properties": {
                        "owner": {
                            "allOf": [{"$ref": "#/components/schemas/User"}],
                        },
                        "repo": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/Repository"},
                                {"type": "null"},
                            ],
                        },
                    },
                },
            },
        }
        result = _format_as_markdown(data, schema, detail="concise")
        # The nested values at depth>=1 should be collapsed to $ref labels
        assert "$ref:User" in result
        assert "$ref:Repository" in result
        # Original values should NOT be visible since they're collapsed
        assert "user1" not in result
        assert "my-repo" not in result

    def test_dict_concise_collapses_list_at_depth(self):
        """detail='concise' at depth>=1 collapses list to $ref:TypeName[N]."""
        data = {
            "nested": {
                "labels": [{"id": 1, "name": "bug"}, {"id": 2, "name": "feature"}],
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {
                        "labels": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Label"},
                        },
                    },
                },
            },
        }
        result = _format_as_markdown(data, schema, detail="concise")
        assert "$ref:Label[2]" in result
        assert "bug" not in result
        assert "feature" not in result

    def test_dict_concise_top_level_stays_expanded(self):
        """detail='concise' at depth=0 keeps top-level nested objects expanded."""
        data = {
            "user": {"id": 1, "login": "testuser"},
        }
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "login": {"type": "string"}},
                },
            },
        }
        result = _format_as_markdown(data, schema, detail="concise")
        # At depth=0, nested objects are sections, not collapsed
        assert "testuser" in result

    def test_property_schema_not_a_dict_skipped(self):
        """Property schema that is not a dict is skipped gracefully."""
        data = {
            "name": "test",
            "ref": "abc123",
        }
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "ref": "$ref: #/components/schemas/Ref",
            },
        }
        # If ref prop schema is not a dict (it's a string), it should be skipped
        result = _format_as_markdown(data, schema)
        assert "Name" in result

    def test_non_dict_non_list_input(self):
        assert _format_as_markdown(True) == "True"

    # ── field_filter and item_title_key hooks ──────────────────────────────────────

    def test_field_filter_on_dict_selects_subset(self):
        """field_filter shows only the specified properties."""
        data = {"id": 1, "name": "Alice", "email": "alice@test.com", "role": "admin"}
        result = _format_as_markdown(data, field_filter=["id", "name"])
        assert "| Id | 1 |" in result
        assert "| Name | Alice |" in result
        assert "Email" not in result
        assert "Role" not in result

    def test_field_filter_on_list_of_dicts(self):
        """field_filter applies to each item in a list of dicts."""
        data = [
            {"id": 1, "name": "Foo", "extra": "x"},
            {"id": 2, "name": "Bar", "extra": "y"},
        ]
        result = _format_as_markdown(data, field_filter=["id", "name"])
        for row in ("Foo", "Bar", "1", "2"):
            assert row in result
        assert "extra" not in result.lower() and "Extra" not in result

    def test_field_filter_skips_missing_keys_gracefully(self):
        """field_filter entries not in data are silently skipped."""
        data = {"name": "Alice"}
        result = _format_as_markdown(data, field_filter=["name", "nonexistent"])
        assert "| Name | Alice |" in result
        assert "nonexistent" not in result.lower()

    def test_item_title_key_customizes_list_headings(self):
        """item_title_key uses the specified field value as the item heading."""
        data = [{"number": 42, "title": "Bug fix"}, {"number": 43, "title": "Feature"}]
        result = _format_as_markdown(data, item_title_key="title")
        assert "# Bug fix" in result
        assert "# Feature" in result
        assert "| Number | 42 |" in result
        assert "| Number | 43 |" in result

    def test_item_title_key_falls_back_to_item_n_when_missing(self):
        """When item_title_key field is missing, falls back to 'Item N'."""
        data = [{"id": 1, "name": "Alice"}]
        result = _format_as_markdown(data, item_title_key="nonexistent")
        assert "# Item 1" in result
        assert "| Id | 1 |" in result

    def test_field_filter_and_item_title_key_together(self):
        """Both hooks can be used together."""
        data = [{"number": 1, "title": "Bug", "body": "Details"}]
        result = _format_as_markdown(
            data,
            field_filter=["number", "title"],
            item_title_key="title",
        )
        assert "# Bug" in result
        assert "| Number | 1 |" in result
        assert "| Title | Bug |" in result
        assert "Body" not in result and "body" not in result

    # ── Consistency: tool and resource should produce same structure ──────────────

    def test_format_produces_nested_sub_tables_for_nested_objects(self):
        """Nested dicts render as bold sub-sections with sub-tables (not dot-path)."""
        data = {"user": {"id": 12, "login": "dev2"}, "labels": [{"name": "Cleanup"}]}
        result = _format_as_markdown(data)
        # User appears as a nested sub-section, not as dot-path keys
        assert "**User:**" in result or "## User" in result
        # Labels appears as a nested section
        assert "**Labels:**" in result or "## Labels" in result
        # Dot-path keys should NOT appear
        assert "user.id" not in result
        assert "labels.Name" not in result


class TestFormatType:
    """Tests for _format_type — type enrichment with enum/array info."""

    def test_plain_type_unchanged(self):
        """No enum, no array items — returns basic type."""
        assert _format_type({"type": "string"}) == "string"
        assert _format_type({"type": "integer"}) == "integer"
        assert _format_type({"type": "boolean"}) == "boolean"

    def test_fallback_when_no_type(self):
        """No type key — returns 'any'."""
        assert _format_type({}) == "any"

    def test_enum_appends_values(self):
        """Enum values appear as type [val1, val2, ...]."""
        prop = {"type": "string", "enum": ["merge", "rebase", "squash"]}
        assert _format_type(prop) == "string [merge, rebase, squash]"

    def test_enum_with_integer_values(self):
        """Non-string enum values are stringified."""
        prop = {"type": "integer", "enum": [1, 2, 3]}
        assert _format_type(prop) == "integer [1, 2, 3]"

    def test_array_with_items_properties(self):
        """Array with items.properties shows array of {key1, key2}."""
        prop = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        }
        assert _format_type(prop) == "array of {operation, path, content}"

    def test_array_without_items(self):
        """Array without items schema — unchanged."""
        assert _format_type({"type": "array"}) == "array"

    def test_array_with_items_no_properties(self):
        """Array items with no properties — unchanged."""
        prop = {"type": "array", "items": {"type": "string"}}
        assert _format_type(prop) == "array"

    def test_enum_takes_priority_over_array(self):
        """When both enum and array are present, enum wins."""
        prop = {
            "type": "array",
            "enum": ["create", "update", "delete"],
            "items": {"type": "string"},
        }
        assert _format_type(prop) == "array [create, update, delete]"


class TestFormatParameterTable:
    """Tests for _format_parameter_table — the markdown parameter table."""

    def test_plain_params(self):
        """Basic string/integer params render without enrichment."""
        props = {
            "owner": {"type": "string", "description": "owner of the repo"},
            "index": {"type": "integer", "description": "issue index"},
        }
        result = _format_parameter_table(props, ["owner", "index"])
        assert "| owner | string | yes | owner of the repo |" in result
        assert "| index | integer | yes | issue index |" in result
        assert "## Parameters" in result

    def test_enum_param(self):
        """Enum param shows values in type column."""
        props = {
            "Do": {
                "type": "string",
                "enum": ["merge", "rebase", "squash"],
            },
        }
        result = _format_parameter_table(props, ["Do"])
        assert "| Do | string [merge, rebase, squash] | yes |  |" in result

    def test_array_param(self):
        """Array param with items.properties shows item keys."""
        props = {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string"},
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                "description": "list of file operations",
            },
        }
        result = _format_parameter_table(props, ["files"])
        assert "| files | array of {operation, path, content} | yes | list of file operations |" in result

    def test_optional_param(self):
        """Non-required param gets 'no' in Required column."""
        props = {
            "message": {"type": "string", "description": "commit message"},
        }
        result = _format_parameter_table(props, [])
        assert "| message | string | no | commit message |" in result

    def test_description_escapes_pipe(self):
        """Pipe characters in description are escaped."""
        props = {
            "owner": {"type": "string", "description": "owner|repo"},
        }
        result = _format_parameter_table(props, ["owner"])
        assert r"| owner | string | yes | owner\|repo |" in result

    def test_invalid_prop_skipped(self):
        """Non-dict properties are skipped without error."""
        props = {"bad": "not a dict"}
        result = _format_parameter_table(props, [])
        assert "bad" not in result
        assert "## Parameters" in result

    def test_empty_properties(self):
        """Empty properties produces header with no data rows."""
        result = _format_parameter_table({}, [])
        assert "## Parameters" in result
        assert "Parameter | Type | Required | Description" in result
        # No data row below the separator
        header_end = result.index("|-----------")
        rest = result[header_end:]
        # Only blank line after separator, no `| owner |` etc.
        assert rest.strip() == "|-----------|------|----------|-------------|"


# ============================================================================
# _format_paginated_result tests
# ============================================================================


class TestFormatPaginatedResult:
    """Tests for _format_paginated_result shared display utility."""

    def test_returns_paginated_toolresult(self):
        """Returns a ToolResult with structured_content containing result."""
        result = _format_paginated_result(
            [{"id": 1}, {"id": 2}], 2, "raw", page=1, limit=10,
        )
        assert isinstance(result, ToolResult)
        assert result.structured_content is not None
        assert "result" in result.structured_content

    def test_respects_page_and_limit(self):
        """When fetch_all=False, only returns the requested page."""
        items = [{"id": i} for i in range(25)]
        result = _format_paginated_result(items, 25, "raw", page=2, limit=10)
        sc = result.structured_content
        assert len(sc["result"]) == 10
        assert sc["result"][0]["id"] == 10
        assert sc["result"][-1]["id"] == 19
        assert sc["has_more"] is True
        assert sc["next_offset"] == 3
        assert sc["total_count"] == 25

    def test_last_page(self):
        """Last page returns fewer items and has_more=False."""
        items = [{"id": i} for i in range(25)]
        result = _format_paginated_result(items, 25, "raw", page=3, limit=10)
        sc = result.structured_content
        assert len(sc["result"]) == 5
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    def test_fetch_all_returns_all_items(self):
        """When fetch_all=True, all items are returned without slicing."""
        items = [{"id": i} for i in range(50)]
        result = _format_paginated_result(
            items, 50, "raw", page=1, limit=10, fetch_all=True,
        )
        sc = result.structured_content
        assert len(sc["result"]) == 50
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 50

    def test_fetch_all_empty_list(self):
        """When fetch_all=True and items is empty, returns empty."""
        result = _format_paginated_result(
            [], 0, "raw", page=1, limit=10, fetch_all=True,
        )
        sc = result.structured_content
        assert sc["result"] == []
        assert sc["has_more"] is False
        assert sc["total_count"] == 0

    def test_markdown_format(self):
        """Markdown format produces text content."""
        items = [{"id": 1, "name": "test"}]
        result = _format_paginated_result(
            items, 1, "markdown", page=1, limit=10,
        )
        assert result.content is not None
        assert len(result.content) > 0
        text = result.content[0].text
        assert "test" in text
        # Verify pagination metadata is in structured_content
        sc = result.structured_content
        assert sc["total_count"] == 1

    def test_json_format(self):
        """JSON format produces JSON text content."""
        items = [{"id": 1, "name": "test"}]
        result = _format_paginated_result(
            items, 1, "json", page=1, limit=10,
        )
        assert result.content is not None
        text = result.content[0].text
        parsed = json.loads(text)
        assert parsed[0]["name"] == "test"
        sc = result.structured_content
        assert sc["total_count"] == 1

    def test_pagination_keys_in_structured_content(self):
        """PAGINATION_KEYS keys appear in structured_content."""
        items = [{"id": i} for i in range(25)]
        result = _format_paginated_result(items, 25, "raw", page=1, limit=10)
        for key in PAGINATION_KEYS:
            assert key in result.structured_content

    def test_empty_items_list(self):
        """Empty items list returns empty result."""
        result = _format_paginated_result(
            [], 0, "raw", page=1, limit=10,
        )
        sc = result.structured_content
        assert sc["result"] == []
        assert sc["total_count"] == 0

    def test_markdown_extras_appended(self):
        """markdown_extras appear as additional sections in markdown output."""
        items = [{"id": 1}]
        result = _format_paginated_result(
            items, 1, "markdown", page=1, limit=10,
            markdown_extras=["**Extra section:** content"],
        )
        text = result.content[0].text
        assert "**Extra section:** content" in text


# ============================================================================
# format_result - detail=concise with JSON output
# ============================================================================


class TestFormatResultConcise:
    """Tests for format_result with detail=concise in JSON mode."""

    def _make_result(self, data: dict | list) -> ToolResult:
        return ToolResult(structured_content={"result": data})

    def _output_schema(self, result_schema: dict) -> dict:
        return {"type": "object", "properties": {"result": result_schema}}

    def test_json_full_no_collapse(self):
        """detail='full' (default) with JSON returns complete data unchanged."""
        data = {"owner": {"id": 1, "login": "user1"}, "name": "repo"}
        schema = self._output_schema({
            "type": "object",
            "properties": {
                "owner": {"$ref": "#/components/schemas/User"},
                "name": {"type": "string"},
            },
        })
        result = format_result(self._make_result(data), "json", output_schema=schema, detail="full")
        assert result.content is not None
        parsed = json.loads(result.content[0].text)
        assert isinstance(parsed["owner"], dict)
        assert parsed["owner"]["login"] == "user1"

    def test_json_concise_collapses_ref_dict(self):
        """detail='concise' + json collapses $ref dicts to labels."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = self._output_schema({
            "type": "object",
            "properties": {"owner": {"$ref": "#/components/schemas/User"}},
        })
        result = format_result(self._make_result(data), "json", output_schema=schema, detail="concise")
        parsed = json.loads(result.content[0].text)
        assert parsed["owner"] == "$ref:User"

    def test_json_concise_collapses_ref_list(self):
        """detail='concise' + json collapses $ref lists to labels."""
        data = {"labels": [{"id": 1, "name": "bug"}, {"id": 2, "name": "feature"}]}
        schema = self._output_schema({
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"$ref": "#/components/schemas/Label"}},
            },
        })
        result = format_result(self._make_result(data), "json", output_schema=schema, detail="concise")
        parsed = json.loads(result.content[0].text)
        assert parsed["labels"] == "$ref:Label[2]"

    def test_json_concise_inline_not_collapsed(self):
        """Inline schemas (no $ref) remain expanded even with detail='concise'."""
        data = {"config": {"host": "localhost", "port": 8080}}
        schema = self._output_schema({
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"host": {"type": "string"}, "port": {"type": "integer"}},
                },
            },
        })
        result = format_result(self._make_result(data), "json", output_schema=schema, detail="concise")
        parsed = json.loads(result.content[0].text)
        assert isinstance(parsed["config"], dict)
        assert parsed["config"]["host"] == "localhost"

    def test_json_concise_top_level_object_stays(self):
        """Top-level object is not collapsed."""
        data = {"name": "repo", "description": "a test repo"}
        schema = self._output_schema({
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
        })
        result = format_result(self._make_result(data), "json", output_schema=schema, detail="concise")
        parsed = json.loads(result.content[0].text)
        assert parsed["name"] == "repo"
        assert parsed["description"] == "a test repo"

    def test_markdown_concise_no_regression(self):
        """Existing markdown concise behavior is preserved."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = self._output_schema({
            "type": "object",
            "properties": {"owner": {"$ref": "#/components/schemas/User"}},
        })
        result = format_result(self._make_result(data), "markdown", output_schema=schema, detail="concise")
        assert result.content is not None
        assert "$ref:User" in result.content[0].text

    def test_no_output_schema_fallback(self):
        """When output_schema is None, concise is a no-op (data unchanged)."""
        data = {"owner": {"id": 1, "login": "user1"}}
        result = format_result(self._make_result(data), "json", output_schema=None, detail="concise")
        parsed = json.loads(result.content[0].text)
        assert isinstance(parsed["owner"], dict)
        assert parsed["owner"]["login"] == "user1"

    def test_raw_passthrough(self):
        """format='raw' ignores detail — data is not collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        result = format_result(self._make_result(data), "raw", detail="concise")
        # Raw returns early, structured_content is the original data
        sc = result.structured_content
        assert sc is not None
        assert isinstance(sc["result"], dict)
        assert sc["result"]["owner"]["login"] == "user1"
