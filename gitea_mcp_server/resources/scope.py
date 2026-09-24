"""Scope derivation utilities for MCP resources and tools.

Re-exports from the flat :mod:`gitea_mcp_server.scope` module so consumers
inside the ``resources/`` package have a package-local import path, without
introducing a cyclic dependency on ``tools/``.
"""

from gitea_mcp_server.scope import derive_required_scope, has_sufficient_scope

__all__ = [
    "derive_required_scope",
    "has_sufficient_scope",
]
