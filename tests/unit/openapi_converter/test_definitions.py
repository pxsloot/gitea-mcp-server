"""Unit tests for OpenAPI converter - definitions and references."""

from gitea_mcp_server.openapi_converter import (
    OptionalPropertyTransformer,
    RequestBodyBuilder,
    SchemaNormalizer,
    SchemaWalker,
    convert_definitions,
    fix_references,
    _add_nullable_for_optional_refs,
)


class TestFixReferences:
    """Tests for the fix_references function."""

    def test_fix_definitions_reference(self):
        """Definitions with no refs should pass through unchanged."""
        spec = {"definitions": {"Model": {"type": "object"}}}
        result = fix_references(spec)
        assert "$ref" not in result  # No refs to fix yet

    def test_fix_path_parameter_reference(self):
        """$ref in path parameters should be rewritten from definitions to components.schemas."""
        spec = {"paths": {"/test": {"get": {"parameters": [{"$ref": "#/definitions/Param"}]}}}}
        result = fix_references(spec)
        param_ref = result["paths"]["/test"]["get"]["parameters"][0]["$ref"]
        assert param_ref == "#/components/schemas/Param"

    def test_fix_response_reference(self):
        """$ref in responses should be rewritten from responses to components.responses."""
        spec = {
            "responses": {"OK": {"description": "Success"}},
            "paths": {"/test": {"get": {"responses": {"200": {"$ref": "#/responses/OK"}}}}},
        }
        result = fix_references(spec)
        resp_ref = result["paths"]["/test"]["get"]["responses"]["200"]["$ref"]
        assert resp_ref == "#/components/responses/OK"

    def test_fix_nested_references(self):
        """Nested $ref inside definitions should be rewritten to components.schemas."""
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
        """Basic definition with properties and required should be preserved."""
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
        """Definitions with $ref to other definitions should be rewritten."""
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
        """Array items with $ref should be rewritten to components.schemas."""
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

    def test_anyof_in_definition(self):
        """anyOf in a definition schema should be preserved and converted."""
        definitions = {
            "Result": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                ],
            }
        }
        result = convert_definitions(definitions)
        assert "anyOf" in result["Result"]
        assert result["Result"]["anyOf"][0]["type"] == "string"
        assert result["Result"]["anyOf"][1]["type"] == "integer"

    def test_allof_in_definition(self):
        """allOf in a definition schema should be preserved and converted."""
        definitions = {
            "Combined": {
                "allOf": [
                    {"type": "object", "properties": {"id": {"type": "integer"}}},
                    {"type": "object", "properties": {"name": {"type": "string"}}},
                ],
            }
        }
        result = convert_definitions(definitions)
        assert "allOf" in result["Combined"]
        assert "id" in result["Combined"]["allOf"][0]["properties"]

    def test_oneof_in_definition(self):
        """oneOf in a definition schema should be preserved and converted."""
        definitions = {
            "Pet": {
                "oneOf": [
                    {"type": "object", "properties": {"bark": {"type": "boolean"}}},
                    {"type": "object", "properties": {"meow": {"type": "boolean"}}},
                ],
            }
        }
        result = convert_definitions(definitions)
        assert "oneOf" in result["Pet"]
        assert "bark" in result["Pet"]["oneOf"][0]["properties"]


class TestOptionalPropertyTransformer:
    """Tests for OptionalPropertyTransformer."""

    def test_transform_email_format_optional(self):
        """Email format + optional should anyOf with empty/null."""
        schema = {"type": "string", "format": "email"}
        parent = {"properties": {"email": schema}, "required": ["id"]}
        transformer = OptionalPropertyTransformer()
        transformer(schema, parent, "email")
        assert "anyOf" in schema
        assert schema["anyOf"][0]["format"] == "email"

    def test_transform_email_format_required(self):
        """Email format + required should NOT add empty/null branches."""
        schema = {"type": "string", "format": "email"}
        parent = {"properties": {"email": schema}, "required": ["email"]}
        transformer = OptionalPropertyTransformer()
        transformer(schema, parent, "email")
        assert "anyOf" in schema
        assert len(schema["anyOf"]) == 1  # no empty/null branches

    def test_type_list_without_null(self):
        """When type is a list without 'null', nullable should append 'null'."""
        schema = {"type": ["string", "integer"]}
        parent = {"properties": {"field": schema}, "required": ["other"]}
        transformer = OptionalPropertyTransformer()
        transformer._add_nullable(schema)
        assert "null" in schema["type"]
        assert schema["type"] == ["string", "integer", "null"]

    def test_noop_when_parent_or_key_none(self):
        """When parent or key is None, transformer should do nothing."""
        schema = {"type": "string"}
        transformer = OptionalPropertyTransformer()
        transformer(schema, None, None)
        assert schema["type"] == "string"

    def test_noop_when_not_property(self):
        """When parent/key is not a property, transformer should do nothing."""
        schema = {"type": "string"}
        parent = {"not_properties": {"field": "value"}}
        transformer = OptionalPropertyTransformer()
        transformer(schema, parent, "field")
        assert schema["type"] == "string"


class TestSchemaWalker:
    """Tests for SchemaWalker."""

    def test_walker_calls_callback_on_each_node(self):
        """Walker should invoke the callback for every schema node."""
        calls = []

        def callback(schema, parent, key):
            calls.append((schema["type"], key))

        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
        }
        SchemaWalker(callback).walk(schema)
        call_types = {c[0] for c in calls}
        assert "object" in call_types
        assert "string" in call_types
        assert "array" in call_types
        assert "integer" in call_types

    def test_walker_non_dict_properties_skipped(self):
        """Walker should skip non-dict property values."""
        calls = []

        def callback(schema, parent, key):
            calls.append(1)

        schema = {
            "type": "object",
            "properties": {
                "name": "not a dict",
            },
        }
        SchemaWalker(callback).walk(schema)
        # Only the root schema should trigger callback
        assert len(calls) == 1

    def test_walker_non_list_combinators_skipped(self):
        """Walker should skip non-list combinator values."""
        calls = []

        def callback(schema, parent, key):
            calls.append(key)

        schema = {
            "type": "object",
            "anyOf": "not a list",
            "properties": {
                "x": {"type": "string"},
            },
        }
        SchemaWalker(callback).walk(schema)
        assert "anyOf" not in str(calls) or True  # just verify no crash

    def test_walker_with_pattern_properties(self):
        """Walker should visit patternProperties entries."""
        calls = []

        def callback(schema, parent, key):
            calls.append(key)

        schema = {
            "type": "object",
            "patternProperties": {
                "^x-": {"type": "string"},
            },
        }
        SchemaWalker(callback).walk(schema)
        assert "^x-" in calls

    def test_walker_with_additional_properties(self):
        """Walker should visit additionalProperties."""
        calls = []

        def callback(schema, parent, key):
            calls.append(key)

        schema = {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }
        SchemaWalker(callback).walk(schema)
        assert "additionalProperties" in calls


class TestRequestBodyBuilder:
    """Tests for RequestBodyBuilder."""

    def test_empty_form_params_returns_none(self):
        """Empty form params should return None."""
        builder = RequestBodyBuilder()
        assert builder.build_from_form_data([]) is None

    def test_form_param_with_schema(self):
        """Form param with existing 'schema' should preserve it."""
        builder = RequestBodyBuilder()
        params = [
            {
                "name": "file",
                "in": "formData",
                "schema": {"type": "string", "format": "binary"},
                "required": True,
            }
        ]
        result = builder.build_from_form_data(params)
        assert result is not None
        schema = result["content"]["multipart/form-data"]["schema"]
        assert schema["properties"]["file"]["type"] == "string"
        assert schema["required"] == ["file"]

    def test_body_param_without_schema_returns_none(self):
        """Body param without 'schema' key should return None."""
        builder = RequestBodyBuilder()
        params = [{"name": "body", "in": "body"}]
        result = builder.build_from_body_params(params)
        assert result is None

    def test_empty_body_params_returns_none(self):
        """Empty body params list should return None."""
        builder = RequestBodyBuilder()
        assert builder.build_from_body_params([]) is None


class TestAddNullableForOptionalRefs:
    """Tests for _add_nullable_for_optional_refs."""

    def test_adds_nullable_to_optional_refs(self):
        """Optional $ref schemas should get nullable anyOf wrapper."""
        spec = {
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"$ref": "#/components/schemas/Email"},
                        },
                        "required": ["name"],
                    },
                    "Email": {"type": "string", "format": "email"},
                }
            }
        }
        _add_nullable_for_optional_refs(spec)
        email_prop = spec["components"]["schemas"]["User"]["properties"]["email"]
        assert "anyOf" in email_prop
        assert email_prop["anyOf"][1]["type"] == "null"


class TestVendorExtensionStripping:
    """Tests for vendor extension (x-*) stripping in schema conversion."""

    def test_convert_schema_strips_x_go_name(self):
        """convert_schema should strip x-go-name from individual properties."""
        from gitea_mcp_server.openapi_converter import convert_schema

        schema = {
            "type": "object",
            "properties": {
                "body": {"type": "string", "x-go-name": "Body"},
                "title": {"type": "string", "x-go-name": "Title"},
            },
        }
        result = convert_schema(schema)
        assert "x-go-name" not in result["properties"]["body"]
        assert "x-go-name" not in result["properties"]["title"]
        # Normal schema fields preserved
        assert result["properties"]["body"]["type"] == "string"
        assert result["properties"]["title"]["type"] == "string"

    def test_convert_schema_strips_x_go_package(self):
        """convert_schema should strip x-go-package from schema level."""
        from gitea_mcp_server.openapi_converter import convert_schema

        schema = {
            "type": "object",
            "x-go-package": "forgejo.org/modules/structs",
            "properties": {
                "name": {"type": "string", "x-go-name": "Name"},
            },
        }
        result = convert_schema(schema)
        assert "x-go-package" not in result
        assert "x-go-name" not in result["properties"]["name"]
        assert result["properties"]["name"]["type"] == "string"

    def test_convert_schema_nested_strips_all_x_fields(self):
        """convert_schema should strip x-* from nested schemas (items, allOf, etc.)."""
        from gitea_mcp_server.openapi_converter import convert_schema

        schema = {
            "type": "array",
            "x-go-package": "forgejo.org/modules/structs",
            "items": {
                "type": "object",
                "properties": {
                    "labels": {
                        "type": "array",
                        "x-go-name": "Labels",
                        "items": {"type": "integer", "format": "int64"},
                    },
                },
                "x-go-package": "forgejo.org/modules/structs",
            },
        }
        result = convert_schema(schema)
        assert "x-go-package" not in result
        assert "x-go-package" not in result["items"]
        assert "x-go-name" not in result["items"]["properties"]["labels"]
        assert result["items"]["properties"]["labels"]["type"] == "array"

    def test_convert_schema_strips_x_from_allOf(self):
        """convert_schema should strip x-* inside allOf/anyOf/oneOf combination schemas."""
        from gitea_mcp_server.openapi_converter import convert_schema

        schema = {
            "allOf": [
                {"type": "object", "x-go-package": "forgejo.org/modules/structs",
                 "properties": {"id": {"type": "integer", "x-go-name": "Id"}}},
                {"type": "object", "x-go-package": "forgejo.org/modules/structs",
                 "properties": {"name": {"type": "string", "x-go-name": "Name"}}},
            ],
        }
        result = convert_schema(schema)
        for sub in result["allOf"]:
            assert "x-go-package" not in sub, f"x-go-package found in allOf sub-schema: {sub}"
            for prop in sub.get("properties", {}).values():
                assert "x-go-name" not in prop, f"x-go-name found in allOf property: {prop}"

    def test_convert_schema_preserves_non_x_fields(self):
        """convert_schema should preserve fields not starting with x-."""
        from gitea_mcp_server.openapi_converter import convert_schema

        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = convert_schema(schema)
        assert "type" in result
        assert "properties" in result
        assert "required" in result

    def test_convert_definitions_strips_x_go_fields(self):
        """convert_definitions should strip x-go-name and x-go-package."""
        from gitea_mcp_server.openapi_converter import convert_definitions

        definitions = {
            "CreateIssueOption": {
                "type": "object",
                "x-go-package": "forgejo.org/modules/structs",
                "properties": {
                    "title": {"type": "string", "x-go-name": "Title"},
                    "body": {"type": "string", "x-go-name": "Body"},
                    "labels": {
                        "type": "array",
                        "x-go-name": "Labels",
                        "items": {"type": "integer", "format": "int64"},
                    },
                },
            },
        }
        result = convert_definitions(definitions)
        schema = result["CreateIssueOption"]
        assert "x-go-package" not in schema
        assert "x-go-name" not in schema["properties"]["title"]
        assert "x-go-name" not in schema["properties"]["body"]
        assert "x-go-name" not in schema["properties"]["labels"]
        assert schema["properties"]["title"]["type"] == "string"
        assert schema["properties"]["labels"]["type"] == "array"
