"""Unit tests for schema utilities (type detection, output schema, ref resolution)."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import ToolResult
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.tools.customize import customize_component
from gitea_mcp_server.tools.schemas import (
    _deep_resolve_schema,
    _is_text_response,
    _schema_type_is_array,
    derive_output_schema,
)

_label_manager = None  # not needed for schema tests

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

    def test_integration_via_customize_component(self):
        """customize_component should set output_schema from openapi_spec."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.openapi_converter import _wrap_success_response_schemas
        from gitea_mcp_server.tools.customize import customize_component

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get an issue"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        spec = deepcopy(self.MINIMAL_SPEC)
        _wrap_success_response_schemas(spec)
        new_tool = customize_component(route, tool, _label_manager, spec)

        assert new_tool is not None
        assert new_tool.output_schema is not None
        assert new_tool.output_schema["type"] == "object"
        assert "result" in new_tool.output_schema["properties"]
        assert "id" in new_tool.output_schema["properties"]["result"]["properties"]
        assert "title" in new_tool.output_schema["properties"]["result"]["properties"]

    def test_integration_no_output_schema_without_spec(self):
        """customize_component should not set output_schema when spec is None."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.tools.customize import customize_component

        route = self._make_route("/test", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = customize_component(route, tool, _label_manager, None)

        assert new_tool is not None
        assert new_tool.output_schema is None

    @pytest.mark.asyncio
    async def test_transform_fn_wraps_result_in_result_key(self):
        """transform_fn should wrap tool result in {'result': ...}."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.tools.customize import customize_component

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        tool.run = AsyncMock(return_value=[{"id": 1}, {"id": 2}])

        new_tool = customize_component(route, tool, _label_manager, self.MINIMAL_SPEC)

        actual = await new_tool.run({"owner": "test", "repo": "test"})
        assert actual.structured_content == {"result": [{"id": 1}, {"id": 2}]}

    @pytest.mark.asyncio
    async def test_object_response_wrapped_by_openapi_tool_via_x_fastmcp(self):
        """When component.output_schema has x-fastmcp-wrap-result, OpenAPITool.run()
        wraps ALL responses in {'result': ...}. The ToolResult flows through
        transform_fn → TransformedTool.run() unchanged."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.tools.customize import customize_component

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        # Mimics enriched spec schema.
        tool.output_schema = {"type": "object", "properties": {"result": {"type": "object", "properties": {"id": {"type": "integer"}}}}}
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        # After customize_component sets x-fastmcp-wrap-result on component,
        # OpenAPITool.run() would wrap the response. Simulate that.
        tool.run = AsyncMock(return_value=ToolResult(structured_content={"result": {"id": 1}}))

        new_tool = customize_component(route, tool, _label_manager, self.MINIMAL_SPEC)

        actual = await new_tool.run({"owner": "test", "repo": "test"})
        assert actual.structured_content == {"result": {"id": 1}}

    @pytest.mark.asyncio
    async def test_array_wrapped_by_openapi_tool_even_without_x_fastmcp(self):
        """OpenAPITool.run() wraps arrays in {'result': [...]} even without
        x-fastmcp-wrap-result (for MCP protocol compliance)."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.tools.customize import customize_component

        route = self._make_route("/repos/{owner}/{repo}/issues/{index}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_get_issue"
        tool.annotations = ToolAnnotations()
        tool.tags = {"issue"}
        tool.parameters = {"properties": {}}
        tool.output_schema = {"type": "object", "properties": {"result": {"type": "array"}}}
        tool.description = ""
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}
        # OpenAPITool.run() wraps non-dict in {"result": ...}
        tool.run = AsyncMock(return_value=ToolResult(structured_content={"result": [{"id": 1}]}))

        new_tool = customize_component(route, tool, _label_manager, self.MINIMAL_SPEC)

        actual = await new_tool.run({"owner": "test", "repo": "test"})
        assert actual.structured_content == {"result": [{"id": 1}]}


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

    def test_text_plain_customize_component_no_output_schema(self):
        """customize_component should not set output_schema for text/plain tools."""
        from fastmcp.server.providers.openapi import OpenAPITool
        from fastmcp.tools.tool import ToolAnnotations

        from gitea_mcp_server.tools.customize import customize_component

        route = self._make_route("/repos/{owner}/{repo}/pulls/{index}.{diffType}", "GET")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_download_pull_diff_or_patch"
        tool.annotations = ToolAnnotations()
        tool.tags = {"repository"}
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Get a pull request diff or patch"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        new_tool = customize_component(route, tool, _label_manager, self.TEXT_SPEC)
        assert new_tool is not None
        # output_schema should be None for text/plain endpoints
        assert new_tool.output_schema is None, (
            f"Expected None, got {new_tool.output_schema}"
        )


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
