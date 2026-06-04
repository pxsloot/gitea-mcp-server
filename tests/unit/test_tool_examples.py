"""Unit tests for schema-to-example generation."""

import pytest
from fastmcp.tools.base import Tool

from gitea_mcp_server.tools.examples import _schema_to_example, _serialize_tool_schema

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
        assert result["name"] == "text"
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
        assert result[0] == "text"

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
        assert _schema_to_example(schema) == "text"

    def test_type_list_skips_null(self):
        from gitea_mcp_server.tools.examples import _schema_to_example

        schema = {"type": ["null", "string"]}
        assert _schema_to_example(schema) == "text"

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
        assert result[0]["label"] == "text"

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
        assert result["output_example"]["name"] == "text"

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
