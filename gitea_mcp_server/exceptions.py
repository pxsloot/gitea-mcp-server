"""Custom exceptions for Gitea MCP Server."""


class GiteaMCPError(Exception):
    """Base exception for Gitea MCP Server."""


class ConfigError(GiteaMCPError):
    """Configuration related errors."""


class GiteaAPIError(GiteaMCPError):
    """Errors when communicating with Gitea API."""

    def __init__(self, message: str, status_code: int | None = None, response: str | None = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class SpecError(GiteaMCPError):
    """Errors related to OpenAPI spec loading or conversion."""


class ToolFilterError(GiteaMCPError):
    """Errors related to tool filtering and permissions."""


class ValidationError(GiteaMCPError):
    """Raised when tool arguments fail validation."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field
