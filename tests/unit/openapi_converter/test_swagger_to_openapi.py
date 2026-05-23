"""Unit tests for OpenAPI converter - end-to-end Swagger to OpenAPI conversion."""

import json
import logging
from pathlib import Path

import jsonschema
import pytest

from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3, enrich_response_schemas

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

        with spec_path.open() as f:
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

    def test_conversion_keeps_object_responses(self):
        """Object-type response schemas should remain unchanged."""
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
        assert "result" not in schema.get("properties", {})


class TestEnrichResponseSchemas:
    """Tests for enrich_response_schemas function."""

    def test_wraps_array_schema(self):
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
        enrich_response_schemas(spec)
        schema = spec["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["type"] == "array"

    def test_skips_object_schema(self):
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
        enrich_response_schemas(spec)
        schema = spec["paths"]["/item"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" not in schema

    def test_skips_ref_schema(self):
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
        enrich_response_schemas(spec)
        schema = spec["paths"]["/item"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema

    def test_wraps_primitive_schema(self):
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
        enrich_response_schemas(spec)
        schema = spec["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["type"] == "string"

    def test_wraps_component_responses(self):
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
        enrich_response_schemas(spec)
        schema = spec["components"]["responses"]["ItemList"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]

    def test_skips_204_no_content(self):
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
        enrich_response_schemas(spec)
        # Should not raise - 204 has no content schema
        assert True

    def test_handles_empty_spec_gracefully(self):
        spec: dict = {}
        enrich_response_schemas(spec)
        assert True

    def test_wraps_201_created_responses(self):
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
        enrich_response_schemas(spec)
        schema = spec["paths"]["/items"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "result" in schema["properties"]

    def test_wraps_multiple_methods_on_same_path(self):
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
        enrich_response_schemas(spec)
        get_schema = spec["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert get_schema["type"] == "object"
        assert "result" in get_schema["properties"]
        post_schema = spec["paths"]["/items"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]
        assert post_schema["type"] == "object"
        assert "result" not in post_schema["properties"]
