"""Gitea MCP Server - Model Context Protocol server for Gitea/Forgejo."""

__version__ = "0.3.0"
__author__ = "Peter"

from gitea_mcp_server.config import Config, ConfigProtocol
from gitea_mcp_server.exceptions import (
    ConfigError,
    GiteaAPIError,
    GiteaMCPError,
    SpecError,
    ToolFilterError,
    ValidationError,
)

__all__ = [
    "Config",
    "ConfigError",
    "ConfigProtocol",
    "GiteaAPIError",
    "GiteaMCPError",
    "SpecError",
    "ToolFilterError",
    "ValidationError",
]
