"""Scope derivation utilities for MCP resources and tools."""

from typing import Any

from gitea_mcp_server.constants import TAG_TO_SCOPE


def derive_required_scope(swagger_tags: set[str] | None, method: str | None) -> str | None:
    """Derive the required Gitea token scope from swagger tags and HTTP method.

    Args:
        swagger_tags: Set of swagger tags from the operation
        method: HTTP method string (e.g., "GET", "POST")

    Returns:
        Scope string (e.g., "read:repository", "write:issue"), or None if no scope needed.
    """
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


def make_resource_meta(required_scope: str | None) -> dict[str, Any]:
    """Create the standard resource metadata dict with required scope.

    Args:
        required_scope: The required scope string or None

    Returns:
        Metadata dict suitable for use with FastMCP resource registration
    """
    return {"fastmcp": {"_internal": {"required_scope": required_scope}}}


__all__ = [
    "derive_required_scope",
    "make_resource_meta",
]
