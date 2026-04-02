"""Unit tests for OpenAPI converter - definitions and references."""

from gitea_mcp_server.openapi_converter import (
    convert_definitions,
    fix_references,
)


class TestFixReferences:
    """Tests for the fix_references function."""

    def test_fix_definitions_reference(self):
        spec = {"definitions": {"Model": {"type": "object"}}}
        result = fix_references(spec)
        assert "$ref" not in result  # No refs to fix yet

    def test_fix_path_parameter_reference(self):
        spec = {"paths": {"/test": {"get": {"parameters": [{"$ref": "#/definitions/Param"}]}}}}
        result = fix_references(spec)
        param_ref = result["paths"]["/test"]["get"]["parameters"][0]["$ref"]
        assert param_ref == "#/components/schemas/Param"

    def test_fix_response_reference(self):
        spec = {
            "responses": {"OK": {"description": "Success"}},
            "paths": {"/test": {"get": {"responses": {"200": {"$ref": "#/responses/OK"}}}}},
        }
        result = fix_references(spec)
        resp_ref = result["paths"]["/test"]["get"]["responses"]["200"]["$ref"]
        assert resp_ref == "#/components/responses/OK"

    def test_fix_nested_references(self):
        spec = {
            "definitions": {
                "Model": {"properties": {"nested": {"$ref": "#/definitions/Nested"}}},
                "Nested": {"type": "string"},
            }
        }
        result = fix_references(spec)
        nested_ref = result["definitions"]["Model"]["properties"]["nested"]["$ref"]
        assert nested_ref == "#/components/schemas/Nested"


class TestConvertDefinitions:
    """Tests for the convert_definitions function."""

    def test_simple_definition(self):
        definitions = {
            "User": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "format": "int64"},
                    "name": {"type": "string"},
                },
                "required": ["id"],
            }
        }
        result = convert_definitions(definitions)
        assert "User" in result
        assert result["User"]["type"] == "object"
        assert result["User"]["properties"]["id"]["type"] == "integer"
        # required should be kept
        assert "required" in result["User"]
        assert result["User"]["required"] == ["id"]

    def test_property_level_required(self):
        """Test that property-level required: true is collected to parent required array."""
        definitions = {
            "Pet": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "required": True},
                    "age": {"type": "integer"},
                    "owner": {"type": "string", "required": True},
                },
            }
        }
        result = convert_definitions(definitions)
        pet_schema = result["Pet"]
        # Should have required array at top level
        assert "required" in pet_schema
        assert set(pet_schema["required"]) == {"name", "owner"}
        # Individual properties should NOT have required field
        assert "required" not in pet_schema["properties"]["name"]
        assert "required" not in pet_schema["properties"]["owner"]

    def test_nested_definitions(self):
        definitions = {
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                },
            },
            "User": {
                "type": "object",
                "properties": {"address": {"$ref": "#/definitions/Address"}},
            },
        }
        result = convert_definitions(definitions)
        assert result["User"]["properties"]["address"]["$ref"] == "#/components/schemas/Address"
        assert result["Address"]["type"] == "object"

    def test_array_with_items_ref(self):
        definitions = {
            "Tag": {"type": "string"},
            "Article": {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"$ref": "#/definitions/Tag"}}},
            },
        }
        result = convert_definitions(definitions)
        tags_items = result["Article"]["properties"]["tags"]["items"]
        assert tags_items["$ref"] == "#/components/schemas/Tag"
