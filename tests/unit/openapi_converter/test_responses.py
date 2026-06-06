"""Unit tests for OpenAPI converter - response conversion."""

from gitea_mcp_server.openapi_converter import (
    _determine_content_type,
    convert_responses,
)


class TestDetermineContentType:
    """Tests for _determine_content_type helper."""

    def test_defaults_to_json_when_no_produces(self):
        """Should default to application/json when no produces specified."""
        assert _determine_content_type(None) == "application/json"

    def test_defaults_to_json_when_empty_produces(self):
        """Should default to application/json when produces is empty."""
        assert _determine_content_type([]) == "application/json"

    def test_returns_json_when_only_json(self):
        """Should return application/json when that is the only produces type."""
        assert _determine_content_type(["application/json"]) == "application/json"

    def test_returns_text_plain_when_produces_text(self):
        """Should return text/plain when produces contains it."""
        assert _determine_content_type(["text/plain"]) == "text/plain"

    def test_returns_text_plain_precedes_json(self):
        """text/plain should be preferred over application/json when both present."""
        assert _determine_content_type(["text/plain", "application/json"]) == "text/plain"

    def test_returns_first_non_json(self):
        """Should return the first non-json content type when available."""
        assert _determine_content_type(["application/xml", "application/json"]) == "application/xml"

    def test_case_insensitive(self):
        """Content type matching should be case-insensitive."""
        assert _determine_content_type(["TEXT/PLAIN"]) == "text/plain"

    def test_handles_whitespace(self):
        """Content types with surrounding whitespace should be handled."""
        assert _determine_content_type([" text/plain "]) == "text/plain"


class TestConvertResponses:
    """Tests for the convert_responses function."""

    def test_simple_response(self):
        """Response with schema should be converted with content type.application/json."""
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
        """Responses without schema (e.g. 204) should have no content key."""
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

    def test_response_with_headers(self):
        """Response headers should be converted."""
        responses = {
            "200": {
                "description": "OK",
                "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
                "headers": {
                    "X-RateLimit-Remaining": {"type": "integer", "description": "Remaining calls"},
                    "X-RateLimit-Reset": {"type": "integer"},
                },
            }
        }
        result = convert_responses(responses)
        assert "headers" in result["200"]
        headers = result["200"]["headers"]
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers
        # Header schema should be properly normalized
        assert "schema" in headers["X-RateLimit-Remaining"]
        assert headers["X-RateLimit-Remaining"]["schema"]["type"] == "integer"
        assert headers["X-RateLimit-Remaining"]["description"] == "Remaining calls"

    def test_response_with_non_dict_headers(self):
        """Non-dict headers should be preserved as-is."""
        responses = {
            "200": {
                "description": "OK",
                "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
                "headers": {
                    "X-Custom": "just a string",
                },
            }
        }
        result = convert_responses(responses)
        assert "headers" in result["200"]
        assert result["200"]["headers"]["X-Custom"] == "just a string"

    def test_non_dict_response_preserved(self):
        """Non-dict responses should be preserved as-is."""
        responses = {
            "200": "just a string",
        }
        result = convert_responses(responses)
        assert result["200"] == "just a string"
