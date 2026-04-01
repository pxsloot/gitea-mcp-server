"""Unit tests for OpenAPI converter."""

import json
import logging
from pathlib import Path

import pytest
import jsonschema

from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.openapi_converter import (
    camel_to_snake,
    convert_definitions,
    convert_parameters,
    convert_paths,
    convert_responses,
    convert_swagger_to_openapi_v3,
    fix_references,
)

# Enable logging for debugging during tests
logging.basicConfig(level=logging.DEBUG)


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


class TestConvertParameters:
    """Tests for the convert_parameters function."""

    def test_simple_parameter(self):
        params = [{"name": "page", "in": "query", "type": "integer", "description": "Page number"}]
        result = convert_parameters(params)
        assert len(result) == 1
        assert result[0]["name"] == "page"
        assert result[0]["in"] == "query"
        # Type should be wrapped in schema
        assert "schema" in result[0]
        assert result[0]["schema"]["type"] == "integer"
        # Description stays at top level
        assert result[0]["description"] == "Page number"

    def test_body_parameter_removed(self):
        params = [{"name": "body", "in": "body", "schema": {"type": "object"}}]
        result = convert_parameters(params)
        assert len(result) == 0  # Body params are skipped

    def test_formData_parameter(self):
        params = [
            {"name": "file", "in": "formData", "type": "string"},
            {"name": "query", "in": "query", "type": "string"},
        ]
        result = convert_parameters(params)
        # formData parameters are marked with _skip_formData and then removed
        # We end up with only the query param
        assert len(result) == 1
        assert result[0]["name"] == "query"

    def test_parameter_with_schema(self):
        params = [{"name": "user", "in": "query", "schema": {"type": "string", "minLength": 1}}]
        result = convert_parameters(params)
        # Schema should be preserved and normalized
        assert "schema" in result[0]
        assert result[0]["schema"]["type"] == "string"
        assert result[0]["schema"]["minLength"] == 1
        # Top-level should not have type/minLength directly
        assert "type" not in result[0]
        assert "minLength" not in result[0]


class TestConvertResponses:
    """Tests for the convert_responses function."""

    def test_simple_response(self):
        responses = {
            "200": {
                "description": "Success",
                "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
            }
        }
        result = convert_responses(responses)
        assert "200" in result
        assert "content" in result["200"]
        assert "application/json" in result["200"]["content"]
        schema = result["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"

    def test_response_without_schema(self):
        responses = {"204": {"description": "No Content"}}
        result = convert_responses(responses)
        assert "204" in result
        assert "content" not in result["204"]


class TestConvertPaths:
    """Tests for the convert_paths function."""

    def test_simple_get(self):
        paths = {
            "/users": {
                "get": {
                    "summary": "List users",
                    "operationId": "listUsers",
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert "/users" in result
        assert result["/users"]["get"]["summary"] == "List users"

    def test_post_with_body(self):
        paths = {
            "/users": {
                "post": {
                    "parameters": [{"name": "body", "in": "body", "schema": {"type": "object"}}],
                    "responses": {"201": {"description": "Created"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/users"]["post"]
        assert "requestBody" in op
        assert "application/json" in op["requestBody"]["content"]

    def test_post_with_formData(self):
        paths = {
            "/upload": {
                "post": {
                    "parameters": [
                        {"name": "file", "in": "formData", "type": "string"},
                        {"name": "name", "in": "formData", "type": "string", "required": True},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/upload"]["post"]
        assert "requestBody" in op
        # Should have both multipart/form-data and application/x-www-form-urlencoded
        assert "multipart/form-data" in op["requestBody"]["content"]
        assert "application/x-www-form-urlencoded" in op["requestBody"]["content"]
        schema = op["requestBody"]["content"]["multipart/form-data"]["schema"]
        assert schema["type"] == "object"
        assert "file" in schema["properties"]
        assert "name" in schema["properties"]
        assert schema["required"] == ["name"]

    def test_mixed_parameters(self):
        paths = {
            "/search": {
                "get": {
                    "parameters": [
                        {"name": "q", "in": "query", "type": "string"},
                        {"name": "body", "in": "body", "schema": {"type": "object"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/search"]["get"]
        # Should have both query parameter and requestBody
        assert any(p["name"] == "q" for p in op["parameters"])
        assert "requestBody" in op


# Load OpenAPI 3.1 schema once
OAS_3_1_SCHEMA = None
try:
    schema_path = Path(__file__).parent.parent / "schemas" / "openapi_3.1_schema.json"
    if schema_path.exists():
        with open(schema_path) as f:
            OAS_3_1_SCHEMA = json.load(f)
except Exception as e:
    logging.getLogger(__name__).warning(f"Failed to load OpenAPI schema: {e}")


class TestConvertSwaggerToOpenAPI:
    """Full integration tests for conversion."""

    def test_minimal_swagger_spec(self):
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "basePath": "/api/v1",
            "paths": {"/ping": {"get": {"responses": {"200": {"description": "pong"}}}}},
        }
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        assert "servers" in result
        assert result["servers"][0]["url"] == "/api/v1"
        assert "/ping" in result["paths"]

    def test_full_spec_with_definitions(self):
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "definitions": {
                "Pet": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                }
            },
            "paths": {
                "/pets": {"get": {"responses": {"200": {"schema": {"$ref": "#/definitions/Pet"}}}}}
            },
        }
        result = convert_swagger_to_openapi_v3(spec)
        assert "components" in result
        assert "schemas" in result["components"]
        assert "Pet" in result["components"]["schemas"]

    def test_invalid_swagger_version(self):
        spec = {"openapi": "3.0.0"}
        with pytest.raises(SpecError, match="Expected Swagger 2.0"):
            convert_swagger_to_openapi_v3(spec)

    def test_invalid_input_type(self):
        with pytest.raises(SpecError, match="must be a dictionary"):
            convert_swagger_to_openapi_v3("not a dict")

    def test_load_real_swagger_file(self):
        """Test loading the actual swagger.v1.json file."""
        spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
        if not spec_path.exists():
            pytest.skip("swagger.v1.json not found")

        with open(spec_path) as f:
            spec = json.load(f)

        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        assert "paths" in result
        assert len(result["paths"]) > 0

    def test_valid_openapi_3_1_schema(self):
        """Test that the converted spec is valid against OpenAPI 3.1 schema."""
        if OAS_3_1_SCHEMA is None:
            pytest.skip("OpenAPI 3.1 schema not available")

        spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
        if not spec_path.exists():
            pytest.skip("swagger.v1.json not found")

        with open(spec_path) as f:
            spec = json.load(f)

        result = convert_swagger_to_openapi_v3(spec)

        # Validate against OpenAPI 3.1 JSON Schema
        try:
            jsonschema.validate(instance=result, schema=OAS_3_1_SCHEMA)
        except jsonschema.ValidationError as e:
            pytest.fail(f"OpenAPI spec validation failed: {e.message}")


class TestCamelToSnake:
    """Tests for the camel_to_snake conversion function."""

    def test_simple_camelcase(self):
        assert camel_to_snake("getAllRepos") == "get_all_repos"

    def test_simple_pascalcase(self):
        assert camel_to_snake("CreateIssue") == "create_issue"

    def test_multiple_camel_phrases(self):
        assert camel_to_snake("issueCreateIssue") == "issue_create_issue"
        assert camel_to_snake("repoGetBranch") == "repo_get_branch"

    def test_consecutive_uppercase(self):
        assert camel_to_snake("GetURL") == "get_url"
        assert camel_to_snake("listAPIKeys") == "list_api_keys"

    def test_single_word(self):
        assert camel_to_snake("get") == "get"
        assert camel_to_snake("GET") == "get"

    def test_with_numbers(self):
        assert camel_to_snake("getV1") == "get_v1"
        assert (
            camel_to_snake("list2FA") == "list2_fa"
        )  # Digit not separated from following uppercase

    def test_already_snake_case(self):
        assert camel_to_snake("already_snake") == "already_snake"

    def test_edge_cases(self):
        assert camel_to_snake("") == ""
        assert camel_to_snake("A") == "a"
        assert camel_to_snake("AB") == "ab"


class TestOperationIdNormalization:
    """Tests for operationId snake_case conversion in convert_paths."""

    def test_camelcase_operation_id_converted(self):
        paths = {
            "/repos": {
                "get": {
                    "operationId": "getAllRepos",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/repos"]["get"]["operationId"] == "get_all_repos"

    def test_pascalcase_operation_id_converted(self):
        paths = {
            "/issues": {
                "post": {
                    "operationId": "CreateIssue",
                    "responses": {"201": {"description": "Created"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/issues"]["post"]["operationId"] == "create_issue"

    def test_mixed_operation_id_converted(self):
        paths = {
            "/repos/{owner}/{repo}/branches": {
                "get": {
                    "operationId": "repoGetBranches",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/repos/{owner}/{repo}/branches"]["get"]["operationId"] == "repo_get_branches"

    def test_generated_operation_id_is_snake_case(self):
        """Test that auto-generated operationIds are also snake_case."""
        paths = {
            "/users/{id}": {
                "put": {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        op_id = result["/users/{id}"]["put"]["operationId"]
        # Generated: method + path with slashes replaced (keeps {param} syntax)
        assert op_id == "put_users_{id}"
        # Verify it's snake_case (no uppercase letters)
        assert op_id == op_id.lower()

    def test_complex_operation_id_with_acronyms(self):
        paths = {
            "/orgs/{org}/ teams": {
                "get": {
                    "operationId": "getOrgTeams",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/orgs/{org}/ teams"]["get"]["operationId"] == "get_org_teams"

    def test_preserves_snake_case_operation_id(self):
        """Test that already snake_case operationIds remain unchanged."""
        paths = {
            "/test": {
                "get": {
                    "operationId": "already_snake_case",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/test"]["get"]["operationId"] == "already_snake_case"
