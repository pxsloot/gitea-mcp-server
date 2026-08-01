"""Tests for gitea_mcp_server/format.py.

Covers all functions in __all__:
- _snake_to_title, _format_datetime, _format_scalar, _format_simple_value
- _resolve_anyof_schema, _format_as_markdown, _format_parameter_table, _format_type
"""

import json
from typing import Any

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
    _resolve_anyof_schema,
    _snake_to_title,
    apply_format,
)
from gitea_mcp_server.models import (
    ToolSchemaResult,  # noqa: TC001 — used as runtime annotation in test helpers
)
from gitea_mcp_server.pagination import PAGINATION_KEYS
from tests.helpers.mcp_results import extract_text_content, get_structured, parse_json_content


class TestSnakeToTitle:
    def test_simple_snake_case(self) -> None:
        assert _snake_to_title("hello_world") == "Hello World"

    def test_single_word(self) -> None:
        assert _snake_to_title("hello") == "Hello"

    def test_camelcase_boundary(self) -> None:
        assert _snake_to_title("helloWorld") == "Hello World"

    def test_multiple_underscores(self) -> None:
        assert _snake_to_title("get_user_by_id") == "Get User By Id"

    def test_mixed_case_with_underscores(self) -> None:
        result = _snake_to_title("issue_list_labels")
        assert result == "Issue List Labels"

    def test_empty_string(self) -> None:
        assert _snake_to_title("") == ""

    def test_already_title_cased(self) -> None:
        assert _snake_to_title("Created") == "Created"

    def test_with_numbers(self) -> None:
        result = _snake_to_title("repo_2fa")
        assert result == "Repo 2Fa"

    def test_space_before_uppercase(self) -> None:
        """Names with embedded space before uppercase converts to lowercase."""
        result = _snake_to_title("get URL")
        assert result == "Get Url"


class TestFormatDatetime:
    def test_valid_iso_datetime(self) -> None:
        result = _format_datetime("2024-01-15T10:30:00Z")
        assert result == "2024-01-15 10:30:00 UTC"

    def test_none_input(self) -> None:
        assert _format_datetime(None) == "N/A"

    def test_empty_string(self) -> None:
        assert _format_datetime("") == "N/A"

    def test_invalid_string_passthrough(self) -> None:
        assert _format_datetime("not-a-date") == "not-a-date"

    def test_timezone_aware_iso(self) -> None:
        result = _format_datetime("2024-06-15T14:30:00+00:00")
        assert result == "2024-06-15 14:30:00 UTC"

    def test_epoch_zero(self) -> None:
        result = _format_datetime("1970-01-01T00:00:00Z")
        assert result == "1970-01-01 00:00:00 UTC"


class TestFormatScalar:
    def test_none_returns_na(self) -> None:
        assert _format_scalar(None) == "N/A"

    def test_boolean_true(self) -> None:
        assert _format_scalar(True) == "True"

    def test_boolean_false(self) -> None:
        assert _format_scalar(False) == "False"

    def test_integer(self) -> None:
        assert _format_scalar(42) == "42"

    def test_float(self) -> None:
        assert _format_scalar(3.14) == "3.14"

    def test_zero_float(self) -> None:
        assert _format_scalar(0.0) == "0.0"

    def test_string_passthrough(self) -> None:
        assert _format_scalar("hello") == "hello"

    def test_non_string_no_schema(self) -> None:
        assert _format_scalar(["a"]) == "['a']"

    def test_datetime_format_with_schema(self) -> None:
        schema = {"format": "date-time"}
        result = _format_scalar("2024-01-01T00:00:00Z", schema)
        assert result == "2024-01-01 00:00:00 UTC"

    def test_string_with_schema_no_date_format(self) -> None:
        schema = {"format": "email"}
        result = _format_scalar("user@example.com", schema)
        assert result == "user@example.com"

    def test_int_with_schema(self) -> None:
        schema = {"format": "int64"}
        assert _format_scalar(123, schema) == "123"


class TestFormatSimpleValue:
    def test_none_returns_na(self) -> None:
        assert _format_simple_value(None) == "N/A"

    def test_list_of_strings(self) -> None:
        assert _format_simple_value(["a", "b", "c"]) == "a, b, c"

    def test_list_of_mixed_types(self) -> None:
        assert _format_simple_value([1, "two", True]) == "1, two, True"

    def test_empty_list(self) -> None:
        assert _format_simple_value([]) == ""

    def test_dict(self) -> None:
        result = _format_simple_value({"key": "val"})
        assert '"key": "val"' in result

    def test_nested_dict(self) -> None:
        result = _format_simple_value({"a": {"b": "c"}})
        assert '"a"' in result

    def test_string(self) -> None:
        assert _format_simple_value("plain text") == "plain text"

    def test_integer(self) -> None:
        assert _format_simple_value(42) == "42"

    def test_boolean(self) -> None:
        assert _format_simple_value(True) == "True"


class TestExtractTypeName:
    """Tests for _extract_type_name — extracts type name from $ref in schemas."""

    def test_direct_ref(self) -> None:
        """Direct $ref on the schema itself."""
        schema = {"$ref": "#/components/schemas/Repository"}
        assert _extract_type_name(schema) == "Repository"

    def test_ref_in_anyof(self) -> None:
        """$ref nested inside anyOf."""
        schema = {"anyOf": [{"$ref": "#/components/schemas/User"}, {"type": "null"}]}
        assert _extract_type_name(schema) == "User"

    def test_ref_in_oneof(self) -> None:
        """$ref nested inside oneOf."""
        schema = {"oneOf": [{"$ref": "#/components/schemas/Label"}, {"type": "string"}]}
        assert _extract_type_name(schema) == "Label"

    def test_no_ref_returns_none(self) -> None:
        """Schema without $ref returns None."""
        schema = {"type": "string"}
        assert _extract_type_name(schema) is None

    def test_none_schema_returns_none(self) -> None:
        """None input returns None without error."""
        assert _extract_type_name(None) is None

    def test_anyof_without_ref_returns_none(self) -> None:
        """anyOf with no $ref options returns None."""
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        assert _extract_type_name(schema) is None

    def test_list_of_non_dict_anyof(self) -> None:
        """anyOf containing non-dict options is handled gracefully."""
        schema = {"anyOf": ["string", "integer"]}
        assert _extract_type_name(schema) is None

    def test_ref_in_allof(self) -> None:
        """$ref nested inside allOf (common OpenAPI pattern)."""
        schema = {"allOf": [{"$ref": "#/components/schemas/User"}]}
        assert _extract_type_name(schema) == "User"

    def test_allof_combined_anyof(self) -> None:
        """allOf with anyOf ref — first found wins."""
        schema = {"allOf": [{"$ref": "#/components/schemas/Repository"}, {"type": "object"}]}
        assert _extract_type_name(schema) == "Repository"


class TestCollapseValue:
    """Tests for _collapse_value — collapses runtime values to compact $ref strings."""

    def test_dict_with_ref(self) -> None:
        """Dict with matching $ref schema collapses to $ref:TypeName."""
        schema = {"$ref": "#/components/schemas/User"}
        assert _collapse_value({"id": 1, "login": "me"}, schema) == "$ref:User"

    def test_dict_with_allof_ref(self) -> None:
        """Dict with allOf $ref (common OpenAPI pattern) collapses correctly."""
        schema = {"allOf": [{"$ref": "#/components/schemas/Repository"}]}
        assert _collapse_value({"name": "repo"}, schema) == "$ref:Repository"

    def test_dict_with_anyof_ref(self) -> None:
        """Dict with anyOf $ref schema collapses to $ref:TypeName."""
        schema = {"anyOf": [{"$ref": "#/components/schemas/Repository"}, {"type": "null"}]}
        assert _collapse_value({"name": "repo"}, schema) == "$ref:Repository"

    def test_dict_without_schema_uses_placeholder(self) -> None:
        """Dict with no schema falls back to placeholder."""
        assert _collapse_value({"id": 1}, None) == "{...}"

    def test_dict_without_ref_uses_placeholder(self) -> None:
        """Dict with schema but no $ref falls back to placeholder."""
        schema = {"type": "object", "properties": {}}
        assert _collapse_value({"id": 1}, schema) == "{...}"

    def test_list_with_ref(self) -> None:
        """List with items.$ref collapses to $ref:TypeName[count]."""
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Label"}}
        assert _collapse_value([{"id": 1}, {"id": 2}], schema) == "$ref:Label[2]"

    def test_list_without_ref_shows_count(self) -> None:
        """List without $ref items falls back to count."""
        schema = {"type": "array", "items": {"type": "object"}}
        assert _collapse_value([{"x": 1}], schema) == "[1 items]"

    def test_list_with_none_schema(self) -> None:
        """List with no schema falls back to count."""
        assert _collapse_value([1, 2, 3], None) == "[3 items]"

    def test_list_with_ref_in_anyof_items(self) -> None:
        """Array items with anyOf $ref collapses correctly."""
        schema = {
            "type": "array",
            "items": {"anyOf": [{"$ref": "#/components/schemas/Issue"}, {"type": "null"}]},
        }
        assert _collapse_value([{"title": "bug"}], schema) == "$ref:Issue[1]"

    def test_scalar_passthrough(self) -> None:
        """Non-dict, non-list values are stringified."""
        assert _collapse_value("hello", None) == "hello"
        assert _collapse_value(42, None) == "42"


class TestCollapseData:
    """Tests for _collapse_data — walk data+schema, collapse $ref objects at depth>=1.

    This function shapes data for any formatter (json or markdown).
    """

    def test_full_detail_returns_unchanged(self) -> None:
        """detail='full' returns data unchanged regardless of schema."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"owner": {"$ref": "#/components/schemas/User"}}}
        result = _collapse_data(data, schema, _depth=0, detail="full")
        assert result is data

    def test_depth_0_no_collapse(self) -> None:
        """At depth=0, the top-level dict is not collapsed, but
        $ref-backed properties at depth>=1 ARE collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"owner": {"$ref": "#/components/schemas/User"}}}
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        # Top-level dict stays as dict (not collapsed to $ref:TypeName)
        assert isinstance(result, dict)
        assert "owner" in result
        # BUT the nested $ref property at depth 1 IS collapsed
        assert result["owner"] == "$ref:User"

    def test_depth_1_dict_with_ref_collapses(self) -> None:
        """At depth>=1, a dict with $ref schema collapses to $ref:TypeName."""
        data = {"user": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"user": {"$ref": "#/components/schemas/User"}}}
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert result["user"] == "$ref:User"

    def test_depth_1_list_with_ref_collapses(self) -> None:
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

    def test_inline_schema_not_collapsed(self) -> None:
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

    def test_allof_ref_collapses(self) -> None:
        """allOf with $ref is resolved and collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {"owner": {"allOf": [{"$ref": "#/components/schemas/User"}]}},
        }
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        assert result["owner"] == "$ref:User"

    def test_anyof_ref_collapses(self) -> None:
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

    def test_none_no_collapse(self) -> None:
        """schema=None means no collapse occurs (data passed through)."""
        data = {"owner": {"id": 1, "login": "user1"}}
        result = _collapse_data(data, None, _depth=0, detail="concise")
        assert result is data

    def test_nested_mixed(self) -> None:
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

    def test_list_at_depth_0_no_collapse(self) -> None:
        """Top-level list is not collapsed to $ref:TypeName[N], but
        items inside it at depth>=1 ARE collapsed."""
        data = [{"id": 1, "login": "user1"}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/User"}}
        result = _collapse_data(data, schema, _depth=0, detail="concise")
        # List stays as list (not collapsed to label)
        assert isinstance(result, list)
        assert len(result) == 1
        # But nested items at depth>=1 ARE collapsed
        assert result[0] == "$ref:User"


class TestResolveAnyOfSchema:
    def test_none_returns_none(self) -> None:
        assert _resolve_anyof_schema(None) is None

    def test_anyof_with_object_returns_first_object(self) -> None:
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

    def test_anyof_only_scalars_returns_original(self) -> None:
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_oneof_with_object_returns_first_object(self) -> None:
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "object", "properties": {"name": {"type": "string"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == "object"

    def test_anyof_object_no_properties_skipped(self) -> None:
        schema = {
            "anyOf": [
                {"type": "object"},
                {"type": "object", "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert "id" in result["properties"]

    def test_no_anyof_or_oneof(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_empty_list(self) -> None:
        schema: dict[str, Any] = {"anyOf": []}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_only_non_dict_items(self) -> None:
        schema = {"anyOf": ["string", 42]}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_object_with_null_properties(self) -> None:
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

    def test_anyof_object_with_type_list(self) -> None:
        """Type-as-list form (``type: [\"object\", \"null\"]``) is handled correctly."""
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": ["object", "null"], "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == ["object", "null"]
        assert "id" in result["properties"]


class TestFormatAsMarkdown:
    def test_none_input(self) -> None:
        result = _format_as_markdown(None)
        assert result == "N/A"

    def test_none_input_with_title(self) -> None:
        result = _format_as_markdown(None, title="Test")
        assert "Test" in result
        assert "N/A" in result

    def test_scalar_value(self) -> None:
        result = _format_as_markdown("hello")
        assert result == "hello"

    def test_integer_scalar(self) -> None:
        result = _format_as_markdown(42)
        assert result == "42"

    def test_empty_list(self) -> None:
        result = _format_as_markdown([])
        assert "_(empty)_" in result

    def test_list_of_scalars_with_schema(self) -> None:
        schema = {"type": "array", "items": {"type": "string"}}
        result = _format_as_markdown(["a", "b", "c"], schema)
        assert "a, b, c" in result

    def test_list_of_scalars_no_schema(self) -> None:
        result = _format_as_markdown(["a", "b"])
        assert "- a" in result
        assert "- b" in result

    def test_list_of_dicts(self) -> None:
        data = [{"name": "Alice"}, {"name": "Bob"}]
        result = _format_as_markdown(data)
        assert "Alice" in result
        assert "Bob" in result
        assert "| Name |" in result

    def test_list_of_dicts_with_schema(self) -> None:
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

    def test_empty_dict(self) -> None:
        result = _format_as_markdown({})
        assert "*Empty*" in result

    def test_dict_with_properties_renders_table(self) -> None:
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

    def test_dict_with_nested_dict_section(self) -> None:
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

    def test_dict_with_nested_list(self) -> None:
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

    def test_title_at_top_level(self) -> None:
        result = _format_as_markdown("hello", title="MyTitle")
        assert "MyTitle" in result
        assert "hello" in result

    def test_title_at_top_level_for_scalar(self) -> None:
        result = _format_as_markdown(42, title="The Answer")
        assert "The Answer" in result
        assert "42" in result

    def test_title_with_dict(self) -> None:
        result = _format_as_markdown({"key": "val"}, title="MyTitle")
        assert "# MyTitle" in result
        assert "| Key | val |" in result
        assert "val" in result

    def test_title_with_list(self) -> None:
        result = _format_as_markdown(["a", "b"], title="MyTitle")
        assert "# MyTitle" in result
        assert "a" in result
        assert "b" in result

    def test_allof_merged_schema(self) -> None:
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

    def test_allof_without_properties(self) -> None:
        data = {"a": 1}
        schema = {"type": "object", "allOf": [{"type": "object"}]}
        result = _format_as_markdown(data, schema)
        assert "a" in result or "A" in result

    def test_properties_without_schema_flat(self) -> None:
        data = {"key": "val"}
        result = _format_as_markdown(data)
        assert "|" in result

    def test_datetime_property_formatted(self) -> None:
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

    def test_anyof_resolved_in_properties(self) -> None:
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

    def test_nested_section_with_depth(self) -> None:
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

    def test_dict_concise_collapses_nested_at_depth(self) -> None:
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

    def test_dict_concise_collapses_list_at_depth(self) -> None:
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

    def test_dict_concise_top_level_stays_expanded(self) -> None:
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

    def test_property_schema_not_a_dict_skipped(self) -> None:
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

    def test_non_dict_non_list_input(self) -> None:
        assert _format_as_markdown(True) == "True"

    # ── field_filter and item_title_key hooks ──────────────────────────────────────

    def test_field_filter_on_dict_selects_subset(self) -> None:
        """field_filter shows only the specified properties."""
        data = {"id": 1, "name": "Alice", "email": "alice@test.com", "role": "admin"}
        result = _format_as_markdown(data, field_filter={"id": {}, "name": {}})
        assert "| Id | 1 |" in result
        assert "| Name | Alice |" in result
        assert "Email" not in result
        assert "Role" not in result

    def test_field_filter_on_list_of_dicts(self) -> None:
        """field_filter applies to each item in a list of dicts."""
        data = [
            {"id": 1, "name": "Foo", "extra": "x"},
            {"id": 2, "name": "Bar", "extra": "y"},
        ]
        result = _format_as_markdown(data, field_filter={"id": {}, "name": {}})
        for row in ("Foo", "Bar", "1", "2"):
            assert row in result
        assert "extra" not in result.lower()
        assert "Extra" not in result

    def test_field_filter_skips_missing_keys_gracefully(self) -> None:
        """field_filter entries not in data are silently skipped."""
        data = {"name": "Alice"}
        result = _format_as_markdown(data, field_filter={"name": {}, "nonexistent": {}})
        assert "| Name | Alice |" in result
        assert "nonexistent" not in result.lower()

    def test_item_title_key_customizes_list_headings(self) -> None:
        """item_title_key uses the specified field value as the item heading."""
        data = [{"number": 42, "title": "Bug fix"}, {"number": 43, "title": "Feature"}]
        result = _format_as_markdown(data, item_title_key="title")
        assert "# Bug fix" in result
        assert "# Feature" in result
        assert "| Number | 42 |" in result
        assert "| Number | 43 |" in result

    def test_item_title_key_falls_back_to_item_n_when_missing(self) -> None:
        """When item_title_key field is missing, falls back to 'Item N'."""
        data = [{"id": 1, "name": "Alice"}]
        result = _format_as_markdown(data, item_title_key="nonexistent")
        assert "# Item 1" in result
        assert "| Id | 1 |" in result

    def test_field_filter_and_item_title_key_together(self) -> None:
        """Both hooks can be used together."""
        data = [{"number": 1, "title": "Bug", "body": "Details"}]
        result = _format_as_markdown(
            data,
            field_filter={"number": {}, "title": {}},
            item_title_key="title",
        )
        assert "# Bug" in result
        assert "| Number | 1 |" in result
        assert "| Title | Bug |" in result
        assert "Body" not in result
        assert "body" not in result

    # ── Consistency: tool and resource should produce same structure ──────────────

    def test_format_produces_nested_sub_tables_for_nested_objects(self) -> None:
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

    # ── Field-level render hints: compact_ref, badge ──────────────────────────────

    def test_compact_ref_renders_dict_as_flat_row(self) -> None:
        """compact_ref renders a nested dict as a flat table row using template."""
        data = {"base": {"owner": "org", "repo": "myrepo", "branch": "main"}}
        field_filter = {
            "base": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        # base appears as a flat table row, not a nested sub-section
        assert "| Base | org/myrepo:main |" in result
        assert "## Base" not in result

    def test_compact_ref_at_full_detail(self) -> None:
        """compact_ref works at detail=full (not just concise)."""
        data = {
            "name": "PR-42",
            "head": {"owner": "fork", "repo": "fork-repo", "branch": "feature-x"},
        }
        field_filter = {
            "name": {},
            "head": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter, detail="full")
        assert "| Head | fork/fork-repo:feature-x |" in result
        assert "## Head" not in result

    def test_compact_ref_at_concise_detail(self) -> None:
        """compact_ref works at detail=concise (same flat rendering)."""
        data = {
            "name": "PR-42",
            "head": {"owner": "fork", "repo": "fork-repo", "branch": "feature-x"},
        }
        field_filter = {
            "name": {},
            "head": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter, detail="concise")
        assert "| Head | fork/fork-repo:feature-x |" in result

    def test_compact_ref_fallback_on_missing_template_key(self) -> None:
        """When template.format() fails, compact_ref falls back to str()."""
        data = {"base": {"label": "main"}}
        field_filter = {
            "base": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        # Should not crash; falls back to str representation
        assert "| Base | {'label': 'main'}" in result or "| Base |" in result
        assert "## Base" not in result

    def test_badge_yes_for_truthy(self) -> None:
        """badge renders truthy values as 'Yes'."""
        data = {"active": True, "name": "test"}
        field_filter = {"active": {"render": "badge"}, "name": {}}
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "| Active | Yes |" in result

    def test_badge_no_for_falsy(self) -> None:
        """badge renders falsy values as 'No'."""
        data = {"active": False, "pull_request": None}
        field_filter = {"active": {"render": "badge"}, "pull_request": {"render": "badge"}}
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "| Active | No |" in result
        assert "| Pull Request | No |" in result

    def test_badge_with_dict_value(self) -> None:
        """badge with a dict (present) renders as 'Yes'."""
        data = {"pull_request": {"url": "https://example.com/pr/1"}}
        field_filter = {"pull_request": {"render": "badge"}}
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "| Pull Request | Yes |" in result
        # Should NOT expand the nested dict
        assert "url" not in result.lower()
        assert "Url" not in result

    def test_expand_default_renders_nested_as_section(self) -> None:
        """Default render='expand' preserves existing nested-section behavior."""
        data: dict[str, Any] = {"user": {"id": 1, "login": "dev"}}
        field_filter: dict[str, Any] = {"user": {}}
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "## User" in result or "**User:**" in result
        assert "dev" in result

    # ── List compact_ref ──────────────────────────────────────────────────────────

    def test_compact_ref_on_list_renders_comma_separated(self) -> None:
        """compact_ref on a list renders comma-separated template values."""
        data = {"labels": [{"name": "bug"}, {"name": "enhancement"}]}
        field_filter = {
            "labels": {"render": "compact_ref", "template": "{name}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "| Labels | bug, enhancement |" in result
        assert "## Labels" not in result

    def test_compact_ref_on_list_single_item(self) -> None:
        """compact_ref on a single-element list works correctly."""
        data = {"labels": [{"name": "bug"}]}
        field_filter = {
            "labels": {"render": "compact_ref", "template": "{name}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "| Labels | bug |" in result

    def test_compact_ref_on_list_empty(self) -> None:
        """compact_ref on an empty list renders empty string."""
        data: dict[str, Any] = {"labels": []}
        field_filter: dict[str, Any] = {
            "labels": {"render": "compact_ref", "template": "{name}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "| Labels |  |" in result

    def test_compact_ref_on_list_fallback_on_missing_key(self) -> None:
        """When template is missing a key, compact_ref list falls back to str()."""
        data = {"items": [{"id": 1}, {"id": 2}]}
        field_filter = {
            "items": {"render": "compact_ref", "template": "{name}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        # Should not crash; falls back to str representation
        assert "| Items |" in result

    def test_compact_ref_on_list_non_dict_items(self) -> None:
        """compact_ref on a list of scalars renders each as str."""
        data = {"tags": ["alpha", "beta"]}
        field_filter = {
            "tags": {"render": "compact_ref", "template": "{name}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        assert "| Tags | alpha, beta |" in result

    def test_dollar_ref_flattened_only_on_expand_path(self) -> None:
        """$ref flattening happens after render hints so compact_ref still works."""
        data = {
            "base": {"$ref": "FakeRef"},
        }
        # With an explicit compact_ref, the $ref dict should be handled
        # by compact_ref (template likely fails → str fallback), not
        # flattened to "$ref:FakeRef".
        field_filter = {
            "base": {"render": "compact_ref", "template": "{ref}"},
        }
        result = _format_as_markdown(data, field_filter=field_filter)
        # Should render as the str fallback (dict doesn't have 'ref' key)
        assert "| Base | {'$ref': 'FakeRef'}" in result or "| Base |" in result
        # Should NOT show the $ref flattened syntax
        assert "$ref:FakeRef" not in result

    def test_dollar_ref_flattened_on_expand_path(self) -> None:
        """$ref flattening still works on the default expand path."""
        data = {
            "user": {"$ref": "User"},
        }
        result = _format_as_markdown(data)
        # Without explicit render hints, the $ref dict is flattened
        assert "$ref:User" in result


class TestFormatType:
    """Tests for _format_type — type enrichment with enum/array info."""

    def test_plain_type_unchanged(self) -> None:
        """No enum, no array items — returns basic type."""
        assert _format_type({"type": "string"}) == "string"
        assert _format_type({"type": "integer"}) == "integer"
        assert _format_type({"type": "boolean"}) == "boolean"

    def test_fallback_when_no_type(self) -> None:
        """No type key — returns 'any'."""
        assert _format_type({}) == "any"

    def test_enum_appends_values(self) -> None:
        """Enum values appear as type [val1, val2, ...]."""
        prop = {"type": "string", "enum": ["merge", "rebase", "squash"]}
        assert _format_type(prop) == "string [merge, rebase, squash]"

    def test_enum_with_integer_values(self) -> None:
        """Non-string enum values are stringified."""
        prop = {"type": "integer", "enum": [1, 2, 3]}
        assert _format_type(prop) == "integer [1, 2, 3]"

    def test_array_with_items_properties(self) -> None:
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

    def test_array_without_items(self) -> None:
        """Array without items schema — unchanged."""
        assert _format_type({"type": "array"}) == "array"

    def test_array_with_items_no_properties(self) -> None:
        """Array items with no properties — unchanged."""
        prop = {"type": "array", "items": {"type": "string"}}
        assert _format_type(prop) == "array"

    def test_enum_takes_priority_over_array(self) -> None:
        """When both enum and array are present, enum wins."""
        prop = {
            "type": "array",
            "enum": ["create", "update", "delete"],
            "items": {"type": "string"},
        }
        assert _format_type(prop) == "array [create, update, delete]"

    def test_type_list_extracts_non_null_type(self) -> None:
        """``type: ["array", "null"]`` should display as ``"array"``."""
        prop = {"type": ["array", "null"]}
        assert _format_type(prop) == "array"

    def test_type_list_with_items_properties(self) -> None:
        """``type: ["array", "null"]`` with items.properties shows array of {...}."""
        prop = {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "path": {"type": "string"},
                },
            },
        }
        assert _format_type(prop) == "array of {operation, path}"

    def test_type_list_with_enum(self) -> None:
        """``type: ["string", "null"]`` with enum appends enum values."""
        prop = {"type": ["string", "null"], "enum": ["open", "closed"]}
        assert _format_type(prop) == "string [open, closed]"

    def test_type_list_all_null(self) -> None:
        """``type: ["null"]`` returns ``"null"`` — the only type present."""
        assert _format_type({"type": ["null"]}) == "null"


class TestFormatParameterTable:
    """Tests for _format_parameter_table — the markdown parameter table."""

    def test_plain_params(self) -> None:
        """Basic string/integer params render without enrichment."""
        props = {
            "owner": {"type": "string", "description": "owner of the repo"},
            "index": {"type": "integer", "description": "issue index"},
        }
        result = _format_parameter_table(props, ["owner", "index"])
        assert "| owner | string | yes | owner of the repo |" in result
        assert "| index | integer | yes | issue index |" in result
        assert "## Parameters" in result

    def test_enum_param(self) -> None:
        """Enum param shows values in type column."""
        props = {
            "Do": {
                "type": "string",
                "enum": ["merge", "rebase", "squash"],
            },
        }
        result = _format_parameter_table(props, ["Do"])
        assert "| Do | string [merge, rebase, squash] | yes |  |" in result

    def test_array_param(self) -> None:
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

    def test_optional_param(self) -> None:
        """Non-required param gets 'no' in Required column."""
        props = {
            "message": {"type": "string", "description": "commit message"},
        }
        result = _format_parameter_table(props, [])
        assert "| message | string | no | commit message |" in result

    def test_description_escapes_pipe(self) -> None:
        """Pipe characters in description are escaped."""
        props = {
            "owner": {"type": "string", "description": "owner|repo"},
        }
        result = _format_parameter_table(props, ["owner"])
        assert r"| owner | string | yes | owner\|repo |" in result

    def test_invalid_prop_skipped(self) -> None:
        """Non-dict properties are skipped without error."""
        props = {"bad": "not a dict"}
        result = _format_parameter_table(props, [])
        assert "bad" not in result
        assert "## Parameters" in result

    def test_empty_properties(self) -> None:
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

    def test_returns_paginated_toolresult(self) -> None:
        """Returns a ToolResult with structured_content containing result."""
        result = _format_paginated_result(
            [{"id": 1}, {"id": 2}], 2, "raw", page=1, limit=10,
        )
        assert isinstance(result, ToolResult)
        assert result.structured_content is not None
        assert "result" in result.structured_content

    def test_respects_page_and_limit(self) -> None:
        """When fetch_all=False, only returns the requested page."""
        items = [{"id": i} for i in range(25)]
        result = _format_paginated_result(items, 25, "raw", page=2, limit=10)
        sc = get_structured(result)
        assert len(sc["result"]) == 10
        assert sc["result"][0]["id"] == 10
        assert sc["result"][-1]["id"] == 19
        assert sc["has_more"] is True
        assert sc["next_offset"] == 3
        assert sc["total_count"] == 25

    def test_last_page(self) -> None:
        """Last page returns fewer items and has_more=False."""
        items = [{"id": i} for i in range(25)]
        result = _format_paginated_result(items, 25, "raw", page=3, limit=10)
        sc = get_structured(result)
        assert len(sc["result"]) == 5
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25

    def test_fetch_all_returns_all_items(self) -> None:
        """When fetch_all=True, all items are returned without slicing."""
        items = [{"id": i} for i in range(50)]
        result = _format_paginated_result(
            items, 50, "raw", page=1, limit=10, fetch_all=True,
        )
        sc = get_structured(result)
        assert len(sc["result"]) == 50
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 50

    def test_fetch_all_empty_list(self) -> None:
        """When fetch_all=True and items is empty, returns empty."""
        result = _format_paginated_result(
            [], 0, "raw", page=1, limit=10, fetch_all=True,
        )
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["has_more"] is False
        assert sc["total_count"] == 0

    def test_markdown_format(self) -> None:
        """Markdown format produces text content."""
        items = [{"id": 1, "name": "test"}]
        result = _format_paginated_result(
            items, 1, "markdown", page=1, limit=10,
        )
        assert result.content is not None
        assert len(result.content) > 0
        text = extract_text_content(result.content)
        assert "test" in text
        # Verify pagination metadata is in structured_content
        sc = get_structured(result)
        assert sc["total_count"] == 1

    def test_json_format(self) -> None:
        """JSON format produces JSON text content."""
        items = [{"id": 1, "name": "test"}]
        result = _format_paginated_result(
            items, 1, "json", page=1, limit=10,
        )
        assert result.content is not None
        text = extract_text_content(result.content)
        parsed = json.loads(text)
        assert parsed[0]["name"] == "test"
        sc = get_structured(result)
        assert sc["total_count"] == 1

    def test_pagination_keys_in_structured_content(self) -> None:
        """PAGINATION_KEYS keys appear in structured_content."""
        items = [{"id": i} for i in range(25)]
        result = _format_paginated_result(items, 25, "raw", page=1, limit=10)
        for key in PAGINATION_KEYS:
            assert result.structured_content is not None
            assert key in result.structured_content

    def test_empty_items_list(self) -> None:
        """Empty items list returns empty result."""
        result = _format_paginated_result(
            [], 0, "raw", page=1, limit=10,
        )
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["total_count"] == 0

    def test_markdown_extras_appended(self) -> None:
        """markdown_extras appear as additional sections in markdown output."""
        items = [{"id": 1}]
        result = _format_paginated_result(
            items, 1, "markdown", page=1, limit=10,
            markdown_extras=["**Extra section:** content"],
        )
        text = extract_text_content(result.content)
        assert "**Extra section:** content" in text


# ============================================================================
# apply_format - detail=concise with JSON output
# ============================================================================


class TestApplyFormatConcise:
    """Tests for apply_format with detail=concise in JSON mode.

    apply_format receives the schema OF the data directly (not wrapped in a
    ``{"properties": {"result": ...}}`` container — that wrapper was specific
    to the removed ``format_result`` which worked on ``ToolResult.structured_content``).
    """

    def test_json_full_no_collapse(self) -> None:
        """detail='full' (default) with JSON returns complete data unchanged."""
        data = {"owner": {"id": 1, "login": "user1"}, "name": "repo"}
        schema = {
            "type": "object",
            "properties": {
                "owner": {"$ref": "#/components/schemas/User"},
                "name": {"type": "string"},
            },
        }
        result = apply_format(data, "json", detail="full", schema=schema)
        assert result.content is not None
        parsed = parse_json_content(result)
        assert isinstance(parsed["owner"], dict)
        assert parsed["owner"]["login"] == "user1"

    def test_json_concise_collapses_ref_dict(self) -> None:
        """detail='concise' + json collapses $ref dicts to labels."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {"owner": {"$ref": "#/components/schemas/User"}},
        }
        result = apply_format(data, "json", detail="concise", schema=schema)
        parsed = parse_json_content(result)
        assert parsed["owner"] == "$ref:User"

    def test_json_concise_collapses_ref_list(self) -> None:
        """detail='concise' + json collapses $ref lists to labels."""
        data = {"labels": [{"id": 1, "name": "bug"}, {"id": 2, "name": "feature"}]}
        schema = {
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"$ref": "#/components/schemas/Label"}},
            },
        }
        result = apply_format(data, "json", detail="concise", schema=schema)
        parsed = parse_json_content(result)
        assert parsed["labels"] == "$ref:Label[2]"

    def test_json_concise_inline_not_collapsed(self) -> None:
        """Inline schemas (no $ref) remain expanded even with detail='concise'."""
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
        result = apply_format(data, "json", detail="concise", schema=schema)
        parsed = parse_json_content(result)
        assert isinstance(parsed["config"], dict)
        assert parsed["config"]["host"] == "localhost"

    def test_json_concise_top_level_object_stays(self) -> None:
        """Top-level object is not collapsed."""
        data = {"name": "repo", "description": "a test repo"}
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
        }
        result = apply_format(data, "json", detail="concise", schema=schema)
        parsed = parse_json_content(result)
        assert parsed["name"] == "repo"
        assert parsed["description"] == "a test repo"

    def test_markdown_concise_collapses_nested_ref(self) -> None:
        """detail='concise' collapses $ref objects at depth>=1 in markdown."""
        data = {
            "meta": {
                "owner": {"id": 1, "login": "user1"},
                "name": "repo",
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {
                        "owner": {"$ref": "#/components/schemas/User"},
                        "name": {"type": "string"},
                    },
                },
            },
        }
        result = apply_format(data, "markdown", detail="concise", schema=schema)
        assert result.content is not None
        text = extract_text_content(result.content)
        assert "$ref:User" in text
        # Top-level scalars and inline props remain expanded
        assert "repo" in text

    def test_no_schema_fallback(self) -> None:
        """When schema is None, concise is a no-op (data unchanged)."""
        data = {"owner": {"id": 1, "login": "user1"}}
        result = apply_format(data, "json", detail="concise", schema=None)
        parsed = parse_json_content(result)
        assert isinstance(parsed["owner"], dict)
        assert parsed["owner"]["login"] == "user1"

    def test_raw_passthrough(self) -> None:
        """format='raw' ignores detail — data is not collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        result = apply_format(data, "raw", detail="concise", schema=None)
        # Raw returns structured_content only
        sc = get_structured(result)
        assert sc is not None
        assert isinstance(sc["result"], dict)
        assert sc["result"]["owner"]["login"] == "user1"


class TestFormatDateTime:
    """Tests for _format_datetime."""

    def test_formats_iso_datetime(self) -> None:
        """Test ISO datetime string is formatted correctly."""
        dt = "2024-01-15T10:30:00Z"
        result = _format_datetime(dt)
        assert result == "2024-01-15 10:30:00 UTC"

    def test_handles_none(self) -> None:
        """Test None returns N/A."""
        assert _format_datetime(None) == "N/A"

    def test_handles_empty_string(self) -> None:
        """Test empty string returns N/A."""
        assert _format_datetime("") == "N/A"

    def test_handles_invalid_format(self) -> None:
        """Test invalid format returns original string."""
        assert _format_datetime("not a date") == "not a date"


class TestFormatListAsMarkdownRef:
    """Tests for _format_list_as_markdown with $ref-flattened data."""

    def test_ref_list_renders_bulleted_refs(self) -> None:
        """List of {"$ref": "Type"} dicts renders as bulleted $ref:Type items."""
        from gitea_mcp_server.format import _format_list_as_markdown

        data = [{"$ref": "User"}, {"$ref": "Repo"}]
        result = _format_list_as_markdown(data)
        assert "$ref:User" in result
        assert "$ref:Repo" in result
        assert "- $ref:User" in result
        assert "- $ref:Repo" in result


class TestFormatDictAsMarkdownEmptyFieldFilter:
    """Tests for _format_dict_as_markdown with empty field_filter."""

    def test_empty_field_filter_falls_back_to_flat_table(self) -> None:
        """When field_filter is empty but data exists, renders flat table."""
        from gitea_mcp_server.format import _format_dict_as_markdown

        data = {"a": 1, "b": 2}
        result = _format_dict_as_markdown(data, field_filter={})
        assert "| Property | Value |" in result
        assert "| a | 1 |" in result
        assert "| b | 2 |" in result


class TestFormatToolInfoMarkdown:
    """Tests for _format_tool_info_markdown."""

    def test_output_schema_section_included(self) -> None:
        """When output_schema is present, 'Output Schema' section is rendered."""
        from gitea_mcp_server.format import _format_tool_info_markdown

        schema: ToolSchemaResult = {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {"properties": {"x": {"type": "string"}}, "required": []},
            "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
        }
        result = _format_tool_info_markdown(schema)
        assert "## Output Schema" in result
        assert "type" in result
        assert "object" in result
