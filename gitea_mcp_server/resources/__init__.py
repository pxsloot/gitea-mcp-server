"""MCP Resources package.

Resources provide read-only data access to Gitea entities via URI templates.
Resources return raw data (JSON or text) with metadata describing the response
schema and optional format hints.  All formatting is done by the display layer
(``tools/display.py`` and ``mcp_tools.py``).

- auto: Auto-generated resources from OpenAPI GET endpoints (raw JSON)
- custom: Hand-written resource implementations with raw data + metadata
- scope: Scope derivation utilities for resources
"""

from gitea_mcp_server.resources.auto import register_auto_generated_resources
from gitea_mcp_server.resources.custom import register_custom_resources
from gitea_mcp_server.resources.scope import derive_required_scope, scope_meta

__all__ = [
    "derive_required_scope",
    "register_auto_generated_resources",
    "register_custom_resources",
    "scope_meta",
]
