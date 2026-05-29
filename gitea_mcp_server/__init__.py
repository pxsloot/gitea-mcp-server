"""Gitea MCP Server - Model Context Protocol server for Gitea/Forgejo."""

__version__ = "0.1.0"
__author__ = "Peter"

from gitea_mcp_server.config import Config
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
    "GiteaAPIError",
    "GiteaMCPError",
    "SpecError",
    "ToolFilterError",
    "ValidationError",
]
