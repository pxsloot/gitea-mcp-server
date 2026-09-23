"""Scope derivation utilities for MCP resources and tools.

Flat module to avoid cross-boundary imports between tools/ and resources/.

An operation may carry several Swagger tags that map to different scope
categories.  Gitea/Forgejo enforce **all** declared scope categories for a
route (``tokenRequiresScopes(...)`` delegates to ``HasScope``, which is a
conjunction), and the Swagger tag set is a subset of those categories — the
spec's per-operation ``tags`` cannot express parent-group middleware scopes.
The derived requirement is therefore the *set* of scopes implied by the tags,
and sufficiency requires every one of them.  This is deterministic (no set
iteration order is observable).  Because a usable token holds every true
category, the conjunction is sound whenever the tags are a subset of the true
categories: it can only over-show a tool (the API then answers 403), never hide
a usable one.

The converter reconciles the known cases where the generated tags disagree
with the router (``openapi_converter/normalize.py``, Rule D) — both omissions
and upstream mis-tags — so this module stays generic: it only reads the
operation tags.
"""

from collections.abc import Collection, Iterable

from gitea_mcp_server.constants import HTTP_METHODS_SAFE, TAG_TO_SCOPE


def derive_required_scope(
    swagger_tags: Iterable[str] | None, method: str | None
) -> frozenset[str] | None:
    """Derive the set of token scopes an operation requires from its tags.

    Every recognised tag contributes one scope; a read level is used for safe
    HTTP methods, a write level otherwise.  The result is a set, so the outcome
    is independent of tag iteration order.  Returns ``None`` when no tag maps
    to a scope (no scope required).

    Args:
        swagger_tags: The operation's OpenAPI ``tags`` (any iterable).
        method: HTTP method; ``None`` is treated as a write.

    Returns:
        The required scopes, or ``None`` when none are implied.
    """
    if not swagger_tags:
        return None

    level = "read" if method and method.upper() in HTTP_METHODS_SAFE else "write"

    scopes: set[str] = set()
    for tag in swagger_tags:
        scope_name = TAG_TO_SCOPE.get(tag)
        if scope_name is None:
            continue
        if scope_name == "sudo":
            scopes.add("sudo")
        else:
            scopes.add(f"{level}:{scope_name}")

    return frozenset(scopes) if scopes else None


def has_sufficient_scope(required: Collection[str] | None, available: set[str]) -> bool:
    """Check if available Gitea token scopes satisfy every required scope.

    Rules:
    - No required scopes (``None`` or empty) always passes.
    - ``sudo`` in available grants everything.
    - ``all`` in available grants everything (Gitea's "full access" shortcut,
      returned by the API as the literal scope ``"all"``; the UI displays it as
      ``[all]``).
    - Otherwise every required scope must be individually satisfied: exact
      match, or ``write:xxx`` implies ``read:xxx``.

    Args:
        required: Required scopes, or ``None``/empty for none.
        available: Set of scope strings the user's token possesses.

    Returns:
        True if every required scope is covered by available scopes.
    """
    if not required:
        return True
    if "sudo" in available:
        return True
    if "all" in available:
        return True
    return all(_scope_satisfied(scope, available) for scope in required)


def _scope_satisfied(required: str, available: set[str]) -> bool:
    """Return True if a single required scope is covered by *available*."""
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
]
