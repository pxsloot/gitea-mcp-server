"""Unit tests for OpenAPI converter - parameter conversion."""

from gitea_mcp_server.openapi_converter import convert_parameters


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
