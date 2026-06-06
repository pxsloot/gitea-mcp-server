"""Unit tests for OpenAPI converter - path conversion."""

from gitea_mcp_server.openapi_converter import convert_paths


class TestConvertPaths:
    """Tests for the convert_paths function."""

    def test_simple_get(self):
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

    def test_post_with_body(self):
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

    def test_post_with_formData(self):
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

    def test_mixed_parameters(self):
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
