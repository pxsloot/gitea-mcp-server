"""Unit tests for OpenAPI converter - response conversion."""

from gitea_mcp_server.openapi_converter import (
    _determine_content_type,
    convert_responses,
)


class TestDetermineContentType:
    """Tests for _determine_content_type helper."""

    def test_defaults_to_json_when_no_produces(self):
        assert _determine_content_type(None) == "application/json"

    def test_defaults_to_json_when_empty_produces(self):
        assert _determine_content_type([]) == "application/json"

    def test_returns_json_when_only_json(self):
        assert _determine_content_type(["application/json"]) == "application/json"

    def test_returns_text_plain_when_produces_text(self):
        assert _determine_content_type(["text/plain"]) == "text/plain"

    def test_returns_text_plain_precedes_json(self):
        assert _determine_content_type(["text/plain", "application/json"]) == "text/plain"

    def test_returns_first_non_json(self):
        assert _determine_content_type(["application/xml", "application/json"]) == "application/xml"

    def test_case_insensitive(self):
        assert _determine_content_type(["TEXT/PLAIN"]) == "text/plain"

    def test_handles_whitespace(self):
        assert _determine_content_type([" text/plain "]) == "text/plain"


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

    def test_text_plain_response_with_produces(self):
        """When produces=['text/plain'], response should use text/plain content type."""
        responses = {
            "200": {
                "description": "Diff output",
                "schema": {"type": "string"},
            }
        }
        result = convert_responses(responses, produces=["text/plain"])
        assert "200" in result
        assert "content" in result["200"]
        assert "text/plain" in result["200"]["content"]
        assert "application/json" not in result["200"]["content"]
        schema = result["200"]["content"]["text/plain"]["schema"]
        assert schema["type"] == "string"

    def test_defaults_to_json_without_produces(self):
        """Without produces, even string responses get application/json."""
        responses = {
            "200": {
                "description": "Some string",
                "schema": {"type": "string"},
            }
        }
        result = convert_responses(responses)
        assert "application/json" in result["200"]["content"]
        assert "text/plain" not in result["200"]["content"]
