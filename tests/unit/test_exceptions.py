"""Tests for exception hierarchy and message formatting."""

import pytest

from gitea_mcp_server.exceptions import (
    ConfigError,
    GiteaAPIError,
    GiteaMCPError,
    SpecError,
    ToolFilterError,
    ValidationError,
)
from typing import Never


class TestGiteaMCPError:
    """Tests for base exception class."""

    def test_is_base_exception(self) -> None:
        assert issubclass(GiteaMCPError, Exception)

    def test_can_be_raised_and_caught(self) -> Never:
        msg = "base error"
        with pytest.raises(GiteaMCPError) as exc:
            raise GiteaMCPError(msg)
        assert str(exc.value) == "base error"


class TestConfigError:
    """Tests for configuration error."""

    def test_inherits_from_gitea_mcp_error(self) -> None:
        assert issubclass(ConfigError, GiteaMCPError)

    def test_message(self) -> None:
        e = ConfigError("missing config")
        assert str(e) == "missing config"


class TestGiteaAPIError:
    """Tests for Gitea API communication error."""

    def test_inherits_from_gitea_mcp_error(self) -> None:
        assert issubclass(GiteaAPIError, GiteaMCPError)

    def test_default_retry_after_is_none(self) -> None:
        e = GiteaAPIError("error")
        assert e.retry_after is None

    def test_message_only(self) -> None:
        e = GiteaAPIError("api failure")
        assert str(e) == "api failure"
        assert e.status_code is None
        assert e.response is None
        assert e.headers == {}

    def test_with_status_code(self) -> None:
        e = GiteaAPIError("not found", status_code=404)
        assert e.status_code == 404
        assert str(e) == "not found"

    def test_with_response_text(self) -> None:
        e = GiteaAPIError("bad request", response='{"message":"bad"}')
        assert e.response == '{"message":"bad"}'

    def test_with_headers(self) -> None:
        e = GiteaAPIError("rate limited", headers={"Retry-After": "60"})
        assert e.headers == {"Retry-After": "60"}

    def test_retry_after_is_class_level(self) -> None:
        assert GiteaAPIError.retry_after is None

    def test_with_all_args(self) -> None:
        """Construct with all optional args and assert round-trip."""
        e = GiteaAPIError(
            "rate limited",
            status_code=429,
            response='{"message":"too fast"}',
            headers={"Retry-After": "30"},
        )
        assert str(e) == "rate limited"
        assert e.status_code == 429
        assert e.response == '{"message":"too fast"}'
        assert e.headers == {"Retry-After": "30"}


class TestSpecError:
    """Tests for spec loading/conversion error."""

    def test_inherits_from_gitea_mcp_error(self) -> None:
        assert issubclass(SpecError, GiteaMCPError)

    def test_message(self) -> None:
        e = SpecError("invalid spec")
        assert str(e) == "invalid spec"


class TestToolFilterError:
    """Tests for tool filtering/permissions error."""

    def test_inherits_from_gitea_mcp_error(self) -> None:
        assert issubclass(ToolFilterError, GiteaMCPError)

    def test_message(self) -> None:
        e = ToolFilterError("no permission")
        assert str(e) == "no permission"


class TestValidationError:
    """Tests for input validation error."""

    def test_inherits_from_gitea_mcp_error(self) -> None:
        assert issubclass(ValidationError, GiteaMCPError)

    def test_message_only(self) -> None:
        e = ValidationError("invalid input")
        assert str(e) == "invalid input"
        assert e.field is None

    def test_with_field(self) -> None:
        e = ValidationError("invalid owner", field="owner")
        assert str(e) == "invalid owner"
        assert e.field == "owner"
