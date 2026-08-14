"""Unit tests for OpenAPI converter - path conversion."""

from gitea_mcp_server.openapi_converter import convert_paths
from gitea_mcp_server.openapi_converter.core import RequestBodyBuilder


class TestRequestBodyBuilder:
    """Tests for RequestBodyBuilder class."""

    def test_form_data_with_description_no_schema(self) -> None:
        """FormData param with description and no schema field uses description."""
        builder = RequestBodyBuilder()
        form_params = [
            {
                "name": "file",
                "in": "formData",
                "type": "string",
                "description": "The file to upload",
            },
        ]
        result = builder.build_from_form_data(form_params)
        assert result is not None
        schema = result["content"]["multipart/form-data"]["schema"]
        assert schema["properties"]["file"]["description"] == "The file to upload"
        assert schema["properties"]["file"]["type"] == "string"


class TestConvertPaths:
    """Tests for the convert_paths function."""

    def test_simple_get(self) -> None:
        """Simple GET path with no parameters should be preserved."""
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

    def test_post_with_body(self) -> None:
        """POST with body parameter should produce requestBody."""
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

    def test_post_with_formData(self) -> None:
        """POST with formData parameters should produce multipart requestBody."""
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

    def test_mixed_parameters(self) -> None:
        """Test POST with both query parameters and body parameters."""
        paths = {
            "/search": {
                "post": {
                    "parameters": [
                        {"name": "q", "in": "query", "type": "string"},
                        {"name": "body", "in": "body", "schema": {"type": "object"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/search"]["post"]
        # Should have query parameter
        assert any(p["name"] == "q" for p in op["parameters"])
        # Should have requestBody from the body parameter
        assert "requestBody" in op

    def test_delete_with_body_params_has_request_body(self) -> None:
        """DELETE operations with ``in: body`` or ``in: formData`` parameters must
        produce requestBody.

        Gitea's Swagger declares request bodies on some DELETE endpoints
        (e.g. DELETE /repos/{owner}/{repo}/issues/{index}/blocks carries
        an IssueMeta body).  The converter builds requestBody whenever
        raw parameters declare in: body or in: formData, regardless of
        HTTP method.
        """
        paths = {
            "/items/{id}": {
                "delete": {
                    "parameters": [
                        {"name": "id", "in": "path", "type": "integer", "required": True},
                        {"name": "body", "in": "body", "schema": {"type": "object"}},
                        {"name": "file", "in": "formData", "type": "string"},
                    ],
                    "responses": {"204": {"description": "No Content"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/items/{id}"]["delete"]
        # path params are preserved
        assert any(p["name"] == "id" for p in op["parameters"])
        # body and formData params are filtered out of parameters
        # (they become requestBody, handled by convert_parameters)
        assert not any(p["name"] == "body" for p in op["parameters"])
        assert not any(p["name"] == "file" for p in op["parameters"])
        # Request body is built from in:body / in:formData params
        assert "requestBody" in op
        rb = op["requestBody"]
        assert "content" in rb

    def test_delete_without_body_params_has_no_request_body(self) -> None:
        """DELETE without ``in: body`` or ``in: formData`` must not produce
        a spurious requestBody.

        Complements ``test_delete_with_body_params_has_request_body``:
        the converter gates on data presence, not method, so a DELETE
        that carries no body parameters must behave exactly like any
        other body-less operation — no requestBody emitted.
        """
        paths = {
            "/items/{id}": {
                "delete": {
                    "parameters": [
                        {"name": "id", "in": "path", "type": "integer", "required": True},
                    ],
                    "responses": {"204": {"description": "No Content"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/items/{id}"]["delete"]
        assert any(p["name"] == "id" for p in op["parameters"])
        assert "requestBody" not in op, (
            f"DELETE without body params must not have requestBody. Keys: {list(op.keys())}"
        )

    def test_delete_with_formdata_produces_request_body(self) -> None:
        """DELETE with only ``in: formData`` must produce multipart requestBody.

        The converter builds requestBody whenever any raw parameter declares
        ``in: body`` or ``in: formData``.  This test isolates the formData
        path to verify it works independently on DELETE (the existing
        ``test_delete_with_body_params_has_request_body`` tests both together).
        """
        paths = {
            "/items/{id}/attachments": {
                "delete": {
                    "parameters": [
                        {"name": "id", "in": "path", "type": "integer", "required": True},
                        {
                            "name": "attachment_ids",
                            "in": "formData",
                            "type": "array",
                            "items": {"type": "integer"},
                            "required": True,
                        },
                    ],
                    "responses": {"204": {"description": "No Content"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/items/{id}/attachments"]["delete"]
        assert any(p["name"] == "id" for p in op["parameters"])
        # formData params are filtered out of parameters
        assert not any(p["name"] == "attachment_ids" for p in op["parameters"])
        # requestBody is built from formData params
        assert "requestBody" in op
        rb = op["requestBody"]
        assert "multipart/form-data" in rb["content"]
        schema = rb["content"]["multipart/form-data"]["schema"]
        assert "attachment_ids" in schema["properties"]
        assert schema["type"] == "object"

    def test_path_level_parameters_converted(self) -> None:
        """Path-level parameters should be converted and preserved."""
        paths = {
            "/users": {
                "parameters": [
                    {
                        "name": "X-Request-Id",
                        "in": "header",
                        "type": "string",
                        "description": "Request ID",
                    },
                ],
                "get": {
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
        result = convert_paths(paths)
        assert "parameters" in result["/users"]
        param_names = [p["name"] for p in result["/users"]["parameters"]]
        assert "X-Request-Id" in param_names
        assert any(
            p.get("schema", {}).get("type") == "string" for p in result["/users"]["parameters"]
        )

    def test_non_http_method_key_preserved(self) -> None:
        """Non-HTTP-method keys in path_item are preserved (e.g., summary, description)."""
        paths = {
            "/pets": {
                "summary": "List all pets",
                "description": "Pet operations",
                "get": {
                    "operationId": "listPets",
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
        result = convert_paths(paths)
        path_item = result["/pets"]
        assert "summary" in path_item
        assert path_item["summary"] == "List all pets"
        assert "description" in path_item

    def test_non_dict_operation_skipped(self) -> None:
        """Non-dict operation values are skipped without error."""
        paths = {
            "/health": {
                "get": "not a dict",
                "responses": {"200": {"description": "OK"}},
            }
        }
        result = convert_paths(paths)
        # The operation should remain as-is (not processed since it's not a dict)
        assert "/health" in result
        assert result["/health"]["get"] == "not a dict"

    def test_empty_operation_id_returns_unchanged(self) -> None:
        """Operation without operationId gets one generated."""
        paths = {
            "/items": {
                "get": {
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        op = result["/items"]["get"]
        assert "operationId" in op
        assert op["operationId"] == "get_items"

    def test_dedup_counter_increments(self) -> None:
        """Three operations with the same operationId get _0, _1, _2 suffixes."""
        paths = {
            "/items": {
                "get": {
                    "operationId": "getItems",
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/things": {
                "get": {
                    "operationId": "get_items",
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/gizmos": {
                "get": {
                    "operationId": "get_items",
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
        result = convert_paths(paths)
        # First occurrence gets normalized name (no suffix needed)
        assert result["/items"]["get"]["operationId"] == "get_items"
        # Second occurrence gets _1 suffix
        assert result["/things"]["get"]["operationId"] == "get_items_1"
        # Third occurrence gets _2 suffix
        assert result["/gizmos"]["get"]["operationId"] == "get_items_2"
