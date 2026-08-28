"""Tests for type_info module (build_type_index, resolve_type_info)."""

from typing import cast

import pytest
from fastmcp.tools.base import Tool

from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.tools.type_info import (
    _walk_parameter_refs,
    _walk_request_body_refs,
    _walk_response_refs,
    build_type_index,
    resolve_type_info,
)
from tests.helpers.spec_fixtures import make_openapi_spec


class TestBuildTypeIndex:
    """Tests for build_type_index."""

    def test_empty_spec_returns_empty(self) -> None:
        """Should return empty dict when spec has no components/schemas."""
        spec: OpenAPISpec = {"openapi": "3.1.0", "paths": {}}
        assert build_type_index(spec) == {}

    def test_registers_all_types(self) -> None:
        """Should register every type from components/schemas."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "login": {"type": "string"}},
                    },
                    "Label": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                    },
                },
            },
        }
        index = build_type_index(spec)
        assert set(index.keys()) == {"User", "Label"}
        assert index["User"]["referenced_types"] == []
        assert index["Label"]["returned_by"] == []

    def test_detects_nested_refs(self) -> None:
        """Should detect $ref references between types."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "assignee": {"$ref": "#/components/schemas/User"},
                        },
                    },
                    "Label": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        }
        index = build_type_index(spec)
        assert "User" in index
        assert "User" in index["User"]["referenced_types"]

    def test_cross_references_from_response(self) -> None:
        """Should record which tools return a type in their response."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {
                "/issues/{id}": {
                    "get": {
                        "operationId": "issue_get_issue",
                        "responses": {
                            "200": {
                                "description": "Issue",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "assignee": {"$ref": "#/components/schemas/User"},
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
                        "properties": {"id": {"type": "integer"}},
                    },
                },
            },
        }
        index = build_type_index(spec)
        assert "User" in index
        assert "issue_get_issue" in index["User"]["returned_by"]

    def test_cross_references_from_parameters(self) -> None:
        """Should record which tools accept a type in their parameters."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "admin_create_user",
                        "parameters": [
                            {
                                "name": "body",
                                "in": "body",
                                "schema": {"$ref": "#/components/schemas/CreateUserOption"},
                            },
                        ],
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
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
                    "CreateUserOption": {
                        "type": "object",
                        "properties": {"username": {"type": "string"}},
                    },
                },
            },
        }
        index = build_type_index(spec)
        assert "CreateUserOption" in index
        assert "admin_create_user" in index["CreateUserOption"]["accepted_by"]

    def test_cross_references_from_request_body(self) -> None:
        """Should record which tools accept a type via requestBody."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {
                "/repos": {
                    "post": {
                        "operationId": "repo_create",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CreateRepoOption"},
                                },
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
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
                    "CreateRepoOption": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        }
        index = build_type_index(spec)
        assert "repo_create" in index["CreateRepoOption"]["accepted_by"]

    def test_deduplicates_cross_references(self) -> None:
        """Should deduplicate operationId entries in returned_by/accepted_by."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {
                "/issues/{id}": {
                    "get": {
                        "operationId": "issue_get_issue",
                        "responses": {
                            "200": {
                                "description": "Issue",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "user": {"$ref": "#/components/schemas/User"},
                                                "assignee": {"$ref": "#/components/schemas/User"},
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
                        "properties": {"id": {"type": "integer"}},
                    },
                },
            },
        }
        index = build_type_index(spec)
        # Even though User appears twice in the response, the operation
        # should only appear once in returned_by.
        assert index["User"]["returned_by"] == ["issue_get_issue"]

    def test_non_dict_schema_skipped(self) -> None:
        """Should skip non-dict schema entries."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {},
            "components": {
                "schemas": {
                    "User": {"type": "object", "properties": {}},
                    "BadType": "not a dict",  # Should be skipped gracefully
                },
            },
        }
        index = build_type_index(spec)
        assert "User" in index
        assert "BadType" not in index


class TestResolveTypeInfo:
    """Tests for resolve_type_info."""

    SIMPLE_SPEC: OpenAPISpec = {
        "openapi": "3.1.0",
        "paths": {
            "/issues/{id}": {
                "get": {
                    "operationId": "issue_get_issue",
                    "responses": {
                        "200": {
                            "description": "Issue",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "assignee": {"$ref": "#/components/schemas/User"},
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
                    "description": "User represents a user",
                    "properties": {
                        "id": {"type": "integer"},
                        "login": {"type": "string"},
                    },
                },
                "Milestone": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "state": {"type": "string"},
                    },
                },
            },
        },
    }

    def test_resolves_known_type_concise(self) -> None:
        """Should return compact type info for a known type."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "User", detail="concise")

        assert result is not None
        assert result["name"] == "User"
        assert result["description"] == "User represents a user"
        assert "schema" in result
        assert "id" in result["schema"]
        assert "login" in result["schema"]
        assert "cross_references" in result
        assert "returned_by" in result["cross_references"]
        assert "accepted_by" in result["cross_references"]
        assert "referenced_types" in result["cross_references"]
        # concise should NOT have resolved_schema
        assert "resolved_schema" not in result

    def test_resolves_known_type_full(self) -> None:
        """Should include resolved_schema when detail='full'."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "User", detail="full")

        assert result is not None
        assert result["name"] == "User"
        assert "resolved_schema" in result
        assert isinstance(result["resolved_schema"], dict)
        # resolved_schema should have type, properties etc.
        assert "type" in result["resolved_schema"]

    def test_returns_none_for_unknown_type(self) -> None:
        """Should return None for a type not in the index."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "NonExistentType")
        assert result is None

    def test_cross_references_include_returned_by(self) -> None:
        """Should include operationIds of tools that return this type."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "User")
        assert result is not None
        assert "issue_get_issue" in result["cross_references"]["returned_by"]

    def test_no_returned_by_for_unused_type(self) -> None:
        """Should have empty returned_by for an unused type."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "Milestone")
        assert result is not None
        assert result["cross_references"]["returned_by"] == []
        assert result["cross_references"]["accepted_by"] == []

    def test_scalar_type_schema_is_primitive(self) -> None:
        """A scalar type (string enum) yields a primitive ``schema``.

        ``resolve_type``'s declared output schema must accept primitives for
        the ``schema`` field — ``schema_to_compact_example`` returns a bare
        string for enum types (e.g. ``ReviewStateType``), and a bare
        ``{"type": "object"}`` declaration would fail output validation.
        """
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "ReviewStateType": {
                        "type": "string",
                        "enum": ["APPROVED", "REQUEST_CHANGES", "COMMENT"],
                    },
                },
            },
        )
        index = build_type_index(spec)
        result = resolve_type_info(spec, index, "ReviewStateType", detail="concise")
        assert result is not None
        assert isinstance(result["schema"], str), (
            f"scalar type schema should be a primitive string, got {result['schema']!r}"
        )


class TestResolveTypeInfoEdgeCases:
    """Tests for guard clauses and error handling in resolve_type_info."""

    def test_non_dict_schema_returns_none(self) -> None:
        """Non-dict schema in type index returns None."""
        index = {
            "TestType": {
                "schema": "not a dict",
                "returned_by": [],
                "accepted_by": [],
                "referenced_types": [],
            }
        }
        result = resolve_type_info({"openapi": "3.1.0"}, index, "TestType")
        assert result is None


class TestWalkResponseRefs:
    """Guard clauses in _walk_response_refs."""

    MINIMAL_SPEC: OpenAPISpec = {"openapi": "3.1.0", "components": {"schemas": {}}}

    def test_non_dict_responses_early_return(self) -> None:
        """Non-dict responses triggers early return."""
        type_index: dict = {}
        _walk_response_refs(self.MINIMAL_SPEC, "not a dict", "op1", type_index)
        assert type_index == {}

    def test_ref_in_response_is_resolved(self) -> None:
        """$ref in response is resolved before content access."""
        type_index = {
            "User": {
                "schema": {"type": "object"},
                "referenced_types": [],
                "returned_by": [],
                "accepted_by": [],
            },
        }
        spec = make_openapi_spec(
            components={
                "responses": {
                    "UserResponse": {
                        "description": "User data",
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
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    },
                },
            },
        )
        _walk_response_refs(
            spec,
            {
                "200": {
                    "$ref": "#/components/responses/UserResponse",
                },
            },
            "op1",
            type_index,
        )
        # The ref resolves to a response containing a $ref to User,
        # so User should be marked as returned_by op1
        assert "op1" in type_index["User"]["returned_by"]

    def test_non_dict_content_skipped(self) -> None:
        """Response with non-dict content is skipped."""
        type_index: dict = {}
        _walk_response_refs(
            self.MINIMAL_SPEC,
            {
                "200": {
                    "description": "OK",
                    "content": "not a dict",
                },
            },
            "op1",
            type_index,
        )
        assert type_index == {}

    def test_non_dict_json_content_skipped(self) -> None:
        """Response with non-dict JSON content is skipped."""
        type_index: dict = {}
        _walk_response_refs(
            self.MINIMAL_SPEC,
            {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": "not a dict",
                    },
                },
            },
            "op1",
            type_index,
        )
        assert type_index == {}


class TestWalkParameterRefs:
    """Guard clauses in _walk_parameter_refs."""

    def test_non_list_parameters_early_return(self) -> None:
        """Non-list parameters triggers early return."""
        type_index: dict = {}
        _walk_parameter_refs("not a list", "op1", type_index)
        assert type_index == {}

    def test_non_dict_param_skipped(self) -> None:
        """Non-dict parameter in list is skipped gracefully."""
        type_index: dict = {}
        _walk_parameter_refs(["valid", 42, {"schema": {"type": "string"}}], "op1", type_index)
        # Should not raise; 42 is skipped
        assert type_index == {}


class TestWalkRequestBodyRefs:
    """Guard clauses in _walk_request_body_refs."""

    MINIMAL_SPEC: OpenAPISpec = {"openapi": "3.1.0"}

    def test_non_dict_body_content_early_return(self) -> None:
        """Non-dict body content triggers early return."""
        type_index: dict = {}
        _walk_request_body_refs({"content": "not a dict"}, "op1", type_index)
        assert type_index == {}

    def test_non_dict_media_item_skipped(self) -> None:
        """Non-dict media item in request body is skipped."""
        type_index: dict = {}
        _walk_request_body_refs(
            {
                "content": {
                    "application/json": "not a dict",
                },
            },
            "op1",
            type_index,
        )
        assert type_index == {}


class TestBuildTypeIndexEdgeCases:
    """Additional guard clauses in build_type_index."""

    def test_non_dict_schemas_returns_empty(self) -> None:
        """When components.schemas is not a dict, return empty."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "components": {"schemas": "not a dict"},
            "paths": {},
        }
        result = build_type_index(spec)
        assert result == {}

    def test_non_dict_path_item_skipped(self) -> None:
        """Non-dict path item is skipped."""
        spec = cast(
            "OpenAPISpec",
            {
                "openapi": "3.1.0",
                "paths": {
                    "/valid": {
                        "get": {
                            "operationId": "get_valid",
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                    "/invalid": "not a dict",
                },
                "components": {"schemas": {}},
            },
        )
        result = build_type_index(spec)
        # Only the valid path should be processed; no error for the invalid one
        assert isinstance(result, dict)

    def test_empty_operation_id_skipped(self) -> None:
        """Operation without operationId is skipped."""
        spec: OpenAPISpec = {
            "openapi": "3.1.0",
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "",  # empty — should be skipped
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            },
            "components": {"schemas": {}},
        }
        result = build_type_index(spec)
        assert result == {}


class TestResolveTypeOutputSchema:
    """Tests for the registered ``resolve_type`` tool's output schema.

    The ``schema`` field of the result is a compact example that can be a
    primitive for scalar types (e.g. ``ReviewStateType`` → a bare string), so
    the declared schema must accept primitives — otherwise ``resolve_type``
    fails output validation on every scalar type, breaking agent discovery.
    """

    SPEC: OpenAPISpec = {
        "openapi": "3.1.0",
        "paths": {},
        "components": {
            "schemas": {
                "ReviewStateType": {
                    "type": "string",
                    "enum": ["APPROVED", "REQUEST_CHANGES", "COMMENT"],
                },
            },
        },
    }

    @pytest.mark.asyncio
    async def _get_resolve_type(self) -> Tool:
        """Helper: register type tools and return the resolve_type tool."""
        from fastmcp import FastMCP

        from gitea_mcp_server.tools.type_info import register_type_tools

        mcp = FastMCP("test")
        register_type_tools(mcp, openapi_spec=self.SPEC, tool_prefix="gitea_")
        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        result = tool_map.get("resolve_type")
        assert result is not None
        return result

    @pytest.mark.asyncio
    async def test_schema_field_accepts_primitives(self) -> None:
        """resolve_type's ``schema`` field must accept primitives (scalar types).

        ``schema_to_compact_example`` yields a bare string for enum types, so
        a bare ``{"type": "object"}`` declaration would fail output validation
        — the same bug family as ``tool_info``'s ``output_example``.
        """
        tool = await self._get_resolve_type()
        assert tool is not None, "resolve_type not registered"
        assert tool.output_schema is not None, "Expected output_schema to be set"
        result_schema = tool.output_schema["properties"]["result"]
        schema_field = result_schema.get("properties", {}).get("schema", {})
        assert schema_field, "schema field missing from resolve_type.result.properties"
        assert "anyOf" in schema_field, "schema field should use anyOf"
        types = {entry.get("type") for entry in schema_field["anyOf"]}
        assert "object" in types, f"anyOf should accept objects, got {types}"
        assert "string" in types, f"anyOf should accept strings, got {types}"
        assert "boolean" in types, f"anyOf should accept booleans, got {types}"
        assert "number" in types, f"anyOf should accept numbers, got {types}"
        assert "null" in types, f"anyOf should accept null, got {types}"
