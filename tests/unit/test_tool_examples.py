"""Unit tests for schema-to-example generation."""

import pytest
from fastmcp.tools.base import Tool

from gitea_mcp_server.tools.examples import (
    _example_array,
    _example_object,
    _example_string,
    _schema_to_example,
    _serialize_tool_schema,
)

class TestSchemaToExample:
    """Tests for _schema_to_example function."""

    def test_object_with_properties(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "active": {"type": "boolean"},
                "score": {"type": "number"},
            },
        }
        result = _schema_to_example(schema)
        assert isinstance(result, dict)
        assert result["id"] == 0
        assert result["name"] == "example-name"
        assert result["active"] is True
        assert result["score"] == 0.0

    def test_uses_schema_example(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {
            "type": "object",
            "properties": {
                "color": {"type": "string", "example": "00aabb"},
            },
        }
        result = _schema_to_example(schema)
        assert result["color"] == "00aabb"

    def test_array_type(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {
            "type": "array",
            "items": {"type": "string"},
        }
        result = _schema_to_example(schema)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == "example"

    def test_string_with_enum(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {"type": "string", "enum": ["open", "closed"]}
        assert _schema_to_example(schema) == "open"

    def test_string_with_format_date_time(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {"type": "string", "format": "date-time"}
        result = _schema_to_example(schema)
        assert "2024-01-01" in result
        assert "T" in result

    def test_anyof_skips_null(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {
            "anyOf": [
                {"type": "null"},
                {"type": "string"},
            ],
        }
        assert _schema_to_example(schema) == "example"

    def test_type_list_skips_null(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {"type": ["null", "string"]}
        assert _schema_to_example(schema) == "example"

    def test_depth_limit(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

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
        result = _schema_to_example(schema, max_depth=2)
        # At max_depth, nested objects return {}
        assert result["a"]["b"] == {}

    def test_property_count_limit(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {
            "type": "object",
            "properties": {str(i): {"type": "string"} for i in range(20)},
        }
        result = _schema_to_example(schema, max_properties=5)
        assert len(result) == 5

    def test_null_type(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        assert _schema_to_example({"type": "null"}) is None

    def test_non_dict_schema_raises(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        with pytest.raises(AttributeError):
            _schema_to_example("not a dict")  # type: ignore[arg-type]

    def test_nested_object_in_array(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string"},
                },
            },
        }
        result = _schema_to_example(schema)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 0
        assert result[0]["label"] == "bug"

    def test_empty_object(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        assert _schema_to_example({"type": "object", "properties": {}}) == {}

    def test_serialize_tool_schema_uses_output_example(self):
        """_serialize_tool_schema should produce output_example instead of output_schema."""
        from fastmcp.tools.base import Tool

        from gitea_mcp_server.tools.examples import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {"x": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        )
        result = _serialize_tool_schema(tool)
        assert "output_example" in result
        assert "output_schema" not in result
        assert result["output_example"]["id"] == 0
        assert result["output_example"]["name"] == "example-name"

    def test_serialize_tool_schema_no_output_schema(self):
        """_serialize_tool_schema should not include output_example when output_schema is None."""
        from fastmcp.tools.base import Tool

        from gitea_mcp_server.tools.examples import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {}},
            output_schema=None,
        )
        result = _serialize_tool_schema(tool)
        assert "output_example" not in result
        assert "output_schema" not in result

    def test_example_string_email_format(self):
        """_example_string with format=email should return user@example.com."""
        assert _example_string({"format": "email"}) == "user@example.com"

    def test_example_string_uri_format(self):
        """_example_string with format=uri should return https://example.com."""
        assert _example_string({"format": "uri"}) == "https://example.com"

    def test_example_string_plain(self):
        """_example_string without format/enum/prop_name should return 'example'."""
        assert _example_string({}) == "example"

    def test_schema_to_example_oneOf_skips_null(self):
        """oneOf should work like anyOf, skipping null types."""
        schema = {
            "oneOf": [
                {"type": "null"},
                {"type": "integer"},
            ],
        }
        assert _schema_to_example(schema) == 0

    def test_schema_to_example_oneOf_first_non_null(self):
        """oneOf should return example for the first non-null option."""
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ],
        }
        assert _schema_to_example(schema) == "example"

    def test_type_list_all_null(self):
        """When type is a list and all entries are 'null', schema_type should become 'null'."""
        schema = {"type": ["null", "null"]}
        assert _schema_to_example(schema) is None

    def test_unrecognized_type_returns_none(self):
        """When schema_type is not recognized, return None."""
        assert _schema_to_example({"type": "file"}) is None

    def test_empty_array_items(self):
        """_example_array with empty items dict should return empty list."""
        assert _example_array({"items": {}}, 0, 3, 15) == []

    def test_array_missing_items(self):
        """_example_array without items key should return empty list."""
        assert _example_array({}, 0, 3, 15) == []

    def test_object_no_properties(self):
        """_example_object without properties should return empty dict."""
        assert _example_object({}, 0, 3, 15) == {}

    def test_serialize_tool_schema_with_tags(self):
        """_serialize_tool_schema should include tags when present."""
        from fastmcp.tools.base import Tool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.tools.examples import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {}},
            tags={"issue", "repository"},
        )
        result = _serialize_tool_schema(tool)
        assert "tags" in result
        assert set(result["tags"]) == {"issue", "repository"}

    def test_serialize_tool_schema_with_version(self):
        """_serialize_tool_schema should include version when present."""
        from fastmcp.tools.base import Tool

        from gitea_mcp_server.tools.examples import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {}},
            version="2.0",
        )
        result = _serialize_tool_schema(tool)
        assert result["version"] == "2.0"

    def test_serialize_tool_schema_with_open_world_hint(self):
        """_serialize_tool_schema should include openWorldHint when True."""
        from fastmcp.tools.base import Tool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.tools.examples import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {}},
            annotations=ToolAnnotations(openWorldHint=True),
        )
        result = _serialize_tool_schema(tool)
        assert result["annotations"]["openWorldHint"] is True


class TestSchemaToCompactExample:
    """Tests for _schema_to_compact_example."""

    def test_ref_emits_dict_with_type_name(self):
        """$ref should emit {"$ref": "TypeName"} instead of inlining."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {"$ref": "#/components/schemas/User"}
        result = _schema_to_compact_example(schema)
        assert result == {"$ref": "User"}

    def test_ref_uses_last_path_segment(self):
        """$ref should extract the tail of the path as the type name."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {"$ref": "#/definitions/api/SomeDeeplyNestedType"}
        result = _schema_to_compact_example(schema)
        assert result == {"$ref": "SomeDeeplyNestedType"}

    def test_max_depth_returns_placeholder(self):
        """At max_depth, should return '{...}'."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

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
        result = _schema_to_compact_example(schema, max_depth=2)
        # a -> depth 1, b -> depth 2 (hits max_depth), returns {...}
        assert result["a"]["b"] == "{...}"

    def test_anyof_skips_null_first_option(self):
        """anyOf should pick the first non-null option."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {
            "anyOf": [
                {"type": "null"},
                {"type": "string"},
            ],
        }
        assert _schema_to_compact_example(schema) == "example"

    def test_oneof_skips_null(self):
        """oneOf should work like anyOf, skipping null types."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {
            "oneOf": [
                {"type": "null"},
                {"type": "integer"},
            ],
        }
        assert _schema_to_compact_example(schema) == 0

    def test_type_list_skips_null(self):
        """type as a list should skip 'null' entries."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {"type": ["null", "string"]}
        assert _schema_to_compact_example(schema) == "example"

    def test_type_list_all_null(self):
        """When type list is all null, should return None."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        assert _schema_to_compact_example({"type": ["null", "null"]}) is None

    def test_uses_schema_example(self):
        """schema 'example' field should be used."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {"type": "string", "example": "custom-value"}
        assert _schema_to_compact_example(schema) == "custom-value"

    def test_object_with_no_properties(self):
        """Empty object should return '{...}'."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        assert _schema_to_compact_example({"type": "object", "properties": {}}) == "{...}"

    def test_array_with_ref_items(self):
        """Array of $ref items should return [{"$ref": "Type"}]."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {
            "type": "array",
            "items": {"$ref": "#/components/schemas/Branch"},
        }
        result = _schema_to_compact_example(schema)
        assert result == [{"$ref": "Branch"}]

    def test_array_with_literal_items(self):
        """Array of literal items should return example values."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {
            "type": "array",
            "items": {"type": "string"},
        }
        result = _schema_to_compact_example(schema)
        assert result == ["example"]

    def test_string_with_enum(self):
        """Enum strings should use the first enum value."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {"type": "string", "enum": ["open", "closed"]}
        assert _schema_to_compact_example(schema) == "open"

    def test_string_with_format_date_time(self):
        """date-time format should generate a timestamp."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        schema = {"type": "string", "format": "date-time"}
        result = _schema_to_compact_example(schema)
        assert "2024-01-01" in result
        assert "T" in result

    def test_leaf_types(self):
        """Leaf types should return example values."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        assert _schema_to_compact_example({"type": "integer"}) == 0
        assert _schema_to_compact_example({"type": "number"}) == 0.0
        assert _schema_to_compact_example({"type": "boolean"}) is True
        assert _schema_to_compact_example({"type": "null"}) is None

    def test_unrecognized_type_returns_none(self):
        """Unknown type should return None."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        assert _schema_to_compact_example({"type": "file"}) is None

    def test_empty_array_items(self):
        """Array with empty items should return empty list."""
        from gitea_mcp_server.tools.examples import _schema_to_compact_example

        assert _schema_to_compact_example({"type": "array", "items": {}}) == []

    def test_serialize_tool_schema_with_raw_meta(self):
        """_serialize_tool_schema should use raw meta schema for compact example."""
        from gitea_mcp_server.tools.examples import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {"x": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "user": {
                                "anyOf": [
                                    {"$ref": "#/components/schemas/User"},
                                    {"type": "null"},
                                ],
                            },
                        },
                    },
                },
            },
            meta={"output_schema_raw": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "user": {
                                "anyOf": [
                                    {"$ref": "#/components/schemas/User"},
                                    {"type": "null"},
                                ],
                            },
                        },
                    },
                },
            }},
        )
        result = _serialize_tool_schema(tool)
        assert "output_example" in result
        # With raw meta, user should be {"$ref": "User"}
        assert result["output_example"]["user"] == {"$ref": "User"}
        assert result["output_example"]["id"] == 0

    def test_serialize_tool_schema_no_raw_meta_fallback(self):
        """Without raw meta, _serialize_tool_schema falls back to old behavior."""
        from gitea_mcp_server.tools.examples import _serialize_tool_schema

        tool = Tool(
            name="test_tool",
            description="Test",
            parameters={"properties": {"x": {"type": "integer"}}},
            output_schema={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                    },
                },
            },
            meta={},
        )
        result = _serialize_tool_schema(tool)
        assert "output_example" in result
        assert result["output_example"]["name"] == "example-name"


class TestLookupStringExampleSuffix:
    """Tests for _lookup_string_example suffix pattern matching (line 66)."""

    def test_suffix_pattern_matches_url(self):
        """_lookup_string_example matches suffix patterns like _url."""
        from gitea_mcp_server.tools.examples import _lookup_string_example

        # "html_url" should match the ("_url", "_uri", ...) suffix pattern
        result = _lookup_string_example("html_url")
        assert result == "https://example.com/path"

    def test_suffix_pattern_matches_sha(self):
        """_lookup_string_example matches suffix patterns like _sha."""
        from gitea_mcp_server.tools.examples import _lookup_string_example

        result = _lookup_string_example("commit_sha")
        assert result == "abc123def456"

    def test_suffix_pattern_matches_id(self):
        """_lookup_string_example matches suffix patterns like _id."""
        from gitea_mcp_server.tools.examples import _lookup_string_example

        result = _lookup_string_example("user_id")
        assert result == "example-id"
