"""MCP Resources package.

Resources provide read-only access to Gitea entities via URI templates.

- auto: Auto-generated resources from OpenAPI GET endpoints (raw JSON)
- custom: Hand-written resource implementations with Markdown formatting
- format: Markdown formatters for different entity types
- scope: Scope derivation utilities for resources
- registry: ResourceRegistry catalog class
"""

from gitea_mcp_server.resources.auto import register_auto_generated_resources
from gitea_mcp_server.resources.custom import register_custom_resources
from gitea_mcp_server.resources.registry import ResourceRegistry
from gitea_mcp_server.resources.scope import derive_required_scope, make_resource_meta

__all__ = [
    "ResourceRegistry",
    "derive_required_scope",
    "make_resource_meta",
    "register_auto_generated_resources",
    "register_custom_resources",
]
