"""Helper functions for live integration tests.

Every function in this module wraps a single MCP tool call and asserts the
call succeeded.  This means test setup and assertions are the same thing —
the helpers *are* the tests.

All tool calls use ``format="json"`` because the raw MCP SDK returns
markdown-formatted output by default.  Setting format to JSON allows the
helpers to parse and return structured data.

Design decisions
----------------
- **Token per test**: Every test that needs a user token calls
  ``create_user_token()`` independently.  This exercises the token-creation
  path and keeps tests self-contained.  Token creation is cheap and the test
  instance is ephemeral.
- **No cleanup for tokens/users/orgs**: The Forgejo instance is a throwaway.
  Only repos are deleted (they accumulate on disk otherwise).
- **One httpx call**: ``create_user_token()`` is the only function that uses
  raw HTTP.  It uses Basic Auth against Forgejo's ``POST /users/{name}/tokens``
  endpoint, which requires password-based authentication (a bearer token
  cannot create another token).  Our ``gitea_user_create_token`` tool requires
  sudo/admin.  This single call is how real users create their own tokens.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import httpx

from tests.helpers.mcp_results import extract_text_content

if TYPE_CHECKING:
    from mcp import ClientSession as MCPClient


def _unwrap(result: Any) -> dict[str, Any]:
    """Extract and parse JSON from a tool call result."""
    text = extract_text_content(result.content)
    parsed = json.loads(text)
    assert isinstance(parsed, dict), f"Expected dict result, got {type(parsed)}"
    return parsed


def _error_text(result: Any) -> str:
    """Extract error text from a tool call result, handling error content types.

    When a tool errors, the content may contain ``TextContent`` with
    error text or other content types.  This helper extracts text from
    whatever is available.
    """
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


def is_error(result: Any) -> bool:
    """Check if a tool call result indicates an error.

    ``mcp.ClientSession.call_tool()`` returns a ``CallToolResult`` with
    ``.isError`` (camelCase).
    """
    return bool(getattr(result, "isError", False))


def _assert_ok(result: Any) -> None:
    """Assert a tool call did not error."""
    assert not is_error(result), (
        f"Tool call failed: {_error_text(result)}"
    )


# ---------------------------------------------------------------------------
# Admin tools
# ---------------------------------------------------------------------------


async def create_user(
    mcp: MCPClient,
    username: str,
    password: str,
    email: str | None = None,
    *,
    must_change_password: bool = False,
    admin: bool = False,
) -> dict[str, Any]:
    """Call ``gitea_admin_create_user`` and return the created user.

    Raises ``AssertionError`` with a clear message on failure (the caller
    should use :func:`ensure_user` for resilient setup that handles the
    "already exists" case gracefully).
    """
    kwargs: dict[str, Any] = {
        "username": username,
        "password": password,
        "must_change_password": must_change_password,
        "format": "json",
    }
    if email:
        kwargs["email"] = email
    if admin:
        kwargs["admin"] = True

    result = await mcp.call_tool("gitea_admin_create_user", kwargs)
    _assert_ok(result)
    return _unwrap(result)


async def ensure_user(
    mcp: MCPClient,
    username: str,
    password: str,
    email: str | None = None,
    *,
    must_change_password: bool = False,
    admin: bool = False,
) -> dict[str, Any]:
    """Create or find a test user, failing clearly on errors.

    Prefer this over :func:`create_user` in test setup — it handles the
    "already exists" case internally and surfaces real errors (scope
    filtering, network issues) as clear assertion failures instead of
    swallowing them.

    Returns a user dict with at least ``username`` and ``email`` keys.
    """
    kwargs: dict[str, Any] = {
        "username": username,
        "password": password,
        "must_change_password": must_change_password,
        "format": "json",
    }
    if email:
        kwargs["email"] = email
    if admin:
        kwargs["admin"] = True

    result = await mcp.call_tool("gitea_admin_create_user", kwargs)
    if is_error(result):
        text = _error_text(result)
        if "already exists" in text.lower():
            return {"username": username, "email": email or f"{username}@local"}
        if "restricted by your token scopes" in text.lower():
            pytest_missing = (
                f"Cannot create user '{username}': admin tool is scope-restricted. "
                f"Is the MCP server started with a token that has the 'sudo' scope? "
                f"Original error: {text[:300]}"
            )
            raise AssertionError(pytest_missing) from None
        raise AssertionError(
            f"Failed to create user '{username}': {text[:300]}"
        ) from None
    return _unwrap(result)


async def create_org(
    mcp: MCPClient,
    username: str,
    *,
    full_name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Call ``gitea_org_create`` and return the created org.

    Raises ``AssertionError`` with a clear message on failure.  Prefer
    :func:`ensure_org` for resilient setup.
    """
    kwargs: dict[str, Any] = {"username": username, "format": "json"}
    if full_name:
        kwargs["full_name"] = full_name
    if description:
        kwargs["description"] = description

    result = await mcp.call_tool("gitea_org_create", kwargs)
    _assert_ok(result)
    return _unwrap(result)


async def ensure_org(
    mcp: MCPClient,
    username: str,
    *,
    full_name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create or find an organization, failing clearly on errors.

    Prefer this over :func:`create_org` in test setup — it handles the
    "already exists" case internally.
    """
    kwargs: dict[str, Any] = {"username": username, "format": "json"}
    if full_name:
        kwargs["full_name"] = full_name
    if description:
        kwargs["description"] = description

    result = await mcp.call_tool("gitea_org_create", kwargs)
    if is_error(result):
        text = _error_text(result)
        if "already exists" in text.lower():
            return {"username": username}
        raise AssertionError(
            f"Failed to create org '{username}': {text[:300]}"
        ) from None
    return _unwrap(result)


async def create_team(
    mcp: MCPClient,
    org: str,
    name: str,
    *,
    permission: str = "read",
    units_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call ``gitea_org_create_team`` and return the created team.

    Teams may already exist from a previous run (teams, like users and
    orgs, persist on the throwaway test instance).  The "already exists"
    case is handled gracefully — returns a synthetic dict.
    """
    kwargs: dict[str, Any] = {
        "org": org,
        "name": name,
        "permission": permission,
        "format": "json",
    }
    if units_map:
        kwargs["units_map"] = units_map

    result = await mcp.call_tool("gitea_org_create_team", kwargs)
    if is_error(result):
        text = _error_text(result)
        if "already exists" in text.lower() or "conflict" in text.lower():
            return {"name": name}
    _assert_ok(result)
    return _unwrap(result)


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
    assert r.status_code == 201, (
        f"User token creation failed ({r.status_code}): {r.text}"
    )
    data = r.json()
    token: str = data["sha1"]
    assert len(token) > 10, f"Unexpected short token: {token!r}"
    return token


# ---------------------------------------------------------------------------
# Repository tools
# ---------------------------------------------------------------------------


async def create_repo(
    mcp: MCPClient,
    name: str,
    *,
    auto_init: bool = True,
    description: str | None = None,
    private: bool = False,
) -> dict[str, Any]:
    """Call ``gitea_create_current_user_repo`` and return the created repo."""
    kwargs: dict[str, Any] = {
        "name": name,
        "auto_init": auto_init,
        "private": private,
        "format": "json",
    }
    if description:
        kwargs["description"] = description

    result = await mcp.call_tool("gitea_create_current_user_repo", kwargs)
    _assert_ok(result)
    return _unwrap(result)


async def delete_repo(
    mcp: MCPClient,
    owner: str,
    name: str,
) -> None:
    """Call ``gitea_repo_delete`` to clean up a test repo."""
    result = await mcp.call_tool(
        "gitea_repo_delete",
        {"owner": owner, "repo": name, "format": "json"},
    )
    _assert_ok(result)


async def purge_repo(
    mcp: MCPClient,
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


# ---------------------------------------------------------------------------
# Branch tools
# ---------------------------------------------------------------------------


async def create_branch(
    mcp: MCPClient,
    owner: str,
    repo: str,
    new_branch_name: str,
    *,
    old_branch_name: str = "main",
) -> dict[str, Any]:
    """Call ``gitea_repo_create_branch`` and return the created branch."""
    result = await mcp.call_tool(
        "gitea_repo_create_branch",
        {
            "owner": owner,
            "repo": repo,
            "new_branch_name": new_branch_name,
            "old_branch_name": old_branch_name,
            "format": "json",
        },
    )
    _assert_ok(result)
    return _unwrap(result)


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------


async def create_file(
    mcp: MCPClient,
    owner: str,
    repo: str,
    filepath: str,
    content: str,
    *,
    branch: str = "main",
    message: str | None = None,
) -> dict[str, Any]:
    """Call ``gitea_repo_create_file`` and return the result.

    Note the param name ``filepath`` (not ``file_path``) — this is a known
    naming divergence (see issue #596 I3).
    """
    import base64

    encoded = base64.b64encode(content.encode()).decode()
    kwargs: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "filepath": filepath,
        "content": encoded,
        "branch": branch,
        "format": "json",
    }
    if message:
        kwargs["message"] = message

    result = await mcp.call_tool("gitea_repo_create_file", kwargs)
    _assert_ok(result)
    return _unwrap(result)


# ---------------------------------------------------------------------------
# Tag tools
# ---------------------------------------------------------------------------


async def create_tag(
    mcp: MCPClient,
    owner: str,
    repo: str,
    tag_name: str,
    *,
    target: str = "main",
    message: str | None = None,
) -> dict[str, Any]:
    """Call ``gitea_repo_create_tag`` and return the created tag.

    Note the param name ``tag_name`` (not ``name``) — the Gitea tool
    parameter is ``tag_name`` but the API response uses ``name``.
    """
    kwargs: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "tag_name": tag_name,
        "target": target,
        "format": "json",
    }
    if message:
        kwargs["message"] = message

    result = await mcp.call_tool("gitea_repo_create_tag", kwargs)
    _assert_ok(result)
    return _unwrap(result)


# ---------------------------------------------------------------------------
# Label and milestone tools
# ---------------------------------------------------------------------------


async def create_label(
    mcp: MCPClient,
    owner: str,
    repo: str,
    name: str,
    color: str,
    *,
    description: str | None = None,
    exclusive: bool = False,
) -> dict[str, Any]:
    """Call ``gitea_issue_create_label`` and return the created label."""
    kwargs: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "name": name,
        "color": color,
        "exclusive": exclusive,
        "format": "json",
    }
    if description:
        kwargs["description"] = description

    result = await mcp.call_tool("gitea_issue_create_label", kwargs)
    _assert_ok(result)
    return _unwrap(result)


async def create_milestone(
    mcp: MCPClient,
    owner: str,
    repo: str,
    title: str,
    *,
    description: str | None = None,
    due_date: str | None = None,
) -> dict[str, Any]:
    """Call ``gitea_issue_create_milestone`` and return the created milestone."""
    kwargs: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "title": title,
        "format": "json",
    }
    if description:
        kwargs["description"] = description
    if due_date:
        kwargs["due_date"] = due_date

    result = await mcp.call_tool("gitea_issue_create_milestone", kwargs)
    _assert_ok(result)
    return _unwrap(result)


# ---------------------------------------------------------------------------
# Issue and PR tools
# ---------------------------------------------------------------------------


async def create_issue(
    mcp: MCPClient,
    owner: str,
    repo: str,
    title: str,
    *,
    body: str | None = None,
    labels: list[int | str] | None = None,
    milestone: int | None = None,
    assignees: list[str] | None = None,
) -> dict[str, Any]:
    """Call ``gitea_issue_create_issue`` and return the created issue."""
    kwargs: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "title": title,
        "format": "json",
    }
    if body:
        kwargs["body"] = body
    if labels:
        kwargs["labels"] = labels
    if milestone is not None:
        kwargs["milestone"] = milestone
    if assignees:
        kwargs["assignees"] = assignees

    result = await mcp.call_tool("gitea_issue_create_issue", kwargs)
    _assert_ok(result)
    return _unwrap(result)


async def add_comment(
    mcp: MCPClient,
    owner: str,
    repo: str,
    index: int,
    body: str,
) -> dict[str, Any]:
    """Call ``gitea_issue_create_comment`` and return the created comment."""
    result = await mcp.call_tool(
        "gitea_issue_create_comment",
        {"owner": owner, "repo": repo, "index": index, "body": body, "format": "json"},
    )
    _assert_ok(result)
    return _unwrap(result)


async def create_pull_request(
    mcp: MCPClient,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    *,
    body: str | None = None,
) -> dict[str, Any]:
    """Call ``gitea_repo_create_pull_request`` and return the created PR."""
    kwargs: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "head": head,
        "base": base,
        "title": title,
        "format": "json",
    }
    if body:
        kwargs["body"] = body

    result = await mcp.call_tool("gitea_repo_create_pull_request", kwargs)
    _assert_ok(result)
    return _unwrap(result)


async def create_commit_status(
    mcp: MCPClient,
    owner: str,
    repo: str,
    sha: str,
    state: str,
    *,
    context: str = "ci/live-test",
    description: str | None = None,
    target_url: str | None = None,
) -> dict[str, Any]:
    """Call ``gitea_repo_create_status`` and return the result.

    Valid *state* values: ``pending``, ``success``, ``error``, ``failure``,
    ``warning``.

    .. note::

        This exercises the **B1** bug: the tool's schema previously accepted
        ``open``/``closed``/``all`` (issue states) instead of commit status
        states.  If the tool rejects valid states, that regression is caught
        here.
    """
    kwargs: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "sha": sha,
        "state": state,
        "context": context,
        "format": "json",
    }
    if description:
        kwargs["description"] = description
    if target_url:
        kwargs["target_url"] = target_url

    result = await mcp.call_tool("gitea_repo_create_status", kwargs)
    _assert_ok(result)
    return _unwrap(result)
