"""Unit tests for HTTP error translation."""

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from mcp.types import ToolAnnotations

from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.server_setup.mcp_builder import (
    _customize_metadata,
    _ToolWrappingTransform,
)
from gitea_mcp_server.tools.errors import (
    _lookup_response_description,
    _param_is_boolean,
    _run_validation,
)
from gitea_mcp_server.validation import ValidationError


@pytest.fixture
def label_manager():
    """Return a fresh LabelManager per test to avoid shared mutable state."""
    return LabelManager()


class TestErrorHandlingEnhancement:
    """Tests for enhanced error handling using OpenAPI response schemas."""

    @pytest.mark.asyncio
    async def test_formats_404_error_using_openapi_spec(self, label_manager):
        """When component.run raises a 404, transform_fn should format a clean message using the OpenAPI spec's response description."""
        import httpx

        # Minimal OpenAPI spec with a 404 response definition for the endpoint
        openapi_spec = {
            "paths": {
                "/repos/{owner}/{repo}/pulls": {
                    "post": {
                        "responses": {
                            "404": {
                                "description": "APINotFound: The specified repository or resource does not exist."
                            }
                        }
                    }
                }
            }
        }

        # Create a mock route for the PR creation endpoint
        route = MagicMock(
            path="/repos/{owner}/{repo}/pulls",
            method="POST",
            summary="Create a pull request",
            operation_id="repo_create_pull_request",
        )

        # Create a mock OpenAPITool with necessary attributes
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "repo_create_pull_request"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "head": {"type": "string"},
                "base": {"type": "string"},
            }
        }
        tool.output_schema = None
        tool.description = "Create a pull request"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Simulate HTTP 404 error with a realistic response body
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.reason_phrase = "Not Found"
        error_body = {
            "message": "The target couldn't be found.",
            "errors": [
                "could not find 'feature/74-retry-after-header' to be a commit, branch or tag in the head repository mcp-server/gitea-mcp-server"
            ],
            "url": "https://git.home.lan/api/v1/repos/mcp-server/gitea-mcp-server/pulls",
        }
        mock_response.json.return_value = error_body

        http_error = httpx.HTTPStatusError("404 Not Found", request=None, response=mock_response)
        value_error = ValueError(f"HTTP error 404: {mock_response.reason_phrase} - {error_body}")
        value_error.__cause__ = http_error

        tool.run = AsyncMock(side_effect=value_error)

        # Apply metadata and wrap via transform (live pipeline path)
        _customize_metadata(route, tool, openapi_spec=openapi_spec)
        transform = _ToolWrappingTransform(label_manager=label_manager, openapi_spec=openapi_spec)
        [wrapped] = await transform.list_tools([tool])

        with pytest.raises(ValueError) as exc_info:
            await wrapped.run(
                {
                    "owner": "mcp-server",
                    "repo": "gitea-mcp-server",
                    "head": "feature/test",
                    "base": "main",
                }
            )

        error_msg = str(exc_info.value)
        # Should include description from OpenAPI spec
        assert "APINotFound" in error_msg
        # Should include message from response body
        assert "The target couldn't be found." in error_msg
        # Should not contain raw "HTTP error 404" format
        assert "HTTP error 404" not in error_msg

    @pytest.mark.asyncio
    async def test_non_http_errors_unchanged(self, label_manager):
        """Non-HTTP ValueErrors should be re-raised without modification."""

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Raise a ValueError that is NOT from an HTTPStatusError
        value_error = ValueError("Some unrelated validation error")
        tool.run = AsyncMock(side_effect=value_error)

        _customize_metadata(route, tool, openapi_spec=openapi_spec)
        transform = _ToolWrappingTransform(label_manager=label_manager, openapi_spec=openapi_spec)
        [wrapped] = await transform.list_tools([tool])

        with pytest.raises(ValueError) as exc_info:
            await wrapped.run({})

        assert str(exc_info.value) == "Some unrelated validation error"

    @pytest.mark.asyncio
    async def test_formats_network_error_cleanly(self, label_manager):
        """httpx.NetworkError (without response) should be formatted as a network issue."""
        import httpx

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Simulate a network error (no response attribute)
        network_error = httpx.NetworkError("Connection failed")
        tool.run = AsyncMock(side_effect=network_error)

        _customize_metadata(route, tool, openapi_spec=openapi_spec)
        transform = _ToolWrappingTransform(label_manager=label_manager, openapi_spec=openapi_spec)
        [wrapped] = await transform.list_tools([tool])

        with pytest.raises(ValueError) as exc_info:
            await wrapped.run({})

        error_msg = str(exc_info.value)
        assert "Network error" in error_msg or "Could not connect" in error_msg
        assert "Connection failed" in error_msg

    @pytest.mark.asyncio
    async def test_formats_timeout_error_cleanly(self, label_manager):
        """httpx.TimeoutException should be formatted as a timeout issue."""
        import httpx

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        timeout_error = httpx.TimeoutException("Request timed out")
        tool.run = AsyncMock(side_effect=timeout_error)

        _customize_metadata(route, tool, openapi_spec=openapi_spec)
        transform = _ToolWrappingTransform(label_manager=label_manager, openapi_spec=openapi_spec)
        [wrapped] = await transform.list_tools([tool])

        with pytest.raises(ValueError) as exc_info:
            await wrapped.run({})

        error_msg = str(exc_info.value)
        assert "timeout" in error_msg.lower() or "timed out" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_formats_unexpected_exception_cleanly(self, label_manager):
        """Unexpected exceptions (RuntimeError, etc.) should be caught and formatted."""

        openapi_spec = {"paths": {}}

        route = MagicMock(path="/test", method="POST", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        # Simulate an unexpected error
        unexpected_error = RuntimeError("Something unexpected happened")
        tool.run = AsyncMock(side_effect=unexpected_error)

        _customize_metadata(route, tool, openapi_spec=openapi_spec)
        transform = _ToolWrappingTransform(label_manager=label_manager, openapi_spec=openapi_spec)
        [wrapped] = await transform.list_tools([tool])

        with pytest.raises(ValueError) as exc_info:
            await wrapped.run({})

        error_msg = str(exc_info.value)
        # Should be user-friendly, not expose raw exception type by default
        assert "unexpected" in error_msg.lower()
        # Should not show full Python traceback to user
        assert "RuntimeError" not in error_msg


class TestLookupResponseDescription:
    """Tests for _lookup_response_description function."""

    def test_route_not_found_in_paths(self):
        """When route.path is not found in paths, should return fallback."""
        openapi_spec = {"paths": {}}
        result = _lookup_response_description(openapi_spec, "/nonexistent", "GET", 404)
        assert result == "HTTP error 404"

    def test_empty_method_falls_back(self):
        """When route.method is empty, should return fallback."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "404": {"description": "Not Found"},
                        }
                    }
                }
            }
        }
        result = _lookup_response_description(openapi_spec, "/test", "", 404)
        assert result == "HTTP error 404"

    def test_status_code_not_in_responses(self):
        """When status code is not in operation responses, should return fallback."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {"description": "OK"},
                        }
                    }
                }
            }
        }
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "HTTP error 404"

    def test_response_def_not_dict(self):
        """When response_def is not a dict, should return fallback."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "404": "just a string",
                        }
                    }
                }
            }
        }
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "HTTP error 404"

    def test_ref_resolution(self):
        """$ref in response_def should be resolved to get description."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "404": {"$ref": "#/components/responses/NotFound"},
                        }
                    }
                }
            },
            "components": {
                "responses": {
                    "NotFound": {"description": "Resource not found"},
                }
            },
        }
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "Resource not found"

    def test_ref_resolution_resolved_not_dict(self):
        """When _resolve_ref returns non-dict, should fallback."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "404": {"$ref": "#/components/responses/NotFound"},
                        }
                    }
                }
            },
            "components": {
                "responses": {
                    "NotFound": "just a string",
                }
            },
        }
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "HTTP error 404"

    def test_ref_resolution_missing_description(self):
        """When resolved ref has no description, should fallback."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "404": {"$ref": "#/components/responses/NotFound"},
                        }
                    }
                }
            },
            "components": {
                "responses": {
                    "NotFound": {"type": "object"},
                }
            },
        }
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "HTTP error 404"

    def test_ref_resolution_with_description_from_schema(self):
        """$ref pointing to a schema with description should work."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "404": {"$ref": "#/components/schemas/Error"},
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Error": {"description": "Standard error response"},
                }
            },
        }
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "Standard error response"

    def test_exception_during_lookup(self):
        """When a KeyError occurs during lookup, should return fallback."""
        openapi_spec = {"paths": {0: "bad"}}
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "HTTP error 404"

    def test_non_dict_paths_raises_attribute_error(self):
        """When paths is not a dict, .get() raises AttributeError → fallback."""
        openapi_spec = {"paths": [1, 2, 3]}
        result = _lookup_response_description(openapi_spec, "/test", "GET", 404)
        assert result == "HTTP error 404"


class TestRunValidation:
    """Tests for _run_validation function."""

    def test_no_required_params(self):
        """When required_params is None/empty, should not raise."""
        _run_validation({"x": 1})  # should not raise

    def test_missing_required_params(self):
        """When a required param is missing, should raise ValidationError."""
        with pytest.raises(ValidationError, match="Missing required parameter"):
            _run_validation({"x": 1}, required_params=["x", "y"])

    def test_validation_passes(self):
        """When all validators pass, should not raise."""
        _run_validation({"owner": "valid-owner", "repo": "valid-repo"}, required_params=["owner", "repo"])

    def test_pagination_validation_passes(self):
        """When page/per_page are present with valid values, should not raise."""
        _run_validation({"page": 1, "per_page": 50})

    def test_pagination_validation_rejects_invalid(self):
        """When per_page is too high, should raise ValidationError."""
        with pytest.raises(ValidationError, match="page|per_page"):
            _run_validation({"page": 1, "per_page": 99999})

    def test_validator_raises_type_error_wraps_cleanly(self, monkeypatch):
        """When a SINGLE_VALIDATOR raises TypeError, it should be wrapped as ValidationError."""
        from gitea_mcp_server.validation import SINGLE_VALIDATORS

        def bad_validator(value, *, field):
            raise TypeError("unexpected type")

        monkeypatch.setitem(SINGLE_VALIDATORS, "owner", bad_validator)
        with pytest.raises(ValidationError, match="Validation error"):
            _run_validation({"owner": "anything"})

    def test_validator_raises_validation_error_re_raises(self):
        """When a SINGLE_VALIDATOR raises ValidationError, it should re-raise unchanged."""
        with pytest.raises(ValidationError):
            _run_validation({"owner": ""})


class TestParamIsBoolean:
    """Tests for _param_is_boolean helper."""

    def test_none_properties_returns_false(self):
        assert _param_is_boolean(None, "labels") is False

    def test_missing_param_returns_false(self):
        assert _param_is_boolean({"owner": {"type": "string"}}, "labels") is False

    def test_string_type_returns_false(self):
        assert _param_is_boolean({"labels": {"type": "string"}}, "labels") is False

    def test_array_type_returns_false(self):
        assert _param_is_boolean({"labels": {"type": "array"}}, "labels") is False

    def test_boolean_type_string_returns_true(self):
        assert _param_is_boolean({"labels": {"type": "boolean"}}, "labels") is True

    def test_boolean_in_list_type_returns_true(self):
        assert _param_is_boolean({"labels": {"type": ["boolean", "null"]}}, "labels") is True

    def test_array_in_list_type_returns_false(self):
        assert _param_is_boolean({"labels": {"type": ["array", "null"]}}, "labels") is False


class TestRunValidationParamProperties:
    """Regression tests: _run_validation with param_projects should skip validators
    when the parameter schema declares a boolean type."""

    def test_labels_boolean_skips_validate_labels(self):
        """labels param with boolean schema should NOT trigger validate_labels."""
        _run_validation(
            {"labels": True},
            param_properties={"labels": {"type": "boolean"}},
        )

    def test_labels_boolean_nullable_skips_validate_labels(self):
        """labels param with ['boolean', 'null'] schema should NOT trigger validate_labels."""
        _run_validation(
            {"labels": True},
            param_properties={"labels": {"type": ["boolean", "null"]}},
        )

    def test_labels_array_still_validates(self):
        """labels param with array schema should still trigger validate_labels."""
        with pytest.raises(ValidationError, match="must be a list"):
            _run_validation(
                {"labels": True},
                param_properties={"labels": {"type": "array", "items": {"type": "string"}}},
            )

    def test_boolean_skip_does_not_affect_other_validators(self):
        """Other validators should still run even when labels is boolean."""
        with pytest.raises(ValidationError, match="must be one of"):
            _run_validation(
                {"labels": True, "state": "invalid"},
                param_properties={
                    "labels": {"type": "boolean"},
                    "state": {"type": "string"},
                },
            )

    def test_no_param_properties_still_validates(self):
        """When param_properties is None, all validators should run."""
        with pytest.raises(ValidationError, match="must be a list"):
            _run_validation({"labels": True})

    def test_empty_param_properties_skips_boolean_check(self):
        """When param_properties is empty dict, validators should run."""
        with pytest.raises(ValidationError, match="must be a list"):
            _run_validation({"labels": True}, param_properties={})


@pytest.mark.asyncio
class TestCatchAllErrorHandler:
    """Tests for the catch-all ``(KeyError, TypeError, AttributeError, RuntimeError)`` handler."""

    @pytest.mark.parametrize(
        ("exception", "exc_name"),
        [
            pytest.param(KeyError("missing_key"), "KeyError", id="KeyError"),
            pytest.param(TypeError("unsupported operand"), "TypeError", id="TypeError"),
            pytest.param(AttributeError("no such attr"), "AttributeError", id="AttributeError"),
            pytest.param(RuntimeError("boom"), "RuntimeError", id="RuntimeError"),
        ],
    )
    async def test_all_exception_types_are_caught(self, exception, exc_name, caplog):
        """All four exception types produce a user-friendly ValueError."""
        caplog.set_level(logging.ERROR)

        from gitea_mcp_server.tools.errors import _run_with_error_handling

        tool = MagicMock()
        tool.name = "my_tool"

        async def failing_run(kwargs):
            raise exception

        tool.run = failing_run

        with pytest.raises(ValueError) as exc_info:
            await _run_with_error_handling(
                kwargs={"owner": "me"},
                component=tool,
                openapi_spec=None,
                route_path="/repos/{owner}",
                route_method="GET",
            )

        error_msg = str(exc_info.value)
        assert "unexpected error" in error_msg.lower()
        assert exc_name not in error_msg

    @pytest.mark.parametrize("exc_type", [KeyError, TypeError, AttributeError, RuntimeError])
    async def test_log_contains_tool_context(self, exc_type, caplog):
        """Log message includes tool name, HTTP method, route, and arg keys."""
        caplog.set_level(logging.ERROR)

        from gitea_mcp_server.tools.errors import _run_with_error_handling

        tool = MagicMock()
        tool.name = "context_tool"

        async def failing_run(kwargs):
            raise exc_type("fail")

        tool.run = failing_run

        with pytest.raises(ValueError):
            await _run_with_error_handling(
                kwargs={"owner": "me", "repo": "my-repo"},
                component=tool,
                openapi_spec=None,
                route_path="/repos/{owner}/{repo}",
                route_method="POST",
            )

        assert any("context_tool" in r.message for r in caplog.records)
        assert any("POST" in r.message for r in caplog.records)
        assert any("/repos/{owner}/{repo}" in r.message for r in caplog.records)
        assert any("owner" in r.message for r in caplog.records)

    async def test_component_without_name_falls_back(self, caplog):
        """When component has no ``name`` attribute, logs 'unknown'."""
        caplog.set_level(logging.ERROR)

        from gitea_mcp_server.tools.errors import _run_with_error_handling

        # MagicMock auto-creates any accessed attribute, so we must
        # set then delete ``.name`` to simulate a component without one
        # and exercise the ``getattr(component, "name", "unknown")`` fallback.
        tool = MagicMock(spec=[])
        tool.name = "nameless"
        del tool.name

        async def failing_run(kwargs):
            raise RuntimeError("fail")

        tool.run = failing_run

        with pytest.raises(ValueError):
            await _run_with_error_handling(
                kwargs={},
                component=tool,
                openapi_spec=None,
                route_path="/test",
                route_method="GET",
            )

        assert any("unknown" in r.message for r in caplog.records)


class TestErrorHandlingNonJson:
    """Tests for error handling with non-JSON response bodies."""

    @pytest.mark.asyncio
    async def test_non_json_error_body_formatted_cleanly(self, label_manager):
        """When HTTP error response body is not valid JSON, should fall back to response.text."""
        openapi_spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "500": {"description": "Internal Server Error"},
                        }
                    }
                }
            }
        }

        route = MagicMock(path="/test", method="GET", summary="Test", operation_id="test")
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "test"
        tool.annotations = ToolAnnotations()
        tool.tags = set()
        tool.parameters = {"properties": {}}
        tool.output_schema = None
        tool.description = "Test"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.reason_phrase = "Internal Server Error"
        # Simulate non-JSON response: .json() raises ValueError
        mock_response.json.side_effect = ValueError("Not JSON")
        mock_response.text = "Internal Server Error: something went wrong"

        http_error = httpx.HTTPStatusError("500 Error", request=None, response=mock_response)
        value_error = ValueError(f"HTTP error 500: {mock_response.reason_phrase}")
        value_error.__cause__ = http_error

        tool.run = AsyncMock(side_effect=value_error)

        _customize_metadata(route, tool, openapi_spec=openapi_spec)
        transform = _ToolWrappingTransform(label_manager=label_manager, openapi_spec=openapi_spec)
        [wrapped] = await transform.list_tools([tool])

        with pytest.raises(ValueError) as exc_info:
            await wrapped.run({})

        error_msg = str(exc_info.value)
        assert "Internal Server Error" in error_msg
        assert "something went wrong" in error_msg


class TestParamIsBoolean:
    """Tests for _param_is_boolean edge cases."""

    def test_non_string_non_list_type_returns_false(self):
        """_param_is_boolean returns False when type is neither str nor list."""
        from gitea_mcp_server.tools.errors import _param_is_boolean

        # When type is a dict (or other non-str/non-list), line 87 return False
        assert _param_is_boolean({"flag": {"type": {}}}, "flag") is False

    def test_non_dict_schema_returns_false(self):
        """_param_is_boolean returns False when schema entry is not a dict (line 81)."""
        from gitea_mcp_server.tools.errors import _param_is_boolean

        # When properties has a non-dict value for the parameter name
        assert _param_is_boolean({"flag": "string_value"}, "flag") is False
