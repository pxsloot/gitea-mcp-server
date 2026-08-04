"""Shared test-world — identities, server pool, and lazy state graph.

This module serves two roles:

1. **Canonical identities** (backward-compatible) — ``User``, ``DEV``,
   ``PEER``, ``RO``, ``LIMITED``, scope constants, org/team names.
   All existing test files import these unchanged.

2. **World** (new — session-scoped fixture) — a lazy state graph that
   replaces per-test server spawns with pooled servers and idempotent
   ``need_*`` methods.  The first call to ``need_repo("dev", "x")``
   creates the repo (testing the tool); every subsequent call returns
   the cached ``RepoState`` without re-creating anything.

Server pool design
------------------
Instead of ~86 ``mcp_client`` context managers (one per test function),
the World starts **one server per token scope** and pools them:

+----------------+---------------------------+----------------------+
| Token scope    | Started                   | Serves               |
+================+===========================+======================+
| Admin (sudo)   | ``World.start()``         | ``need_user``,       |
|                |                           | ``need_org``,        |
|                |                           | ``need_team``        |
+----------------+---------------------------+----------------------+
| DEV write      | First ``server_for(DEV,   | All repo/issue/PR    |
|                | SCOPE_WRITE)``            | workflow tests       |
+----------------+---------------------------+----------------------+
| RO read-only   | First ``server_for(RO,    | ``test_scope.py``    |
|                | SCOPE_READ)``             |                      |
+----------------+---------------------------+----------------------+
| LIMITED        | First ``server_for(       | ``test_scope.py``,   |
|                | LIMITED, ...)``           | discovery tests      |
+----------------+---------------------------+----------------------+

This drops server process spawns from ~86 to ~4, saving ~2 minutes.
One World is created per pytest worker. Tests assigned to that worker share
its pooled servers and execute sequentially, while the namespace suffix keeps
different workers and concurrent runs isolated.

State graph
-----------
The World tracks what exists::

    World
    ├── _users  {"dev": {...}, "peer": {...}, ...}
    ├── _orgs   {"live-org": {...}}
    ├── _teams  {("live-org", "live-team"): {...}}
    └── _repos  {"dev/workflow-repo": (RepoRequest, RepoState),
                  "dev/issues-repo":   (RepoRequest, RepoState), ...}

Each ``RepoState`` tracks branches, labels, milestones, issues, pull requests,
and tags
inside that repo.  ``need_*`` methods are idempotent — create+verify
the first time, return cached state every subsequent call.

Design decisions
----------------
- **Server startup tested once per scope.**  The first ``server_for``
  call starts a real stdio MCP server.  If that startup path breaks,
  the first test that needs that server scope catches it.
- **Token caching** (existing): ``get_token()`` caches tokens per
  (user, scopes) key.  One mint per combination per suite run.
- **Canonical scope lists**: ``SCOPE_WRITE``, ``SCOPE_READ``,
  ``SCOPE_LIMITED`` — single source of truth.
- **Repositories are cleaned up at World teardown.** ``purge_repo`` also
  runs before creation so interrupted runs start cleanly. Users, orgs, tokens,
  and teams persist on the throwaway test instance.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from tests.helpers.mcp_results import extract_text_content
from tests.live.conflict import BootstrapVerificationError, RepoRequest, check_conflict
from tests.live.dependency_graph import DependencyGraph

if TYPE_CHECKING:
    from mcp import ClientSession

_WORKER: str = os.getenv("PYTEST_XDIST_WORKER", "local")
"""The xdist worker id, or ``local`` outside xdist."""

_RUN_ID: str = re.sub(
    r"[^a-z0-9-]", "-",
    os.getenv("GITEA_LIVE_RUN_ID", uuid.uuid4().hex[:8]).lower(),
).strip("-")[:16] or uuid.uuid4().hex[:8]
"""Run namespace; override with ``GITEA_LIVE_RUN_ID`` in CI."""

_NAMESPACE: str = f"{_RUN_ID}-{_WORKER}"
"""Unique suffix preventing concurrent live runs from sharing entities."""

# =============================================================================
# Canonical scope lists
# =============================================================================

SCOPE_WRITE = ["write:repository", "write:issue", "write:user"]
"""Full write access — the primary actor scopes."""

SCOPE_READ = ["read:repository", "read:user", "read:issue"]
"""Read-only access — for scope gating tests."""

SCOPE_LIMITED = ["write:repository", "read:issue"]
"""Partial write — can create repos but not issues."""

# =============================================================================
# Test identities
# =============================================================================


class User:
    """A test user identity — username, password, email."""

    __slots__ = ("email", "password", "username")

    def __init__(self, base: str, password: str) -> None:
        self.username = f"{base}-{_NAMESPACE}"
        self.password = password
        self.email = f"{self.username}@live-test.local"


DEV = User("live-dev", "dev-pass-007")
"""Primary actor for workflow tests."""

PEER = User("live-peer", "peer-pass-007")
"""PR counterpart / second actor."""

RO = User("live-ro", "ro-pass-007")
"""Read-only victim for scope gating."""

LIMITED = User("live-limited", "limited-pass-007")
"""Partial-scope victim for scope gating."""

ALL_USERS = (DEV, PEER, RO, LIMITED)

# =============================================================================
# Token cache — one token per (user, scopes) per suite run (backward-compat)
# =============================================================================

_tokens: dict[str, str] = {}


async def get_token(url: str, user: User, scopes: list[str]) -> str:
    """Get a cached token for *user* with *scopes*.  Mints on first call.

    Tokens are cached per (username, sorted scopes) key for the lifetime
    of the test suite run.  Subsequent calls for the same user+scopes
    return the cached token without hitting the Gitea API.

    This is the backward-compatible entry point — tests also call
    ``World.token()`` (same cache, same behaviour).
    """
    key = f"{user.username}:{','.join(sorted(scopes))}"
    if key in _tokens:
        return _tokens[key]
    from tests.live.helpers import create_user_token

    token = await create_user_token(url, user.username, user.password, "cached-ci", scopes)
    _tokens[key] = token
    return token


# =============================================================================
# Org and team
# =============================================================================

ORG_NAME = f"live-org-{_NAMESPACE}"
"""Test organization name."""

TEAM_NAME = f"live-team-{_NAMESPACE}"
"""Test team within the organization."""


# =============================================================================
# Internal helpers (no circular imports)
# =============================================================================


def _is_error(result: Any) -> bool:
    """Check if an MCP tool call result indicates an error (has ``.isError``)."""
    return bool(getattr(result, "isError", False))


def _unwrap(result: Any) -> dict[str, Any]:
    """Extract and parse JSON from a tool call result.

    Raises ``TypeError`` if the parsed result is not a dict.
    """
    text = extract_text_content(result.content)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        msg = f"Expected dict result, got {type(parsed).__name__}"
        raise TypeError(msg)
    return cast("dict[str, Any]", parsed)


def _error_text(result: Any) -> str:
    """Extract error text from a tool call result."""
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


def _assert_keys(data: dict[str, Any], *keys: str) -> None:
    """Assert all *keys* are present in *data*."""
    missing = [k for k in keys if k not in data]
    if missing:
        msg = f"Missing required keys: {missing}. Available: {sorted(data.keys())}"
        raise AssertionError(msg)


def _assert_key_types(data: dict[str, Any], **typed: type) -> None:
    """Assert specific keys have the expected types.

    Raises ``TypeError`` if a key has the wrong type.
    """
    for key, expected_type in typed.items():
        actual = data.get(key)
        if not isinstance(actual, expected_type):
            msg = (
                f"Key {key!r}: expected {expected_type.__name__}, "
                f"got {type(actual).__name__} ({actual!r})"
            )
            raise TypeError(msg)


def _assert_content(data: dict[str, Any], **expected: Any) -> None:
    """Assert specific key-value pairs match exactly."""
    for key, expected_val in expected.items():
        actual = data.get(key)
        if actual != expected_val:
            msg = f"Key {key!r}: expected {expected_val!r}, got {actual!r}"
            raise AssertionError(msg)


# =============================================================================
# RepoState — tracks what's inside a known repo
# =============================================================================


@dataclass
class RepoState:
    """Lazy state tracker for a single test repository.

    Created by ``World.need_repo()``.  ``need_*`` methods are
    idempotent — they create+verify the first time and return cached
    state every subsequent call.

    Attrs:
        owner: Repository owner (login name).
        name: Repository name.
        data: Raw API response dict from ``create_repo``.
        branches: ``{branch_name: branch_data}`` — created lazily.
        labels: ``{label_name: label_data}`` — created lazily.
        milestones: ``{milestone_title: milestone_data}`` — created lazily.
        issues: ``{issue_number: issue_data}`` — created lazily.
        tags: ``{tag_name: tag_data}`` — created lazily.
    """

    owner: str
    name: str
    data: dict[str, Any]

    # Back-reference to the World — needed to call tools through the
    # pooled server for this repo's owner+scopes.
    _world: World = field(repr=False)
    _user: User = field(repr=False)
    _scopes: list[str] = field(repr=False)

    branches: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: dict[str, dict[str, Any]] = field(default_factory=dict)
    milestones: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    pull_requests: dict[int, dict[str, Any]] = field(default_factory=dict)
    tags: dict[str, dict[str, Any]] = field(default_factory=dict)
    _files: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    """Files cached by ``{branch}:{path}`` key."""

    # ── Per-resource option guards (conflict detection) ─────────────────
    _branch_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _label_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _milestone_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _issue_options: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)
    _tag_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _pr_options: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)
    _file_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    async def _server(self) -> ClientSession:
        """Get the pooled server for this repo's owner+scopes."""
        return await self._world.server_for(self._user, self._scopes)

    # ── need_* — idempotent create-or-return ──────────────────────────

    async def need_branch(
        self, name: str, *, old: str = "main"
    ) -> dict[str, Any]:
        """Ensure a branch exists.  Creates from *old* if not cached.

        Raises:
            ConflictError: If a previous request for this branch name
                used a different *old* (source branch).
        """
        if name in self.branches:
            check_conflict(
                "branch", name,
                self._branch_options.get(name, {}),
                {"old": old},
            )
            return self.branches[name]

        mcp = await self._server()
        result = await mcp.call_tool(
            "gitea_repo_create_branch",
            {
                "owner": self.owner,
                "repo": self.name,
                "new_branch_name": name,
                "old_branch_name": old,
                "format": "json",
            },
        )
        if _is_error(result):
            msg = f"need_branch({name!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.branches[name] = data
        self._branch_options[name] = {"old": old}
        return data

    async def need_file(
        self,
        path: str,
        content: str,
        *,
        branch: str = "main",
        message: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a file exists on *branch*.  Creates if not cached.

        Note the param name ``path`` (not ``filepath``) — the underlying
        tool uses ``filepath`` (a known naming divergence).
        """
        file_key = f"{branch}:{path}"
        if file_key in self._files:
            check_conflict(
                "file", f"{file_key!r}",
                self._file_options.get(file_key, {}),
                {"content": content, "message": message},
            )
            return self._files[file_key]

        if branch != "main" and branch not in self.branches:
            await self.need_branch(branch)

        import base64

        mcp = await self._server()
        encoded = base64.b64encode(content.encode()).decode()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "filepath": path,
            "content": encoded,
            "branch": branch,
            "format": "json",
        }
        if message:
            kwargs["message"] = message

        result = await mcp.call_tool("gitea_repo_create_file", kwargs)
        if _is_error(result):
            msg = f"need_file({path!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self._files[file_key] = data
        self._file_options[file_key] = {"content": content, "message": message}
        return data

    async def need_label(
        self,
        name: str,
        color: str = "#000000",
        *,
        description: str | None = None,
        exclusive: bool = False,
    ) -> dict[str, Any]:
        """Ensure a label exists.  Creates if not cached.

        Raises:
            ConflictError: If a previous request for this label name
                used different *color*, *description*, or *exclusive*.
        """
        if name in self.labels:
            check_conflict(
                "label", name,
                self._label_options.get(name, {}),
                {"color": color, "description": description, "exclusive": exclusive},
            )
            return self.labels[name]

        mcp = await self._server()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "name": name,
            "color": color,
            "exclusive": exclusive,
            "format": "json",
        }
        if description:
            kwargs["description"] = description

        result = await mcp.call_tool("gitea_issue_create_label", kwargs)
        if _is_error(result):
            msg = f"need_label({name!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.labels[name] = data
        self._label_options[name] = {
            "color": color, "description": description, "exclusive": exclusive,
        }
        return data

    async def need_milestone(
        self,
        title: str,
        *,
        description: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a milestone exists.  Creates if not cached.

        Raises:
            ConflictError: If a previous request for this milestone
                title used different *description* or *due_date*.
        """
        if title in self.milestones:
            check_conflict(
                "milestone", title,
                self._milestone_options.get(title, {}),
                {"description": description, "due_date": due_date},
            )
            return self.milestones[title]

        mcp = await self._server()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "title": title,
            "format": "json",
        }
        if description:
            kwargs["description"] = description
        if due_date:
            kwargs["due_date"] = due_date

        result = await mcp.call_tool("gitea_issue_create_milestone", kwargs)
        if _is_error(result):
            msg = f"need_milestone({title!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.milestones[title] = data
        self._milestone_options[title] = {
            "description": description, "due_date": due_date,
        }
        return data

    async def need_issue(
        self,
        title: str,
        *,
        body: str | None = None,
        labels: list[int | str] | None = None,
        milestone: int | None = None,
        assignees: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an issue and cache it by number.

        Issues are matched by *title* — if an issue with this title
        already exists in the cache, the request options (*body*,
        *labels*, *milestone*, *assignees*) must match.  If an
        issue exists on the Gitea instance (from a previous run),
        it is adopted into the cache.

        Raises:
            ConflictError: If a cached issue with the same title was
                created with different *body*, *labels*, *milestone*,
                or *assignees*.
        """
        # Check by title in cached issues
        for number, cached in self.issues.items():
            if cached.get("title") == title:
                check_conflict(
                    "issue", f"#{number} ({title!r})",
                    self._issue_options.get(number, {}),
                    {
                        "body": body, "labels": labels,
                        "milestone": milestone, "assignees": assignees,
                    },
                )
                return cached

        # Check if it exists in Gitea (created by a previous run)
        mcp = await self._server()
        list_result = await mcp.call_tool(
            "gitea_issue_list_issues",
            {"owner": self.owner, "repo": self.name, "format": "json"},
        )
        if not _is_error(list_result):
            try:
                text = extract_text_content(list_result.content)
                existing = json.loads(text)
                if isinstance(existing, list):
                    for item in existing:
                        if item.get("title") == title:
                            number = item["number"]
                            self.issues[number] = item
                            self._issue_options[number] = {
                                "body": body, "labels": labels,
                                "milestone": milestone, "assignees": assignees,
                            }
                            return cast("dict[str, Any]", item)
            except (json.JSONDecodeError, AssertionError):
                pass  # Create fresh

        # Create new issue
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
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
        if _is_error(result):
            msg = f"need_issue({title!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.issues[data["number"]] = data
        self._issue_options[data["number"]] = {
            "body": body, "labels": labels,
            "milestone": milestone, "assignees": assignees,
        }
        return data

    async def need_pull_request(
        self,
        title: str,
        *,
        head: str,
        base: str = "main",
        body: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a pull request exists, matching cached or remote state.

        Raises:
            ConflictError: If a cached PR with the same title was
                created with different *head*, *base*, or *body*.
        """
        for number, cached in self.pull_requests.items():
            if cached.get("title") == title:
                check_conflict(
                    "pull_request", f"#{number} ({title!r})",
                    self._pr_options.get(number, {}),
                    {"head": head, "base": base, "body": body},
                )
                return cached

        mcp = await self._server()
        listed = await mcp.call_tool(
            "gitea_repo_list_pull_requests",
            {
                "owner": self.owner,
                "repo": self.name,
                "state": "all",
                "format": "json",
            },
        )
        if not _is_error(listed):
            try:
                data = json.loads(extract_text_content(listed.content))
                if isinstance(data, list):
                    for item in data:
                        if item.get("title") == title:
                            number = item["number"]
                            self.pull_requests[number] = item
                            self._pr_options[number] = {
                                "head": head, "base": base, "body": body,
                            }
                            return cast("dict[str, Any]", item)
            except (json.JSONDecodeError, AssertionError):
                pass

        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "head": head,
            "base": base,
            "title": title,
            "format": "json",
        }
        if body:
            kwargs["body"] = body
        result = await mcp.call_tool("gitea_repo_create_pull_request", kwargs)
        if _is_error(result):
            msg = f"need_pull_request({title!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        _assert_keys(data, "number", "title", "state", "head", "base")
        _assert_content(data, title=title, state="open")
        self.pull_requests[data["number"]] = data
        self._pr_options[data["number"]] = {
            "head": head, "base": base, "body": body,
        }
        return data

    async def need_tag(
        self,
        name: str,
        *,
        target: str = "main",
        message: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a tag exists.  Creates if not cached.

        Note: the tool parameter is ``tag_name`` but the API response
        uses ``name`` — a known naming divergence.

        Raises:
            ConflictError: If a previous request for this tag name
                used different *target* or *message*.
        """
        if name in self.tags:
            check_conflict(
                "tag", name,
                self._tag_options.get(name, {}),
                {"target": target, "message": message},
            )
            return self.tags[name]

        mcp = await self._server()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "tag_name": name,
            "target": target,
            "format": "json",
        }
        if message:
            kwargs["message"] = message

        result = await mcp.call_tool("gitea_repo_create_tag", kwargs)
        if _is_error(result):
            msg = f"need_tag({name!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.tags[name] = data
        self._tag_options[name] = {"target": target, "message": message}
        return data


# =============================================================================
# World — server pool + lazy state graph
# =============================================================================


class World:
    """Test world: pooled MCP servers + lazy state graph.

    Created once per pytest worker (session-scoped fixture).  With
    ``asyncio_default_test_loop_scope = session``, tests assigned to that
    worker share one event loop and pooled server connections.

    Tests sharing one World execute sequentially. Under xdist, each worker
    gets its own World and namespace, so independent live stories can run in
    parallel without sharing Forgejo entities.

    Usage::

        async def test_X(world):
            workflow = Workflow(world)
            repo = await workflow.ensure_repo(...)  # creates + verifies
            mcp = await workflow.client(DEV, SCOPE_WRITE)  # pooled
            ...

        async def test_Y(world):
            workflow = Workflow(world)
            repo = await workflow.ensure_repo(...)  # verified graph node
            mcp = await workflow.client(DEV, SCOPE_WRITE)  # same server
            ...
    """

    def __init__(
        self, gitea_url: str, admin_token: str, server_args: list[str]
    ) -> None:
        self._url = gitea_url
        self._admin_token = admin_token
        self._server_args = server_args

        # ── State (survives across tests) ──────────────────────────────
        self._token_cache: dict[str, str] = {}
        self._users: dict[str, dict[str, Any]] = {}
        self._orgs: dict[str, dict[str, Any]] = {}
        self._teams: dict[str, dict[str, Any]] = {}
        self._repos: dict[str, tuple[RepoRequest, RepoState]] = {}
        self.dependency_graph = DependencyGraph()
        """Authoritative verified dependency graph for this worker's World."""
        self._bootstrapped: bool = False
        self.bootstrap_count: int = 0
        """Number of times ``start()`` has been called (metatest: must be 1)."""

        # ── Server pool (one process per token scope) ──────────────────
        self._servers: dict[str, ClientSession] = {}
        self._exit_stack = AsyncExitStack()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Bootstrap: create canonical users, org, team, verify tokens.

        Called once per session.  Every creation is also a verification
        — required keys, types, and content are asserted.  If bootstrap
        fails, all live tests fail (the test instance is broken).
        """
        if self._bootstrapped:
            return

        self.bootstrap_count += 1

        # ── Step 1: Start admin server ────────────────────────────────
        admin = await self.admin_server()

        # ── Step 2: Create canonical users ────────────────────────────
        for user in ALL_USERS:
            await self.need_user(user)

        # ── Step 3: Create org ────────────────────────────────────────
        await self.need_org(ORG_NAME,
                            full_name="Live Test Organization",
                            description="Bootstrap org for live integration tests")
        result = await admin.call_tool(
            "gitea_org_get", {"org": ORG_NAME, "format": "json"}
        )
        if _is_error(result):
            msg = f"Bootstrap: failed to read org '{ORG_NAME}': {_error_text(result)[:300]}"
            raise AssertionError(msg)
        org_data = _unwrap(result)
        _assert_keys(org_data,
            "id", "username", "name", "full_name", "description",
            "avatar_url", "location", "website", "visibility",
            "repo_admin_change_team_access",
        )
        _assert_key_types(org_data, id=int, username=str, name=str, visibility=str)
        _assert_content(org_data, username=ORG_NAME)

        # ── Step 4: Create team ───────────────────────────────────────
        team = await self.need_team(ORG_NAME, TEAM_NAME, permission="write",
                                    units_map={
                                        "repo.code": "write",
                                        "repo.issues": "write",
                                        "repo.pulls": "write",
                                    })
        assert "name" in team, (
            f"Bootstrap: team response missing 'name' key: {sorted(team.keys())}"
        )
        assert team["name"] == TEAM_NAME, (
            f"Bootstrap: expected team name {TEAM_NAME!r}, got {team.get('name')!r}"
        )

        # ── Step 5: Verify DEV user was created ─────────────────────────
        # The admin server can look up any user.  Token verification
        # happens naturally when server_for(DEV, SCOPE_WRITE) starts.
        result = await admin.call_tool(
            "gitea_user_get",
            {"username": DEV.username, "format": "json"},
        )
        if _is_error(result):
            msg = f"Bootstrap: failed to look up DEV user: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        current_data = _unwrap(result)
        _assert_keys(current_data, "id", "login", "username", "email")
        _assert_content(current_data, login=DEV.username, username=DEV.username)

        self._bootstrapped = True

    async def stop(self) -> None:
        """Close all pooled servers."""
        with suppress(Exception):
            await self._exit_stack.aclose()

    async def cleanup(self) -> None:
        """Delete repositories created by this World, best effort per repo.

        Cleanup runs while pooled servers are still alive.  Every repository
        is attempted even if an earlier deletion fails; the first failure is
        re-raised after the remaining repositories have been attempted.
        """
        from tests.live.helpers import purge_repo

        failures: list[tuple[str, BaseException]] = []
        for key, (_, repo) in self._repos.items():
            try:
                mcp = await self.server_for(repo._user, repo._scopes)
                await purge_repo(mcp, repo.owner, repo.name)
            except BaseException as exc:
                failures.append((key, exc))
        if failures:
            details = "; ".join(f"{key}: {exc}" for key, exc in failures)
            message = f"Live repository cleanup failed: {details}"
            raise RuntimeError(message)

    # ── Server pool ───────────────────────────────────────────────────

    async def admin_server(self) -> ClientSession:
        """Get (or start) the pooled admin MCP server."""
        key = "__admin__"
        if key not in self._servers:
            await self._start_server(key, self._admin_token)
        return self._servers[key]

    async def server_for(
        self, user: User, scopes: list[str]
    ) -> ClientSession:
        """Get (or start) a pooled MCP server for *user* with *scopes*.

        The server is keyed by the token string — two (user, scopes)
        pairs that produce the same token share the same server.
        """
        token = await self.token(user, scopes)
        key = f"__token__{token[:16]}"
        if key not in self._servers:
            await self._start_server(key, token)
        return self._servers[key]

    async def _start_server(
        self, key: str, token: str
    ) -> ClientSession:
        """Start an MCP server process over stdio and keep it alive."""
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=self._server_args[0],
            args=self._server_args[1:],
            env={
                **os.environ,
                "GITEA_URL": self._url,
                "GITEA_TOKEN": token,
                "TRANSPORT_TYPE": "stdio",
            },
        )

        read, write = await self._exit_stack.enter_async_context(
            stdio_client(params)
        )
        session: ClientSession = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        self._servers[key] = session
        return session

    # ── Token cache (shared across tests) ─────────────────────────────

    async def token(self, user: User, scopes: list[str]) -> str:
        """Get a cached token for *user* with *scopes*.  Mints on first call."""
        key = f"{user.username}:{','.join(sorted(scopes))}"
        if key in self._token_cache:
            return self._token_cache[key]
        from tests.live.helpers import create_user_token

        token = await create_user_token(
            self._url, user.username, user.password, "cached-ci", scopes
        )
        self._token_cache[key] = token
        return token

    # ── State graph: need_* — idempotent create-or-return ─────────────

    async def need_user(self, user: User) -> dict[str, Any]:
        """Ensure *user* exists on the Gitea instance. Idempotent.

        Uses the admin server to call ``admin_create_user``.  Handles
        the "already exists" case gracefully (user was created by a
        previous run on the throwaway test instance).
        """
        if user.username in self._users:
            return self._users[user.username]

        admin = await self.admin_server()
        result = await admin.call_tool("gitea_admin_create_user", {
            "username": user.username,
            "password": user.password,
            "email": user.email,
            "must_change_password": False,
            "format": "json",
        })
        if _is_error(result):
            text = _error_text(result)
            if "already exists" in text.lower():
                # Re-read and verify the pre-existing user
                entity = f"user {user.username}"
                verify = await admin.call_tool(
                    "gitea_user_get",
                    {"username": user.username, "format": "json"},
                )
                if _is_error(verify):
                    raise BootstrapVerificationError(
                        entity, "readable", True, False,
                    ) from None
                data = _unwrap(verify)
                _assert_keys(data, "id", "login", "username", "email", "active")
                _assert_content(
                    data, login=user.username, username=user.username,
                )
                if data.get("email") != user.email:
                    raise BootstrapVerificationError(
                        entity, "email", user.email, data.get("email"),
                    )
                if not data.get("active", True):
                    raise BootstrapVerificationError(
                        entity, "active", True, False,
                    )
                if data.get("prohibit_login", False):
                    raise BootstrapVerificationError(
                        entity, "prohibit_login", False, True,
                    )
                self._users[user.username] = data
                return data
            msg = f"Failed to create user '{user.username}': {text[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self._users[user.username] = data
        return data

    async def need_org(
        self,
        username: str,
        *,
        full_name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Ensure an organization exists. Idempotent."""
        if username in self._orgs:
            return self._orgs[username]

        admin = await self.admin_server()
        kwargs: dict[str, Any] = {"username": username, "format": "json"}
        if full_name:
            kwargs["full_name"] = full_name
        if description:
            kwargs["description"] = description

        result = await admin.call_tool("gitea_org_create", kwargs)
        if _is_error(result):
            text = _error_text(result)
            if "already exists" in text.lower():
                # Re-read and verify the pre-existing org
                entity = f"org {username}"
                verify = await admin.call_tool(
                    "gitea_org_get",
                    {"org": username, "format": "json"},
                )
                if _is_error(verify):
                    raise BootstrapVerificationError(
                        entity, "readable", True, False,
                    ) from None
                data = _unwrap(verify)
                _assert_keys(data, "id", "username", "visibility")
                _assert_content(data, username=username)
                if full_name is not None and data.get("full_name") != full_name:
                    raise BootstrapVerificationError(
                        entity, "full_name", full_name, data.get("full_name"),
                    )
                self._orgs[username] = data
                return data
            msg = f"Failed to create org '{username}': {text[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self._orgs[username] = data
        return data

    async def need_team(
        self,
        org: str,
        name: str,
        *,
        permission: str = "read",
        units_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Ensure a team exists within an organization. Idempotent."""
        key = f"{org}/{name}"
        if key in self._teams:
            return self._teams[key]

        admin = await self.admin_server()
        kwargs: dict[str, Any] = {
            "org": org,
            "name": name,
            "permission": permission,
            "format": "json",
        }
        if units_map is None:
            units_map = {
                "repo.code": "write",
                "repo.issues": "write",
                "repo.pulls": "write",
                "repo.releases": "write",
            }
        kwargs["units_map"] = units_map

        result = await admin.call_tool("gitea_org_create_team", kwargs)
        if _is_error(result):
            text = _error_text(result)
            if "already exists" in text.lower() or "conflict" in text.lower():
                # List teams in the org and find the pre-existing one
                entity = f"team {org}/{name}"
                list_result = await admin.call_tool(
                    "gitea_org_list_teams",
                    {"org": org, "format": "json"},
                )
                if _is_error(list_result):
                    raise BootstrapVerificationError(
                        entity, "listable", True, False,
                    ) from None
                teams_data = json.loads(
                    extract_text_content(list_result.content)
                )
                team_data: dict[str, Any] | None = None
                if isinstance(teams_data, list):
                    for item in teams_data:
                        if item.get("name") == name:
                            team_data = item
                            break
                if team_data is None:
                    raise BootstrapVerificationError(
                        entity, "found", True, False,
                    ) from None
                # Verify permission
                if team_data.get("permission") != permission:
                    raise BootstrapVerificationError(
                        entity, "permission",
                        permission, team_data.get("permission"),
                    )
                # Verify units_map entries
                required_units = units_map or {
                    "repo.code": "write",
                    "repo.issues": "write",
                    "repo.pulls": "write",
                    "repo.releases": "write",
                }
                actual_units = team_data.get("units_map", {})
                for unit_key, expected_level in required_units.items():
                    actual_level = actual_units.get(unit_key)
                    if actual_level != expected_level:
                        raise BootstrapVerificationError(
                            entity, f"units_map.{unit_key}",
                            expected_level, actual_level,
                        )
                self._teams[key] = team_data
                return team_data
            msg = f"Failed to create team '{name}' in '{org}': {text[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self._teams[key] = data
        return data

    async def need_repo(
        self,
        owner: str,
        name: str,
        *,
        user: User | None = None,
        scopes: list[str] | None = None,
        auto_init: bool = True,
        description: str | None = None,
        private: bool = False,
        branch: str | None = None,
        old_branch: str = "main",
        files: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> RepoState:
        """Ensure a repository exists and return its ``RepoState``.

        Idempotent — creates the repo on first call, returns cached
        ``RepoState`` on every subsequent call.  If *branch*, *files*,
        or *labels* are provided, those are ensured inside the repo
        (also idempotent).

        On a cache hit, the stored ``RepoRequest`` contract is checked
        against the new request.  A mismatch raises
        :class:`ConflictError` — the same repository identity cannot
        be materialised with different configuration within one World.

        Args:
            owner: Repository owner login.
            name: Repository name.
            user: The ``User`` who owns this repo.  Defaults to looking
                up *owner* in the canonical user set.
            scopes: Scopes for the owner's token.  Defaults to
                ``SCOPE_WRITE``.
            auto_init: Passed to ``gitea_create_current_user_repo``.
            description: Optional repo description.
            private: Whether the repo is private.
            branch: Optional branch to create after repo creation.
            old_branch: Source branch for *branch* creation (default "main").
            files: ``{path: content}`` to create on *branch*.
            labels: ``{name: color}`` labels to create.

        Returns:
            ``RepoState`` — the (cached or newly created) repo state.

        Raises:
            ConflictError: If a previous request for this ``owner/name``
                had different *auto_init*, *description*, *private*,
                *branch*, *old_branch*, *files*, or *labels*.
        """
        key = f"{owner}/{name}"

        # Build the request contract
        request = RepoRequest(
            owner=owner,
            name=name,
            auto_init=auto_init,
            description=description,
            private=private,
            branch=branch,
            old_branch=old_branch,
            files=tuple(sorted((files or {}).items())),
            labels=tuple(sorted((labels or {}).items())),
        )

        # Check cache — conflict on incompatible re-request
        if key in self._repos:
            stored_request, stored_state = self._repos[key]
            stored_request.assert_compatible(request)
            return stored_state

        # Resolve user and scopes
        _user: User | None = user
        if _user is None:
            for candidate in ALL_USERS:
                if candidate.username == owner:
                    _user = candidate
                    break
            if _user is None:
                msg = (
                    f"Owner '{owner}' is not a canonical test user. "
                    f"Pass ``user=User(...)`` explicitly."
                )
                raise ValueError(msg)
        _scopes = scopes if scopes is not None else SCOPE_WRITE

        # Ensure the user exists (fail-hard if creation fails)
        await self.need_user(_user)

        # Get a pooled server for this owner
        mcp = await self.server_for(_user, _scopes)

        # Purge before create — clean slate even after interrupted runs
        from tests.live.helpers import purge_repo

        await purge_repo(mcp, owner, name)

        # Create the repo
        kwargs: dict[str, Any] = {
            "name": name, "auto_init": auto_init,
            "private": private, "format": "json",
        }
        if description:
            kwargs["description"] = description

        result = await mcp.call_tool(
            "gitea_create_current_user_repo", kwargs
        )
        if _is_error(result):
            msg = f"need_repo({key!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        repo_data = _unwrap(result)

        state = RepoState(
            owner=owner, name=name, data=repo_data,
            _world=self, _user=_user, _scopes=_scopes,
        )
        self._repos[key] = (request, state)

        # Create branch + files + labels if requested
        if branch is not None:
            await state.need_branch(branch, old=old_branch)

        if files is not None:
            for path, content in files.items():
                await state.need_file(
                    path, content,
                    branch=branch if branch is not None else "main",
                )

        if labels is not None:
            for label_name, color in labels.items():
                await state.need_label(label_name, color)

        return state
