"""Scope derivation utilities for MCP resources and tools.

Flat module to avoid cross-boundary imports between tools/ and resources/.
"""

from typing import Any

from gitea_mcp_server.constants import TAG_TO_SCOPE


def derive_required_scope(swagger_tags: set[str] | None, method: str | None) -> str | None:
    if not swagger_tags:
        return None

    scope_name = None
    for tag in swagger_tags:
        s = TAG_TO_SCOPE.get(tag)
        if s is not None:
            scope_name = s
            break

    if scope_name is None:
        return None

    if scope_name == "sudo":
        return "sudo"

    if method and method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return f"read:{scope_name}"
    return f"write:{scope_name}"


def scope_meta(required_scope: str | None) -> dict[str, Any]:
    return {"required_scope": required_scope}


__all__ = [
    "derive_required_scope",
    "scope_meta",
]
