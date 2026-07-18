"""Tests for type_info module (build_type_index, resolve_type_info)."""

from gitea_mcp_server.tools.type_info import build_type_index, resolve_type_info


class TestBuildTypeIndex:
    """Tests for build_type_index."""

    def test_empty_spec_returns_empty(self):
        """Should return empty dict when spec has no components/schemas."""
        spec: dict = {"openapi": "3.1.0", "paths": {}}
        assert build_type_index(spec) == {}

    def test_registers_all_types(self):
        """Should register every type from components/schemas."""
        spec: dict = {
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

    def test_detects_nested_refs(self):
        """Should detect $ref references between types."""
        spec: dict = {
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

    def test_cross_references_from_response(self):
        """Should record which tools return a type in their response."""
        spec: dict = {
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

    def test_cross_references_from_parameters(self):
        """Should record which tools accept a type in their parameters."""
        spec: dict = {
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
                                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
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

    def test_cross_references_from_request_body(self):
        """Should record which tools accept a type via requestBody."""
        spec: dict = {
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
                                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
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

    def test_deduplicates_cross_references(self):
        """Should deduplicate operationId entries in returned_by/accepted_by."""
        spec: dict = {
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

    def test_non_dict_schema_skipped(self):
        """Should skip non-dict schema entries."""
        spec: dict = {
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

    SIMPLE_SPEC: dict = {
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

    def test_resolves_known_type_concise(self):
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

    def test_resolves_known_type_full(self):
        """Should include resolved_schema when detail='full'."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "User", detail="full")

        assert result is not None
        assert result["name"] == "User"
        assert "resolved_schema" in result
        assert isinstance(result["resolved_schema"], dict)
        # resolved_schema should have type, properties etc.
        assert "type" in result["resolved_schema"]

    def test_returns_none_for_unknown_type(self):
        """Should return None for a type not in the index."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "NonExistentType")
        assert result is None

    def test_cross_references_include_returned_by(self):
        """Should include operationIds of tools that return this type."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "User")
        assert result is not None
        assert "issue_get_issue" in result["cross_references"]["returned_by"]

    def test_no_returned_by_for_unused_type(self):
        """Should have empty returned_by for an unused type."""
        index = build_type_index(self.SIMPLE_SPEC)
        result = resolve_type_info(self.SIMPLE_SPEC, index, "Milestone")
        assert result is not None
        assert result["cross_references"]["returned_by"] == []
        assert result["cross_references"]["accepted_by"] == []
