"""Unit tests for OpenAPI converter - end-to-end Swagger to OpenAPI conversion."""

import json
import logging
from pathlib import Path

import jsonschema
import pytest

from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.openapi_converter import (
    BasePathToServerConverter,
    SecuritySchemeConverter,
    convert_swagger_to_openapi_v3,
    _wrap_success_response_schemas,
)

# Load OpenAPI 3.1 schema once
OAS_3_1_SCHEMA = None
try:
    schema_path = Path(__file__).parent.parent.parent / "schemas" / "openapi_3.1_schema.json"
    if schema_path.exists():
        with schema_path.open() as f:
            OAS_3_1_SCHEMA = json.load(f)
except (OSError, json.JSONDecodeError) as e:
    logging.getLogger(__name__).warning("Failed to load OpenAPI schema: %s", e)


class TestConvertSwaggerToOpenAPI:
    """Full integration tests for conversion."""

    def _minimal_spec(self) -> dict:
        return {
            "swagger": "2.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "basePath": "/api/v1",
            "paths": {"/ping": {"get": {"responses": {"200": {"description": "pong"}}}}},
        }

    def test_output_version_is_3_1_1(self):
        """Converted spec should have OpenAPI version 3.1.1."""
        result = convert_swagger_to_openapi_v3(self._minimal_spec())
        assert result["openapi"] == "3.1.1"

    def test_basepath_becomes_server_url(self):
        """Swagger basePath should become OpenAPI server URL."""
        result = convert_swagger_to_openapi_v3(self._minimal_spec())
        assert result["servers"][0]["url"] == "/api/v1"

    def test_paths_are_preserved(self):
        """All paths from Swagger spec should be preserved in output."""
        result = convert_swagger_to_openapi_v3(self._minimal_spec())
        assert "/ping" in result["paths"]

    def test_full_spec_with_definitions(self):
        """Definitions should be converted to components.schemas."""
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
        """Non-Swagger-2.0 input should raise SpecError."""
        spec = {"openapi": "3.0.0"}
        with pytest.raises(SpecError, match="Expected Swagger 2.0"):
            convert_swagger_to_openapi_v3(spec)

    def test_invalid_input_type(self):
        """Non-dict input should raise SpecError."""
        with pytest.raises(SpecError, match="must be a dictionary"):
            convert_swagger_to_openapi_v3("not a dict")

    def test_load_real_swagger_file(self):
        """Test loading the actual swagger.v1.json file."""
        spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
        with spec_path.open() as f:
            spec = json.load(f)

        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        assert "paths" in result
        assert len(result["paths"]) > 0

    def test_valid_openapi_3_1_schema(self):
        """Test that the converted spec is valid against OpenAPI 3.1 schema."""
        spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"

        with spec_path.open() as f:
            spec = json.load(f)

        result = convert_swagger_to_openapi_v3(spec)

        # Validate against OpenAPI 3.1 JSON Schema
        try:
            jsonschema.validate(instance=result, schema=OAS_3_1_SCHEMA)
        except jsonschema.ValidationError as e:
            pytest.fail(f"OpenAPI spec validation failed: {e.message}")

    def test_conversion_enriches_array_responses(self):
        """Converted spec should have array response schemas wrapped in result."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "paths": {
                "/items": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                                },
                            }
                        }
                    }
                }
            },
        }
        result = convert_swagger_to_openapi_v3(spec)
        schema = result["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["type"] == "array"

    def test_conversion_wraps_object_responses(self):
        """Object-type response schemas should also be wrapped in result."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "paths": {
                "/item": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                                },
                            }
                        }
                    }
                }
            },
        }
        result = convert_swagger_to_openapi_v3(spec)
        schema = result["paths"]["/item"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["type"] == "object"
        assert "id" in schema["properties"]["result"]["properties"]

    def test_text_plain_response_not_wrapped(self):
        """text/plain responses should remain as string schema, not wrapped in result.

        When a Swagger operation has ``produces: ['text/plain']``, the response
        should use ``text/plain`` content type and NOT be wrapped in ``result``.
        """
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "paths": {
                "/repos/{owner}/{repo}/pulls/{index}.{diffType}": {
                    "get": {
                        "produces": ["text/plain"],
                        "operationId": "repoDownloadPullDiffOrPatch",
                        "parameters": [
                            {"type": "string", "name": "owner", "in": "path", "required": True},
                            {"type": "string", "name": "repo", "in": "path", "required": True},
                            {"type": "integer", "format": "int64", "name": "index", "in": "path", "required": True},
                            {"enum": ["diff", "patch"], "type": "string", "name": "diffType", "in": "path", "required": True},
                        ],
                        "responses": {
                            "200": {
                                "description": "APIString is a string response",
                                "schema": {"type": "string"},
                            }
                        },
                    }
                },
            },
        }
        result = convert_swagger_to_openapi_v3(spec)
        path_item = result["paths"]["/repos/{owner}/{repo}/pulls/{index}.{diffType}"]["get"]

        # Response should use text/plain content type, not application/json
        response = path_item["responses"]["200"]
        assert "text/plain" in response["content"]
        assert "application/json" not in response["content"]

        # Schema should be a plain string, NOT wrapped in result object
        schema = response["content"]["text/plain"]["schema"]
        assert schema["type"] == "string"

        # x-original-content-types should be preserved
        assert path_item.get("x-original-content-types") == ["text/plain"]

    def _make_x_original_spec(self) -> dict:
        return {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "paths": {
                "/diff": {
                    "get": {
                        "produces": ["text/plain"],
                        "operationId": "getDiff",
                        "responses": {
                            "200": {"description": "OK", "schema": {"type": "string"}},
                        },
                    }
                },
                "/json": {
                    "get": {
                        "produces": ["application/json"],
                        "operationId": "getJson",
                        "responses": {
                            "200": {
                                "description": "OK",
                                "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
                            },
                        },
                    }
                },
                "/no-produces": {
                    "get": {
                        "operationId": "getNoProduces",
                        "responses": {
                            "200": {"description": "OK", "schema": {"type": "string"}},
                        },
                    }
                },
            },
        }

    def test_text_plain_endpoint_has_x_original_content_types(self):
        """text/plain endpoints should have x-original-content-types preserved."""
        result = convert_swagger_to_openapi_v3(self._make_x_original_spec())
        assert result["paths"]["/diff"]["get"].get("x-original-content-types") == ["text/plain"]

    def test_json_endpoint_does_not_have_x_original_content_types(self):
        """JSON endpoints should not have x-original-content-types marker."""
        result = convert_swagger_to_openapi_v3(self._make_x_original_spec())
        assert "x-original-content-types" not in result["paths"]["/json"]["get"]

    def test_no_produces_endpoint_lacks_x_original_content_types(self):
        """Endpoints without produces should not have x-original-content-types."""
        result = convert_swagger_to_openapi_v3(self._make_x_original_spec())
        assert "x-original-content-types" not in result["paths"]["/no-produces"]["get"]


class TestEnrichResponseSchemas:
    """Tests for _wrap_success_response_schemas function."""

    def test_wraps_array_schema(self):
        """Array response schemas should be wrapped in result object."""
        spec = {
            "paths": {
                "/items": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["type"] == "array"

    def test_wraps_object_schema(self):
        """Object response schemas should be wrapped in result object."""
        spec = {
            "paths": {
                "/item": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["paths"]["/item"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert "id" in schema["properties"]["result"]["properties"]

    def test_stays_unwrapped_when_ref_cannot_be_resolved(self):
        """$ref schemas with unresolvable targets should remain unchanged."""
        spec = {
            "paths": {
                "/item": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Item"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["paths"]["/item"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema

    def test_wraps_ref_schema(self):
        """$ref response schemas should be resolved and wrapped in result."""
        spec = {
            "paths": {
                "/item": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Item"}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Item": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    }
                }
            },
        }
        _wrap_success_response_schemas(spec)
        schema = spec["paths"]["/item"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert "id" in schema["properties"]["result"]["properties"]
        assert "$ref" not in schema

    def test_wraps_response_ref(self):
        """Response-level $ref is left as-is; component schema gets wrapped.

        Note: response-level $ref never appears in practice -- the Swagger 2.0
        to OpenAPI 3.x converter inlines all refs. This test just verifies
        no crash when one is encountered.
        """
        spec = {
            "paths": {
                "/version": {
                    "get": {
                        "responses": {
                            "200": {"$ref": "#/components/responses/ServerVersion"},
                        }
                    }
                }
            },
            "components": {
                "responses": {
                    "ServerVersion": {
                        "description": "ServerVersion",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ServerVersion"},
                            }
                        },
                    }
                },
                "schemas": {
                    "ServerVersion": {
                        "type": "object",
                        "properties": {"version": {"type": "string"}},
                    }
                },
            },
        }
        _wrap_success_response_schemas(spec)
        # Path-level $ref is left as-is (no content to wrap).
        path_response = spec["paths"]["/version"]["get"]["responses"]["200"]
        assert "$ref" in path_response
        # Component-level schema is wrapped.
        schema = spec["components"]["responses"]["ServerVersion"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert "version" in schema["properties"]["result"]["properties"]

    def test_wraps_primitive_schema(self):
        """Primitive (string) response schemas should be wrapped in result object."""
        spec = {
            "paths": {
                "/health": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["type"] == "string"

    def test_wraps_component_responses_inline(self):
        """Component-level response schemas should also be wrapped."""
        spec = {
            "components": {
                "responses": {
                    "ItemList": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["components"]["responses"]["ItemList"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]

    def test_wraps_component_responses_ref(self):
        """Component responses with $ref should be resolved and wrapped."""
        spec = {
            "components": {
                "responses": {
                    "ItemDetail": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Item"}
                            }
                        }
                    }
                },
                "schemas": {
                    "Item": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    }
                },
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["components"]["responses"]["ItemDetail"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert "id" in schema["properties"]["result"]["properties"]

    def test_skips_204_no_content(self):
        """204 No Content responses should be skipped (no content to wrap)."""
        spec = {
            "paths": {
                "/item/{id}": {
                    "delete": {
                        "responses": {
                            "204": {"description": "No Content"}
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        assert "content" not in spec["paths"]["/item/{id}"]["delete"]["responses"]["204"]

    def test_handles_empty_spec_gracefully(self):
        """Empty spec should not cause errors during wrapping."""
        spec: dict = {}
        _wrap_success_response_schemas(spec)
        assert spec == {}

    def test_wraps_201_created_responses(self):
        """201 Created responses should be wrapped like 200 responses."""
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "201": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["paths"]["/items"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]

    def test_wraps_multiple_methods_on_same_path(self):
        """Multiple HTTP methods on the same path should each get wrapping."""
        spec = {
            "paths": {
                "/items": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "array", "items": {"type": "object"}}
                                    }
                                }
                            }
                        }
                    },
                    "post": {
                        "responses": {
                            "201": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        get_schema = spec["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert get_schema["type"] == "object"
        assert "result" in get_schema["properties"]
        post_schema = spec["paths"]["/items"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]
        assert post_schema["type"] == "object"
        assert "result" in post_schema["properties"]
        assert "id" in post_schema["properties"]["result"]["properties"]

    def test_skips_text_plain_content(self):
        """text/plain content types should NOT be wrapped in result."""
        spec = {
            "paths": {
                "/diff": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "text/plain": {
                                        "schema": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _wrap_success_response_schemas(spec)
        schema = spec["paths"]["/diff"]["get"]["responses"]["200"]["content"]["text/plain"]["schema"]
        # text/plain schemas should remain unwrapped
        assert schema["type"] == "string"
        assert "properties" not in schema


class TestSecuritySchemeConverter:
    """Tests for SecuritySchemeConverter."""

    def _converter(self):
        return SecuritySchemeConverter()

    def test_basic_auth(self):
        """basic type should become http scheme basic."""
        sec_defs = {
            "basicAuth": {"type": "basic"},
        }
        result = self._converter().convert(sec_defs)
        assert result["basicAuth"]["type"] == "http"
        assert result["basicAuth"]["scheme"] == "basic"

    def test_api_key_with_in(self):
        """apiKey type should preserve name and in fields."""
        sec_defs = {
            "apiKey": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
        }
        result = self._converter().convert(sec_defs)
        assert result["apiKey"]["type"] == "apiKey"
        assert result["apiKey"]["name"] == "X-API-Key"
        assert result["apiKey"]["in"] == "header"

    def test_api_key_defaults(self):
        """apiKey without name/in should use defaults."""
        sec_defs = {
            "apiKey": {"type": "apiKey"},
        }
        result = self._converter().convert(sec_defs)
        assert result["apiKey"]["type"] == "apiKey"
        assert "name" not in result["apiKey"]
        assert "in" not in result["apiKey"]

    def test_oauth2_implicit(self):
        """oauth2 with flow=implicit should produce authorizationUrl."""
        sec_defs = {
            "oauth": {
                "type": "oauth2",
                "flow": "implicit",
                "authorizationUrl": "https://example.com/auth",
                "scopes": {"read": "read access"},
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["oauth"]["type"] == "oauth2"
        flows = result["oauth"]["flows"]
        assert "implicit" in flows
        assert flows["implicit"]["authorizationUrl"] == "https://example.com/auth"
        assert flows["implicit"]["scopes"] == {"read": "read access"}

    def test_oauth2_password(self):
        """oauth2 with flow=password should produce tokenUrl."""
        sec_defs = {
            "oauth": {
                "type": "oauth2",
                "flow": "password",
                "tokenUrl": "https://example.com/token",
                "scopes": {"write": "write access"},
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["oauth"]["type"] == "oauth2"
        flows = result["oauth"]["flows"]
        assert "password" in flows
        assert flows["password"]["tokenUrl"] == "https://example.com/token"
        assert flows["password"]["scopes"] == {"write": "write access"}

    def test_oauth2_client_credentials(self):
        """oauth2 with flow=clientCredentials should produce tokenUrl."""
        sec_defs = {
            "oauth": {
                "type": "oauth2",
                "flow": "clientCredentials",
                "tokenUrl": "https://example.com/token",
                "scopes": {"admin": "admin access"},
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["oauth"]["type"] == "oauth2"
        flows = result["oauth"]["flows"]
        assert "clientCredentials" in flows
        assert flows["clientCredentials"]["tokenUrl"] == "https://example.com/token"
        assert flows["clientCredentials"]["scopes"] == {"admin": "admin access"}

    def test_oauth2_authorization_code(self):
        """oauth2 with flow=authorizationCode should produce both urls."""
        sec_defs = {
            "oauth": {
                "type": "oauth2",
                "flow": "authorizationCode",
                "authorizationUrl": "https://example.com/auth",
                "tokenUrl": "https://example.com/token",
                "scopes": {"all": "full access"},
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["oauth"]["type"] == "oauth2"
        flows = result["oauth"]["flows"]
        assert "authorizationCode" in flows
        assert flows["authorizationCode"]["authorizationUrl"] == "https://example.com/auth"
        assert flows["authorizationCode"]["tokenUrl"] == "https://example.com/token"
        assert flows["authorizationCode"]["scopes"] == {"all": "full access"}

    def test_security_defs_without_flow(self):
        """oauth2 without flow key should produce empty flows."""
        sec_defs = {
            "oauth": {
                "type": "oauth2",
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["oauth"]["type"] == "oauth2"
        assert "flows" not in result["oauth"]

    def test_non_dict_details_skipped(self):
        """Non-dict security definition entries should be skipped."""
        sec_defs = {
            "bad": "not a dict",
        }
        result = self._converter().convert(sec_defs)
        assert result == {}

    def test_description_preserved(self):
        """Description field in security definitions should be preserved."""
        sec_defs = {
            "token": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "API tokens must be prepended with 'token' followed by a space.",
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["token"]["description"] == "API tokens must be prepended with 'token' followed by a space."

    def test_description_preserved_for_basic_auth(self):
        """Description should be preserved for basic auth type."""
        sec_defs = {
            "basic": {
                "type": "basic",
                "description": "Basic HTTP authentication",
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["basic"]["description"] == "Basic HTTP authentication"

    def test_description_preserved_for_oauth2(self):
        """Description should be preserved for OAuth2 type."""
        sec_defs = {
            "oauth": {
                "type": "oauth2",
                "flow": "implicit",
                "authorizationUrl": "https://example.com/auth",
                "description": "OAuth2 implicit flow",
            }
        }
        result = self._converter().convert(sec_defs)
        assert result["oauth"]["description"] == "OAuth2 implicit flow"


class TestBasePathToServerConverter:
    """Tests for BasePathToServerConverter."""

    def test_with_host(self):
        """When host is present, should construct full URL."""
        spec = {"basePath": "/api/v1", "host": "git.example.com", "schemes": ["https"]}
        BasePathToServerConverter().convert(spec)
        assert spec.get("servers") == [{"url": "https://git.example.com/api/v1"}]

    def test_without_host(self):
        """Without host, should use basePath only."""
        spec = {"basePath": "/api/v1"}
        BasePathToServerConverter().convert(spec)
        assert spec.get("servers") == [{"url": "/api/v1"}]

    def test_without_base_path(self):
        """Without basePath, should do nothing."""
        spec = {"host": "git.example.com"}
        BasePathToServerConverter().convert(spec)
        assert "servers" not in spec

    def test_default_scheme(self):
        """When no schemes given, default to http."""
        spec = {"basePath": "/api", "host": "git.example.com"}
        BasePathToServerConverter().convert(spec)
        assert spec["servers"][0]["url"].startswith("http://")
