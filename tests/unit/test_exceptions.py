"""Tests for exception hierarchy and message formatting."""

from gitea_mcp_server.exceptions import (
    ConfigError,
    GiteaAPIError,
    GiteaMCPError,
    SpecError,
    ToolFilterError,
    ValidationError,
)


class TestGiteaMCPError:
    """Tests for base exception class."""

    def test_is_base_exception(self):
        assert issubclass(GiteaMCPError, Exception)

    def test_can_be_raised_and_caught(self):
        try:
            raise GiteaMCPError("base error")
        except GiteaMCPError as e:
            assert str(e) == "base error"


class TestConfigError:
    """Tests for configuration error."""

    def test_inherits_from_gitea_mcp_error(self):
        assert issubclass(ConfigError, GiteaMCPError)

    def test_message(self):
        e = ConfigError("missing config")
        assert str(e) == "missing config"


class TestGiteaAPIError:
    """Tests for Gitea API communication error."""

    def test_inherits_from_gitea_mcp_error(self):
        assert issubclass(GiteaAPIError, GiteaMCPError)

    def test_default_retry_after_is_none(self):
        e = GiteaAPIError("error")
        assert e.retry_after is None

    def test_message_only(self):
        e = GiteaAPIError("api failure")
        assert str(e) == "api failure"
        assert e.status_code is None
        assert e.response is None
        assert e.headers == {}

    def test_with_status_code(self):
        e = GiteaAPIError("not found", status_code=404)
        assert e.status_code == 404
        assert str(e) == "not found"

    def test_with_response_text(self):
        e = GiteaAPIError("bad request", response='{"message":"bad"}')
        assert e.response == '{"message":"bad"}'

    def test_with_headers(self):
        e = GiteaAPIError("rate limited", headers={"Retry-After": "60"})
        assert e.headers == {"Retry-After": "60"}

    def test_retry_after_is_class_level(self):
        assert GiteaAPIError.retry_after is None


class TestSpecError:
    """Tests for spec loading/conversion error."""

    def test_inherits_from_gitea_mcp_error(self):
        assert issubclass(SpecError, GiteaMCPError)

    def test_message(self):
        e = SpecError("invalid spec")
        assert str(e) == "invalid spec"


class TestToolFilterError:
    """Tests for tool filtering/permissions error."""

    def test_inherits_from_gitea_mcp_error(self):
        assert issubclass(ToolFilterError, GiteaMCPError)

    def test_message(self):
        e = ToolFilterError("no permission")
        assert str(e) == "no permission"


class TestValidationError:
    """Tests for input validation error."""

    def test_inherits_from_gitea_mcp_error(self):
        assert issubclass(ValidationError, GiteaMCPError)

    def test_message_only(self):
        e = ValidationError("invalid input")
        assert str(e) == "invalid input"
        assert e.field is None

    def test_with_field(self):
        e = ValidationError("invalid owner", field="owner")
        assert str(e) == "invalid owner"
        assert e.field == "owner"
