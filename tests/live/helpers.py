"""Helper functions for live integration tests.

Minimal set — the ``World`` class and ``RepoState`` methods now handle
most tool calls (create_repo, create_branch, create_file, etc.).
Only two functions remain external:

- ``create_user_token()`` — the one httpx call (Basic Auth token creation)
- ``purge_repo()`` — delete-before-create pre-cleanup
- Repository cleanup is owned by the session-scoped ``World``.

Internal helpers (``_unwrap``, ``_error_text``, ``_assert_ok``,
``is_error``) are used only within this module.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from tests.helpers.mcp_results import extract_text_content


def is_error(result: Any) -> bool:
    """Check if a tool call result indicates an error.

    ``mcp.ClientSession.call_tool()`` returns a ``CallToolResult`` with
    ``.isError`` (camelCase).
    """
    return bool(getattr(result, "isError", False))


def _error_text(result: Any) -> str:
    """Extract error text from a tool call result, handling error content types."""
    content = getattr(result, "content", None)
    if not content:
        return ""
    from mcp.types import TextContent

    texts: list[str] = []
    for item in content:
        if isinstance(item, TextContent):
            texts.append(item.text)
        else:
            texts.append(str(item))
    return "\n".join(texts)


def _assert_ok(result: Any) -> None:
    """Assert a tool call did not error."""
    assert not is_error(result), f"Tool call failed: {_error_text(result)}"


def _unwrap(result: Any) -> dict[str, Any]:
    """Extract and parse JSON from a tool call result."""
    text = extract_text_content(result.content)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        msg = f"Expected dict result, got {type(parsed).__name__}"
        raise TypeError(msg)
    return parsed


# ---------------------------------------------------------------------------
# User token — the one httpx call
# ---------------------------------------------------------------------------


async def create_user_token(
    url: str,
    username: str,
    password: str,
    token_name: str,
    scopes: list[str],
) -> str:
    """Mint a scope-limited token via Basic Auth.

    Uses ``POST /users/{name}/tokens`` which requires password-based
    authentication.  This is the **only** httpx call in the entire live
    test suite.

    Appends a timestamp to *token_name* to avoid conflicts with tokens
    created by previous test runs.

    Returns the ``sha1`` token string (only revealed at creation time).
    """
    unique_name = f"{token_name}-{int(time.time())}"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{url}/api/v1/users/{username}/tokens",
            json={"name": unique_name, "scopes": scopes},
            auth=(username, password),
        )
    assert r.status_code == 201, f"User token creation failed ({r.status_code}): {r.text}"
    data = r.json()
    token: str = data["sha1"]
    assert len(token) > 10, f"Unexpected short token: {token!r}"
    return token


# ---------------------------------------------------------------------------
# Repository cleanup
# ---------------------------------------------------------------------------


async def purge_repo(
    mcp: Any,
    owner: str,
    name: str,
) -> None:
    """Delete a test repo if it exists — pre-cleanup for test setup.

    Does NOT fail if the repo doesn't exist (404).  Fails hard on any
    other error (permission, network, etc.).
    """
    result = await mcp.call_tool(
        "gitea_repo_delete",
        {"owner": owner, "repo": name, "format": "json"},
    )
    if is_error(result):
        text = _error_text(result)
        if "not found" in text.lower() or "404" in text:
            return  # Already clean
    _assert_ok(result)
