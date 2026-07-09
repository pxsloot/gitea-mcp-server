"""Unit tests for OpenAPI converter - parameter conversion."""

from gitea_mcp_server.openapi_converter import _convert_components, convert_parameters


class TestConvertParameters:
    """Tests for the convert_parameters function."""

    def test_simple_parameter(self):
        """Query parameter with type should be wrapped in schema."""
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
        """Body parameters should be removed during parameter conversion."""
        params = [{"name": "body", "in": "body", "schema": {"type": "object"}}]
        result = convert_parameters(params)
        assert len(result) == 0  # Body params are skipped

    def test_formData_parameter(self):
        """formData parameters should be removed; non-formData params preserved."""
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
        """Parameter with existing schema should preserve and normalize it."""
        params = [{"name": "user", "in": "query", "schema": {"type": "string", "minLength": 1}}]
        result = convert_parameters(params)
        # Schema should be preserved and normalized
        assert "schema" in result[0]
        assert result[0]["schema"]["type"] == "string"
        assert result[0]["schema"]["minLength"] == 1
        # Top-level should not have type/minLength directly
        assert "type" not in result[0]
        assert "minLength" not in result[0]

    def test_collection_format_removed(self):
        """collectionFormat should be stripped from converted parameters."""
        params = [{"name": "ids", "in": "query", "type": "array", "items": {"type": "integer"}, "collectionFormat": "csv"}]
        result = convert_parameters(params)
        assert "collectionFormat" not in result[0]
        assert result[0]["name"] == "ids"
        assert result[0]["schema"]["type"] == "array"

    def test_parameter_without_schema_fields(self):
        """Parameter without schema or type fields should get empty schema."""
        params = [{"name": "empty", "in": "query"}]
        result = convert_parameters(params)
        assert result[0]["name"] == "empty"
        assert "schema" not in result[0]


class TestConvertComponents:
    """Tests for _convert_components function."""

    def test_parameters_as_dict(self):
        """Parameters as a dict should be converted to named entries."""
        spec = {
            "parameters": {
                "page": {"name": "page", "in": "query", "type": "integer"},
            }
        }
        result = _convert_components(spec)
        assert "parameters" in result
        assert "page" in result["parameters"]
        assert result["parameters"]["page"]["name"] == "page"
        assert result["parameters"]["page"]["in"] == "query"

    def test_parameters_as_list(self):
        """Parameters as a list should also be converted."""
        spec = {
            "parameters": [
                {"name": "page", "in": "query", "type": "integer"},
                {"name": "limit", "in": "query", "type": "integer"},
            ]
        }
        result = _convert_components(spec)
        assert "parameters" in result
        assert "page" in result["parameters"]
        assert "limit" in result["parameters"]

    def test_parameters_neither_dict_nor_list(self):
        """Parameters as neither dict nor list should be skipped."""
        spec = {
            "parameters": "just a string",
        }
        result = _convert_components(spec)
        assert "parameters" not in result

    def test_empty_parameters_skipped(self):
        """Empty parameters list should be skipped."""
        spec = {
            "parameters": {},
        }
        result = _convert_components(spec)
        assert "parameters" not in result

    def test_security_definitions_converted(self):
        """securityDefinitions should be converted to securitySchemes."""
        spec = {
            "securityDefinitions": {
                "basicAuth": {"type": "basic"},
            }
        }
        result = _convert_components(spec)
        assert "securitySchemes" in result
        assert "basicAuth" in result["securitySchemes"]


class TestVendorExtensionStripping:
    """Tests for vendor extension (x-*) stripping in parameter conversion."""

    def test_parameter_level_x_fields_stripped(self):
        """convert_parameters should strip x-* keys from parameter objects."""
        params = [
            {
                "name": "owner",
                "in": "path",
                "type": "string",
                "required": True,
                "x-go-name": "Owner",
                "description": "owner of the repo",
            }
        ]
        result = convert_parameters(params)
        assert len(result) == 1
        assert "x-go-name" not in result[0]
        # Non-x fields preserved
        assert result[0]["name"] == "owner"
        assert result[0]["description"] == "owner of the repo"
        assert "schema" in result[0]

    def test_parameter_schema_x_fields_stripped(self):
        """convert_parameters should strip x-* keys from parameter schema sub-object."""
        params = [
            {
                "name": "filter",
                "in": "query",
                "schema": {"type": "string", "x-go-name": "Filter"},
            }
        ]
        result = convert_parameters(params)
        assert len(result) == 1
        assert "x-go-name" not in result[0]["schema"]
        assert result[0]["schema"]["type"] == "string"

    def test_parameter_schema_fields_from_type_stripped(self):
        """convert_parameters should strip x-* when schema is built from type fields."""
        params = [
            {
                "name": "page",
                "in": "query",
                "type": "integer",
                "x-go-name": "Page",
                "description": "Page number",
            }
        ]
        result = convert_parameters(params)
        assert len(result) == 1
        assert "x-go-name" not in result[0]
        # Schema should still be built correctly
        assert result[0]["schema"]["type"] == "integer"
        # Description stays at top level
        assert result[0]["description"] == "Page number"
