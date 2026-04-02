"""Configuration management for Gitea MCP Server."""

import logging
import threading
from typing import Any, ClassVar

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gitea_mcp_server.exceptions import ConfigError

logger = logging.getLogger(__name__)

# Module-level lock for thread-safe singleton
_config_lock = threading.Lock()


class Config(BaseSettings):
    """Configuration for Gitea MCP Server.

    Loads settings from environment variables and .env file.
    Supports both prefixed (GITEA_*) and standard environment variable names.
    """

    _instance: ClassVar["Config | None"] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Required settings - use alias for environment variable names
    url: str = Field(
        default="http://localhost:3000", description="Base URL of Gitea instance", alias="GITEA_URL"
    )
    token: str = Field(description="API token for authentication", alias="GITEA_TOKEN")

    # Optional settings
    verify_ssl: bool = Field(
        default=True, description="Whether to verify SSL certificates", alias="GITEA_VERIFY_SSL"
    )
    ssl_cert_file: str | None = Field(
        default=None, description="Path to SSL certificate file", alias="SSL_CERT_FILE"
    )
    log_level: str = Field(default="INFO", description="Logging level", alias="LOG_LEVEL")
    log_format: str = Field(
        default="json", description="Log format: json or text", alias="LOG_FORMAT"
    )
    tool_filtering_enabled: bool = Field(
        default=True,
        description="Enable tool filtering based on user permissions",
        alias="TOOL_FILTERING_ENABLED",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL format and ensure it doesn't contain /api/v1."""
        if not v:
            msg = "GITEA_URL cannot be empty"
            raise ConfigError(msg)
        if not v.startswith(("http://", "https://")):
            msg = f"Invalid GITEA_URL: {v} must start with http:// or https://"
            raise ConfigError(msg)
        v = v.rstrip("/")
        if v.endswith("/api/v1"):
            msg = f"GITEA_URL must not include '/api/v1' - provide the base URL only (e.g., 'https://git.example.com')"
            raise ConfigError(msg)
        return v

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        """Validate token is not empty."""
        if not v or not v.strip():
            msg = "GITEA_TOKEN is required and cannot be empty"
            raise ConfigError(msg)
        return v.strip()

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = v.upper()
        if normalized not in valid_levels:
            msg = f"Invalid LOG_LEVEL: {v}. Must be one of {valid_levels}"
            raise ConfigError(msg)
        return normalized

    @property
    def base_url(self) -> str:
        """Get the API base URL."""
        return f"{self.url}/api/v1"

    @classmethod
    def get(cls) -> "Config":
        """Get the singleton Config instance (thread-safe)."""
        with _config_lock:
            if cls._instance is None:
                try:
                    cls._instance = cls()
                except Exception:
                    logger.exception("Failed to initialize configuration")
                    raise
            return cls._instance

    def __init__(self, **data: Any) -> None:
        """Initialize configuration with validation."""
        try:
            super().__init__(**data)
        except ValidationError as e:
            # Convert Pydantic validation errors into a cleaner ConfigError
            error_messages = []
            for error in e.errors():
                # error["loc"] is a tuple of field names; join them
                field = ".".join(str(part) for part in error["loc"] if part != "__root__")
                msg = error["msg"]
                error_messages.append(f"{field}: {msg}")
            raise ConfigError("Configuration errors: " + " | ".join(error_messages)) from e
        except Exception as e:
            msg = f"Configuration error: {e}"
            raise ConfigError(msg) from e

        # Additional validation after initialization
        if not self.token:
            msg = "GITEA_TOKEN is required - set in .env file or environment"
            raise ConfigError(msg)
