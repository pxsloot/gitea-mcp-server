"""Unit tests for schema utilities (type detection, output schema, ref resolution)."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.server_setup.mcp_builder import (
    _customize_metadata,
    _ToolWrappingTransform,
)
from gitea_mcp_server.tools.schemas import (
    _deep_resolve_schema,
    _is_text_response,
    _schema_type_is_array,
    derive_output_schema,
)

class TestSchemaTypeIsArray:
    """Tests for _schema_type_is_array."""

    def test_detects_string_type(self):
        """Should return True for type 'array'."""
        from gitea_mcp_server.tools.schemas import _schema_type_is_array

        assert _schema_type_is_array({"type": "array"}) is True

    def test_detects_list_type(self):
        """Should return True for type ['array', 'null']."""
        from gitea_mcp_server.tools.schemas import _schema_type_is_array

        assert _schema_type_is_array({"type": ["array", "null"]}) is True

    def test_rejects_non_array_string(self):
        """Should return False for non-array string types."""
        from gitea_mcp_server.tools.schemas import _schema_type_is_array

        assert _schema_type_is_array({"type": "string"}) is False
        assert _schema_type_is_array({"type": "object"}) is False

    def test_rejects_non_array_list(self):
        """Should return False when 'array' not in type list."""
        from gitea_mcp_server.tools.schemas import _schema_type_is_array

        assert _schema_type_is_array({"type": ["string", "null"]}) is False

    def test_no_type_key(self):
        """Should return False when no type key."""
        from gitea_mcp_server.tools.schemas import _schema_type_is_array

        assert _schema_type_is_array({}) is False


class TestDeriveOutputSchema:
    """Tests for derive_output_schema function."""

    MINIMAL_SPEC: dict = {
        "openapi": "3.1.0",
        "paths": {
            "/repos/{owner}/{repo}/issues/{index}": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Issue",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer", "description": "Issue ID"},
                                            "title": {"type": "string"},
                                            "body": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    }
                },
                "delete": {
                    "responses": {
                        "204": {"description": "No Content"},
                    }
                },
            },
            "/repos/{owner}/{repo}/issues": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "IssueList",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            },
        },
        "components": {
            "schemas": {
                "Repository": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                }
            },
            "responses": {
                "Repository": {
                    "description": "Repository",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Repository"}
                        }
                    },
                }
            },
        },
    }

    def _make_route(self, path: str, method: str = "GET") -> MagicMock:
        """Helper to create a mock route."""
        return MagicMock(path=path, method=method, summary="Test", operation_id="test_op")

    def test_inline_schema_response(self):
        """Should extract inline schema directly from response content."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)

        assert schema is not None
        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "title" in schema["properties"]

    def test_array_response(self):
        """Should handle array-type response schemas."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/repos/{owner}/{repo}/issues", "GET")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)

        assert schema is not None
        assert schema["type"] == "array"
        assert schema["items"]["type"] == "object"

    def test_ref_response_resolved(self):
        """Should resolve $ref in response to get the schema."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        spec_with_ref: dict = {
            "openapi": "3.1.0",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {
                        "responses": {
                            "200": {"$ref": "#/components/responses/Repository"}
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Repository": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    }
                },
                "responses": {
                    "Repository": {
                        "description": "Repository",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Repository"}
                            }
                        },
                    }
                },
            },
        }

        route = self._make_route("/repos/{owner}/{repo}", "GET")
        schema = derive_output_schema(route, spec_with_ref)

        assert schema is not None
        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "name" in schema["properties"]

    def test_no_content_response_returns_none(self):
        """204 No Content responses should return None."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "DELETE")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)
        assert schema is None

    def test_none_spec_returns_none(self):
        """When spec is None, should return None."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/test", "GET")
        schema = derive_output_schema(route, None)
        assert schema is None

    def test_missing_path_returns_none(self):
        """When route path is not in spec, should return None."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/nonexistent/path", "GET")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)
        assert schema is None

    def test_missing_method_returns_none(self):
        """When route method is not in spec, should return None."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "PATCH")
        schema = derive_output_schema(route, self.MINIMAL_SPEC)
        assert schema is None

    def test_prefers_200_over_201(self):
        """Should prefer 200 over 201 when both are present."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        spec: dict = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "post": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"from_200": {"type": "string"}}}
                                    }
                                },
                            },
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"from_201": {"type": "string"}}}
                                    }
                                },
                            },
                        }
                    }
                }
            },
        }

        route = self._make_route("/test", "POST")
        schema = derive_output_schema(route, spec)
        assert schema is not None
        assert "from_200" in schema["properties"]
        assert "from_201" not in schema["properties"]

    def test_falls_back_to_201_when_no_200(self):
        """Should fall back to 201 when no 200 response exists."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        spec: dict = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "post": {
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }

        route = self._make_route("/test", "POST")
        schema = derive_output_schema(route, spec)
        assert schema is not None
        assert "id" in schema["properties"]

    def test_integration_via_customize_metadata(self):
        """_customize_metadata should set output_schema from openapi_spec."""
        from gitea_mcp_server.openapi_converter import _wrap_success_response_schemas

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = None
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get an issue"
        tool.meta = {}

        spec = deepcopy(self.MINIMAL_SPEC)
        _wrap_success_response_schemas(spec)
        _customize_metadata(route, tool, openapi_spec=spec)

        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "result" in tool.output_schema["properties"]
        assert "id" in tool.output_schema["properties"]["result"]["properties"]
        assert "title" in tool.output_schema["properties"]["result"]["properties"]
        assert tool.output_schema.get("x-fastmcp-wrap-result") is True

    def test_no_output_schema_without_spec(self):
        """_customize_metadata should not set output_schema when spec has no matching path."""
        route = self._make_route("/test", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        assert tool.output_schema is None

    @pytest.mark.asyncio
    async def test_transform_pipeline_passes_results_through(self):
        """_ToolWrappingTransform should pass ToolResult through unchanged for JSON object endpoints."""
        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = None
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.meta = {}
        tool.version = "1"
        tool.auth = None
        tool.serializer = None

        _customize_metadata(route, tool, openapi_spec=self.MINIMAL_SPEC)
        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                content=[],
                structured_content={"result": [{"id": 1}, {"id": 2}]},
            )

            transform = _ToolWrappingTransform(
                openapi_spec=self.MINIMAL_SPEC,
            )
            [wrapped] = await transform.list_tools([tool])
            actual = await wrapped.run({"owner": "test", "repo": "test"})

            assert actual.structured_content == {"result": [{"id": 1}, {"id": 2}]}

    @pytest.mark.asyncio
    async def test_x_fastmcp_flag_and_result_passthrough(self):
        """_customize_metadata sets x-fastmcp-wrap-result; transform passes through."""
        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = None
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.meta = {}
        tool.version = "1"
        tool.auth = None
        tool.serializer = None

        _customize_metadata(route, tool, openapi_spec=self.MINIMAL_SPEC)
        assert tool.output_schema is not None
        assert tool.output_schema.get("x-fastmcp-wrap-result") is True
        assert "id" in tool.output_schema["properties"]

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                content=[],
                structured_content={"result": {"id": 1}},
            )

            transform = _ToolWrappingTransform(
                openapi_spec=self.MINIMAL_SPEC,
            )
            [wrapped] = await transform.list_tools([tool])
            actual = await wrapped.run({"owner": "test", "repo": "test"})

            assert actual.structured_content == {"result": {"id": 1}}

    @pytest.mark.asyncio
    async def test_transform_pipeline_handles_array_result(self):
        """When the output schema declares an array result, the transform pipeline
        passes through and injects pagination metadata."""
        from gitea_mcp_server.openapi_converter import _wrap_success_response_schemas

        route = self._make_route("/repos/{owner}/{repo}/issues", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_list_issues"
        tool.annotations = None
        tool.tags = {"issue"}
        tool.parameters = {"properties": {"page": {"type": "integer"}, "per_page": {"type": "integer"}}}
        tool.output_schema = None
        tool.description = ""
        tool.meta = {}
        tool.version = "1"
        tool.auth = None
        tool.serializer = None

        spec = deepcopy(self.MINIMAL_SPEC)
        _wrap_success_response_schemas(spec)
        _customize_metadata(route, tool, openapi_spec=spec)
        assert tool.output_schema is not None
        assert tool.output_schema.get("x-fastmcp-wrap-result") is True
        assert "result" in tool.output_schema["properties"]

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder._run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                content=[],
                structured_content={"result": [{"id": 1}]},
            )

            transform = _ToolWrappingTransform(
                openapi_spec=spec,
            )
            [wrapped] = await transform.list_tools([tool])
            actual = await wrapped.run(arguments={"page": 1, "per_page": 10})

            assert actual.structured_content["result"] == [{"id": 1}]


class TestIsTextResponse:
    """Tests for _is_text_response function."""

    @pytest.fixture
    def text_spec(self):
        return {
            "openapi": "3.1.1",
            "paths": {
                "/repos/{owner}/{repo}/pulls/{index}.{diffType}": {
                    "get": {
                        "x-original-content-types": ["text/plain"],
                        "responses": {
                            "200": {
                                "content": {
                                    "text/plain": {
                                        "schema": {"type": "string"},
                                    }
                                }
                            }
                        },
                    }
                },
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "array", "items": {"type": "object"}},
                                    }
                                }
                            }
                        }
                    }
                },
                "/no-content-types": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"},
                                    }
                                }
                            }
                        }
                    }
                },
            },
        }

    def test_text_plain_endpoint_detected(self, text_spec):
        from gitea_mcp_server.tools.schemas import _is_text_response
        assert _is_text_response(text_spec, "/repos/{owner}/{repo}/pulls/{index}.{diffType}", "get") is True

    def test_json_endpoint_not_text(self, text_spec):
        from gitea_mcp_server.tools.schemas import _is_text_response
        assert _is_text_response(text_spec, "/repos/{owner}/{repo}/issues", "get") is False

    def test_no_content_types_not_text(self, text_spec):
        from gitea_mcp_server.tools.schemas import _is_text_response
        assert _is_text_response(text_spec, "/no-content-types", "get") is False

    def test_missing_path_returns_false(self, text_spec):
        from gitea_mcp_server.tools.schemas import _is_text_response
        assert _is_text_response(text_spec, "/nonexistent", "get") is False

    def test_missing_method_returns_false(self, text_spec):
        from gitea_mcp_server.tools.schemas import _is_text_response
        assert _is_text_response(text_spec, "/repos/{owner}/{repo}/issues", "post") is False


class TestTextResponseOutputSchema:
    """Tests that text/plain endpoints get no output_schema."""

    TEXT_SPEC: dict = {
        "openapi": "3.1.1",
        "paths": {
            "/repos/{owner}/{repo}/pulls/{index}.{diffType}": {
                "get": {
                    "x-original-content-types": ["text/plain"],
                    "responses": {
                        "200": {
                            "description": "APIString is a string response",
                            "content": {
                                "text/plain": {
                                    "schema": {"type": "string"},
                                }
                            },
                        }
                    },
                }
            },
            "/repos/{owner}/{repo}/issues": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "IssueList",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                                    }
                                }
                            },
                        }
                    }
                }
            },
        },
    }

    def _make_route(self, path: str, method: str = "GET") -> MagicMock:
        return MagicMock(path=path, method=method, summary="Test", operation_id="test_op")

    def test_text_plain_derive_output_schema_none(self):
        """text/plain endpoints should return None from derive_output_schema."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/repos/{owner}/{repo}/pulls/{index}.{diffType}", "GET")
        schema = derive_output_schema(route, self.TEXT_SPEC)
        assert schema is None

    def test_json_still_gets_output_schema(self):
        """JSON endpoints should still get output_schema."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        route = self._make_route("/repos/{owner}/{repo}/issues", "GET")
        schema = derive_output_schema(route, self.TEXT_SPEC)
        assert schema is not None
        assert schema["type"] == "array"

    def test_text_plain_customize_metadata_sets_fallback_schema(self):
        """_customize_metadata should set lightweight string output_schema for text/plain endpoints."""
        route = self._make_route("/repos/{owner}/{repo}/pulls/{index}.{diffType}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_download_pull_diff_or_patch"
        tool.annotations = None
        tool.tags = {"repository"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get a pull request diff or patch"
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec=self.TEXT_SPEC)

        # Phase 4 (#352): text/plain endpoints get a lightweight string fallback schema
        assert tool.output_schema is not None, "Expected fallback schema for text/plain"
        assert tool.output_schema["type"] == "object"
        assert tool.output_schema["properties"]["result"]["type"] == "string"
        assert tool.output_schema.get("x-fastmcp-wrap-result") is True


class TestDeepResolveSchema:
    """Tests for _deep_resolve_schema function."""

    SPEC: dict = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "login": {"type": "string"},
                    },
                },
                "Repository": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "owner": {"$ref": "#/components/schemas/User"},
                    },
                },
                "NestedRef": {
                    "type": "object",
                    "properties": {
                        "repo": {"$ref": "#/components/schemas/Repository"},
                    },
                },
                "AllOfSchema": {
                    "allOf": [
                        {"$ref": "#/components/schemas/User"},
                        {"type": "object", "properties": {"extra": {"type": "string"}}},
                    ],
                },
                "ArraySchema": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/User"},
                },
            },
        },
    }

    def test_resolves_nested_property_refs(self):
        """Resolves $ref inside property values."""
        from gitea_mcp_server.tools.schemas import _deep_resolve_schema

        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "user": {"$ref": "#/components/schemas/User"},
            },
        }
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["properties"]["user"]["type"] == "object"
        assert resolved["properties"]["user"]["properties"]["id"]["type"] == "integer"
        assert resolved["properties"]["user"]["properties"]["login"]["type"] == "string"

    def test_resolves_items_ref(self):
        """Resolves $ref in array items."""
        from gitea_mcp_server.tools.schemas import _deep_resolve_schema

        schema = {
            "type": "array",
            "items": {"$ref": "#/components/schemas/User"},
        }
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["items"]["type"] == "object"
        assert "id" in resolved["items"]["properties"]

    def test_resolves_chain_of_refs(self):
        """Resolves $ref chains (Repo -> User -> no more refs)."""
        from gitea_mcp_server.tools.schemas import _deep_resolve_schema

        schema = {"$ref": "#/components/schemas/NestedRef"}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["type"] == "object"
        assert resolved["properties"]["repo"]["type"] == "object"
        assert resolved["properties"]["repo"]["properties"]["owner"]["type"] == "object"
        assert resolved["properties"]["repo"]["properties"]["owner"]["properties"]["login"]["type"] == "string"

    def test_resolves_allOf_entries(self):
        """Recursively resolves $ref inside allOf entries."""
        from gitea_mcp_server.tools.schemas import _deep_resolve_schema

        schema = {"$ref": "#/components/schemas/AllOfSchema"}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["allOf"][0]["type"] == "object"
        assert resolved["allOf"][0]["properties"]["id"]["type"] == "integer"

    def test_resolves_top_level_ref(self):
        """Resolves a top-level $ref."""
        from gitea_mcp_server.tools.schemas import _deep_resolve_schema

        schema = {"$ref": "#/components/schemas/User"}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved["type"] == "object"
        assert resolved["properties"]["id"]["type"] == "integer"
        assert resolved["properties"]["login"]["type"] == "string"

    def test_leaf_schema_unchanged(self):
        """A schema with no refs should return a copy unchanged."""
        from gitea_mcp_server.tools.schemas import _deep_resolve_schema

        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        resolved = _deep_resolve_schema(schema, self.SPEC)
        assert resolved == schema

    def test_circular_ref_does_not_loop(self):
        """Circular $ref should not cause infinite recursion."""
        from gitea_mcp_server.tools.schemas import _deep_resolve_schema

        circular_spec = {
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "child": {"$ref": "#/components/schemas/Node"},
                        },
                    },
                },
            },
        }
        schema = {"$ref": "#/components/schemas/Node"}
        resolved = _deep_resolve_schema(schema, circular_spec)
        assert resolved["type"] == "object"
        assert resolved["properties"]["id"]["type"] == "integer"
        assert resolved["properties"]["child"]["$ref"] == "#/components/schemas/Node"

    def test_deep_resolve_applied_in_derive_output_schema(self):
        """derive_output_schema should deep-resolve nested refs."""
        from gitea_mcp_server.tools.schemas import derive_output_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "owner": {"$ref": "#/components/schemas/User"},
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
            },
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "login": {"type": "string"},
                        },
                    },
                },
            },
        }
        route = MagicMock(path="/repos/{owner}/{repo}", method="GET")
        schema = derive_output_schema(route, spec)
        assert schema is not None
        assert schema["properties"]["owner"]["type"] == "object"
        assert schema["properties"]["owner"]["properties"]["login"]["type"] == "string"

    def test_deep_resolve_non_dict_schema(self):
        """_deep_resolve_schema should return {} for non-dict input."""
        assert _deep_resolve_schema("not a dict", {}) == {}

    def test_deep_resolve_ref_resolves_to_non_dict(self):
        """When $ref resolves to non-dict, should keep the $ref key."""
        spec = {
            "components": {
                "schemas": {
                    "Foo": "just a string",
                }
            }
        }
        result = _deep_resolve_schema({"$ref": "#/components/schemas/Foo"}, spec)
        assert "$ref" in result

    def test_deep_resolve_custom_dict_key(self):
        """Non-standard keys with dict values should be deep-resolved."""
        spec = {"components": {"schemas": {"Bar": {"type": "string"}}}}
        schema = {
            "type": "object",
            "example": {"nested": {"$ref": "#/components/schemas/Bar"}},
        }
        resolved = _deep_resolve_schema(schema, spec)
        assert resolved["example"]["nested"]["type"] == "string"


class TestGetSuccessSchema:
    """Tests for _get_success_schema edge cases."""

    def test_non_dict_responses(self):
        """When responses is not a dict, should return None."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": "not a dict",
                    }
                }
            },
        }
        route = MagicMock(path="/test", method="GET")
        assert _get_success_schema(spec, "/test", "get") is None

    def test_ref_resolves_to_non_dict(self):
        """When $ref in response resolves to non-dict, should continue to next status code."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {"$ref": "#/components/responses/OK"},
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                    }
                                },
                            },
                        }
                    }
                }
            },
            "components": {
                "responses": {
                    "OK": "just a string",
                }
            },
        }
        result = _get_success_schema(spec, "/test", "get")
        assert result is not None
        assert result["type"] == "object"

    def test_non_dict_content(self):
        """When content is not a dict, should continue."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {"description": "OK", "content": "not a dict"},
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {"schema": {"type": "string"}}
                                },
                            },
                        }
                    }
                }
            },
        }
        result = _get_success_schema(spec, "/test", "get")
        assert result is not None

    def test_non_dict_json_content(self):
        """When application/json content is not a dict, should continue."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": "not a dict",
                                },
                            },
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {"schema": {"type": "string"}}
                                },
                            },
                        }
                    }
                }
            },
        }
        result = _get_success_schema(spec, "/test", "get")
        assert result is not None

    def test_non_dict_schema(self):
        """When schema is not a dict, should continue."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {"schema": "not a dict"},
                                },
                            },
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {"schema": {"type": "string"}}
                                },
                            },
                        }
                    }
                }
            },
        }
        result = _get_success_schema(spec, "/test", "get")
        assert result is not None


class TestGetRawSuccessSchema:
    """Tests for _get_success_schema with resolve=False."""

    def test_inline_schema_keeps_ref_intact(self):
        """resolve=False should return schema with $ref intact."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "user": {"$ref": "#/components/schemas/User"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "login": {"type": "string"},
                        },
                    },
                },
            },
        }
        result = _get_success_schema(spec, "/test", "get", resolve=False)
        assert result is not None
        user_schema = result["properties"]["user"]
        # $ref must survive - NOT deep-resolved
        assert "$ref" in user_schema
        assert user_schema["$ref"] == "#/components/schemas/User"

    def test_resolve_true_expands_ref(self):
        """resolve=True (default) should deep-resolve $ref."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "user": {"$ref": "#/components/schemas/User"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "login": {"type": "string"},
                        },
                    },
                },
            },
        }
        result = _get_success_schema(spec, "/test", "get", resolve=True)
        assert result is not None
        user_schema = result["properties"]["user"]
        # $ref must be resolved - not a $ref dict
        assert "$ref" not in user_schema
        assert user_schema["type"] == "object"
        assert user_schema["properties"]["id"]["type"] == "integer"

    def test_text_response_returns_none(self):
        """Text responses should return None regardless of resolve flag."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "text/plain": {"schema": {"type": "string"}},
                                },
                            },
                        },
                        "x-original-content-types": ["text/plain"],
                    },
                },
            },
        }
        assert _get_success_schema(spec, "/test", "get", resolve=False) is None
        assert _get_success_schema(spec, "/test", "get", resolve=True) is None

    def test_missing_path_returns_none(self):
        """Missing path should return None."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {"openapi": "3.1.0", "paths": {}}
        assert _get_success_schema(spec, "/nonexistent", "get", resolve=False) is None

    def test_missing_method_returns_none(self):
        """Missing method should return None."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {"schema": {"type": "string"}},
                                },
                            },
                        },
                    },
                },
            },
        }
        assert _get_success_schema(spec, "/test", "post", resolve=False) is None

    def test_prefers_200_over_201(self):
        """Should prefer 200 status code over 201."""
        from gitea_mcp_server.tools.schemas import _get_success_schema

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"from_200": {"type": "string"}}},
                                    },
                                },
                            },
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"from_201": {"type": "string"}}},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        result = _get_success_schema(spec, "/test", "get", resolve=False)
        assert result is not None
        assert "from_200" in result["properties"]
        assert "from_201" not in result["properties"]
