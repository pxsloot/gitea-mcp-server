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


def has_sufficient_scope(required: str | None, available: set[str]) -> bool:
    """Check if available Gitea token scopes satisfy a required scope.

    Rules:
    - None required (no scope needed) always passes.
    - ``sudo`` in available grants everything.
    - ``all`` in available grants everything (Gitea's "full access" shortcut,
      returned by the API as the literal scope ``"all"``; the UI displays it as
      ``[all]``).
    - Exact match passes.
    - ``write:xxx`` implies ``read:xxx``.

    Args:
        required: Required scope string or None.
        available: Set of scope strings the user's token possesses.

    Returns:
        True if the required scope is covered by available scopes.
    """
    if required is None:
        return True
    if "sudo" in available:
        return True
    if "all" in available:
        return True
    if required in available:
        return True
    if required.startswith("read:"):
        resource = required.split(":", 1)[1]
        if f"write:{resource}" in available:
            return True
    return False


__all__ = [
    "derive_required_scope",
    "has_sufficient_scope",
    "scope_meta",
]
