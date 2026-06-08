"""Unit tests for OpenAPI converter - email and date format handling."""

from gitea_mcp_server.openapi_converter import (
    _add_nullable_for_optional_refs,
    convert_swagger_to_openapi_v3,
)


class TestEmailFormatHandling:
    """Tests for email format preservation with empty string and null support."""

    def test_email_field_with_format_becomes_anyof(self):
        """Test that format:email fields are converted to anyOf preserving format."""
        schema = {
            "type": "object",
            "properties": {
                "email": {"type": "string", "format": "email", "description": "User email address"}
            },
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        # The schema is mutated in place
        email_schema = schema["properties"]["email"]
        assert "anyOf" in email_schema
        any_of = email_schema["anyOf"]
        # Optional field: should have email branch, empty string branch, and null branch
        assert len(any_of) == 3
        # Find email branch
        email_branch = next((b for b in any_of if b.get("format") == "email"), None)
        assert email_branch is not None
        assert email_branch["type"] == "string"
        assert email_branch["format"] == "email"
        # Find empty string branch
        empty_branch = next((b for b in any_of if b.get("maxLength") == 0), None)
        assert empty_branch is not None
        assert empty_branch["type"] == "string"
        # Find null branch
        null_branch = next((b for b in any_of if b.get("type") == "null"), None)
        assert null_branch is not None
        # Description should be preserved at the top level
        assert email_schema.get("description") == "User email address"

    def test_required_email_field_excludes_null_and_empty(self):
        """Test that required email fields do NOT include null or empty string branches."""
        schema = {
            "type": "object",
            "required": ["email"],
            "properties": {"email": {"type": "string", "format": "email"}},
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        email_schema = schema["properties"]["email"]
        # Required field: should remain as simple format:email, NOT anyOf
        # Actually, it becomes anyOf with just the email branch
        assert "anyOf" in email_schema
        any_of = email_schema["anyOf"]
        # Should have exactly 1 branch (the email format branch)
        assert len(any_of) == 1
        branch = any_of[0]
        assert branch["type"] == "string"
        assert branch["format"] == "email"
        # Should NOT have empty string or null branches
        empty_branch = next((b for b in any_of if b.get("maxLength") == 0), None)
        assert empty_branch is None
        null_branch = next((b for b in any_of if b.get("type") == "null"), None)
        assert null_branch is None

    def test_optional_email_field_includes_null(self):
        """Test that optional email fields include null branch."""
        schema = {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}}
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        email_schema = schema["properties"]["email"]
        assert "anyOf" in email_schema
        # Should have email, empty string, and null branches
        assert len(email_schema["anyOf"]) == 3
        null_branch = next((b for b in email_schema["anyOf"] if b.get("type") == "null"), None)
        assert null_branch is not None

    def test_email_preserves_other_constraints(self):
        """Test that other string constraints (minLength, pattern) are preserved on email branch."""
        schema = {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "format": "email",
                    "minLength": 5,
                    "maxLength": 254,
                    "pattern": "^[^@]+@[^@]+\\.[^@]+$",
                }
            },
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        email_schema = schema["properties"]["email"]
        email_branch = next((b for b in email_schema["anyOf"] if b.get("format") == "email"), None)
        assert email_branch is not None
        assert email_branch.get("minLength") == 5
        assert email_branch.get("maxLength") == 254
        assert email_branch.get("pattern") == "^[^@]+@[^@]+\\.[^@]+$"

    def test_email_format_still_valid_openapi_3_1(self):
        """Test that a spec with email anyOf is valid OpenAPI 3.1."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "definitions": {
                "User": {
                    "type": "object",
                    "properties": {"email": {"type": "string", "format": "email"}},
                }
            },
            "paths": {
                "/user": {"get": {"responses": {"200": {"schema": {"$ref": "#/definitions/User"}}}}}
            },
        }
        result = convert_swagger_to_openapi_v3(spec)
        # Should produce valid OpenAPI 3.1
        assert result["openapi"] == "3.1.1"
        user_schema = result["components"]["schemas"]["User"]
        email_schema = user_schema["properties"]["email"]
        assert "anyOf" in email_schema
        # Verify all branches
        any_of = email_schema["anyOf"]
        assert len(any_of) >= 2
        # Email branch with format
        assert any(b.get("format") == "email" for b in any_of)
        # Empty string branch
        assert any(b.get("type") == "string" and b.get("maxLength") == 0 for b in any_of)


class TestDateFormatHandling:
    """Tests that date/date-time formats do NOT get empty string handling.

    Gitea returns null for missing date values, not empty strings.
    Optional date fields should be nullable (type includes null) but should NOT
    use anyOf with an empty string branch.
    """

    def test_optional_date_field_gets_nullable_type_no_anyof(self):
        """Test that optional format:date fields become nullable without anyOf."""
        schema = {
            "type": "object",
            "properties": {
                "date_field": {"type": "string", "format": "date", "description": "Some date"}
            },
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        date_schema = schema["properties"]["date_field"]
        # Should NOT have anyOf
        assert "anyOf" not in date_schema
        # Should have type including null
        assert date_schema.get("type") == ["string", "null"] or date_schema.get("type") == [
            "null",
            "string",
        ]
        # Format should be preserved
        assert date_schema.get("format") == "date"
        # Description should be preserved
        assert date_schema.get("description") == "Some date"

    def test_required_date_field_stays_simple(self):
        """Required date field remains simple string with format, no null addition."""
        schema = {
            "type": "object",
            "required": ["date_field"],
            "properties": {"date_field": {"type": "string", "format": "date"}},
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        date_schema = schema["properties"]["date_field"]
        # Should NOT have anyOf
        assert "anyOf" not in date_schema
        # Should remain simple string with format
        assert date_schema.get("type") == "string"
        assert date_schema.get("format") == "date"

    def test_optional_datetime_field_gets_nullable_type_no_anyof(self):
        """Test that optional format:date-time fields become nullable without anyOf."""
        schema = {
            "type": "object",
            "properties": {"datetime_field": {"type": "string", "format": "date-time"}},
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        dt_schema = schema["properties"]["datetime_field"]
        assert "anyOf" not in dt_schema
        assert dt_schema.get("type") == ["string", "null"] or dt_schema.get("type") == [
            "null",
            "string",
        ]
        assert dt_schema.get("format") == "date-time"

    def test_date_time_integration_validates_openapi_3_1(self):
        """Test that a spec with date fields converts to valid OpenAPI 3.1."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "definitions": {
                "Event": {
                    "type": "object",
                    "properties": {
                        "start_time": {"type": "string", "format": "date-time"},
                        "end_time": {"type": "string", "format": "date-time"},
                        "event_date": {"type": "string", "format": "date"},
                    },
                }
            },
            "paths": {
                "/events": {
                    "get": {"responses": {"200": {"schema": {"$ref": "#/definitions/Event"}}}}
                }
            },
        }
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        event_schema = result["components"]["schemas"]["Event"]
        # Check each date field: optional, should be nullable, no anyOf
        for field_name in ["start_time", "end_time", "event_date"]:
            field_schema = event_schema["properties"][field_name]
            assert "anyOf" not in field_schema, f"{field_name} should not have anyOf"
            assert field_schema.get("type") == ["string", "null"] or field_schema.get("type") == [
                "null",
                "string",
            ], f"{field_name} should be nullable"
            if field_name.startswith("event_"):
                assert field_schema.get("format") == "date"
            else:
                assert field_schema.get("format") == "date-time"


class TestUuidFormatHandling:
    """Tests that uuid format fields are nullable but do NOT get empty string anyOf.

    UUID format is similar to date/date-time: Gitea returns null for missing
    values, not empty strings. Optional uuid fields should get nullable type
    without anyOf.
    """

    def test_optional_uuid_field_gets_nullable_type_no_anyof(self):
        """Optional format:uuid fields should be nullable without anyOf."""
        schema = {
            "type": "object",
            "properties": {
                "token_id": {"type": "string", "format": "uuid", "description": "A UUID token"}
            },
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        uuid_schema = schema["properties"]["token_id"]
        assert "anyOf" not in uuid_schema
        assert uuid_schema.get("type") == ["string", "null"] or uuid_schema.get("type") == [
            "null",
            "string",
        ]
        assert uuid_schema.get("format") == "uuid"
        assert uuid_schema.get("description") == "A UUID token"

    def test_required_uuid_field_stays_simple(self):
        """Required uuid field remains simple string with format."""
        schema = {
            "type": "object",
            "required": ["token_id"],
            "properties": {"token_id": {"type": "string", "format": "uuid"}},
        }
        _add_nullable_for_optional_refs({"components": {"schemas": {"Test": schema}}})
        uuid_schema = schema["properties"]["token_id"]
        assert "anyOf" not in uuid_schema
        assert uuid_schema.get("type") == "string"
        assert uuid_schema.get("format") == "uuid"

    def test_uuid_integration_validates_openapi_3_1(self):
        """Test that spec with uuid fields converts to valid OpenAPI 3.1."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1.0"},
            "basePath": "/api",
            "definitions": {
                "Token": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                    },
                }
            },
            "paths": {
                "/tokens/{id}": {
                    "get": {"responses": {"200": {"schema": {"$ref": "#/definitions/Token"}}}}
                }
            },
        }
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        token_schema = result["components"]["schemas"]["Token"]
        uuid_field = token_schema["properties"]["id"]
        assert "anyOf" not in uuid_field
        assert uuid_field.get("type") == ["string", "null"] or uuid_field.get("type") == [
            "null",
            "string",
        ]
        assert uuid_field.get("format") == "uuid"
