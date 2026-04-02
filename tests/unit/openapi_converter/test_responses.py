"""Unit tests for OpenAPI converter - response conversion."""

from gitea_mcp_server.openapi_converter import convert_responses


class TestConvertResponses:
    """Tests for the convert_responses function."""

    def test_simple_response(self):
        responses = {
            "200": {
                "description": "Success",
                "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
            }
        }
        result = convert_responses(responses)
        assert "200" in result
        assert "content" in result["200"]
        assert "application/json" in result["200"]["content"]
        schema = result["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"

    def test_response_without_schema(self):
        responses = {"204": {"description": "No Content"}}
        result = convert_responses(responses)
        assert "204" in result
        assert "content" not in result["204"]
