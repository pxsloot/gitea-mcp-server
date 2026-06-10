"""Scope derivation utilities for MCP resources and tools.

Re-exports from the flat scope.py module to maintain backward compatibility
for imports from within the resources/ package.
"""

from gitea_mcp_server.scope import derive_required_scope, scope_meta

__all__ = [
    "derive_required_scope",
    "scope_meta",
]
