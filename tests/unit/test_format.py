"""Tests for gitea_mcp_server/format.py.

Covers all functions in __all__:
- _snake_to_title, _format_datetime, _format_scalar, _format_simple_value
- _resolve_anyof_schema, _format_as_markdown
"""

from gitea_mcp_server.format import (
    _format_as_markdown,
    _format_datetime,
    _format_scalar,
    _format_simple_value,
    _resolve_anyof_schema,
    _snake_to_title,
)


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
        assert "| name |" in result

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

    def test_non_dict_non_list_input(self):
        assert _format_as_markdown(True) == "True"
