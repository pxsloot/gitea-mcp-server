"""Shared test-world — identities, server pool, and lazy state graph.

This module serves two roles:

1. **Canonical identities** (backward-compatible) — ``User``, ``DEV``,
   ``PEER``, ``RO``, ``LIMITED``, scope constants, org/team names.
   Defined in ``identities.py`` and re-exported here for existing imports.

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
- **Repositories, teams, orgs, and users are cleaned up at World teardown.**
  ``purge_repo`` also runs before creation so interrupted runs start cleanly.
  Only entities recorded in the ``OwnershipLedger`` as run-created are
  deleted; pre-existing entities are preserved.  Token cleanup is an
  accepted limitation (token IDs are not tracked).

Module structure
----------------
Identities and repository state live in separate modules to keep this
file focused on the ``World`` orchestration facade:

- ``tests/live/identities.py`` — ``User``, ``DEV``, ``PEER``, …,
  scope constants, org/team names
- ``tests/live/state.py`` — ``RepoState``, internal helpers
- ``tests/live/conflict.py`` — ``ConflictError``, ``RepoRequest``,
  ``check_conflict``
"""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack, suppress
from typing import TYPE_CHECKING, Any

from tests.helpers.mcp_results import extract_text_content
from tests.live.conflict import BootstrapVerificationError, RepoRequest
from tests.live.dependency_graph import DependencyGraph
from tests.live.identities import (  # noqa: F401 — re-exported
    _NAMESPACE,
    _RUN_ID,
    _WORKER,
    ALL_USERS,
    DEV,
    LIMITED,
    ORG_NAME,
    PEER,
    RO,
    SCOPE_LIMITED,
    SCOPE_READ,
    SCOPE_WRITE,
    TEAM_NAME,
    User,
)
from tests.live.state import (
    RepoState,
    _assert_content,
    _assert_key_types,
    _assert_keys,
    _error_text,
    _is_error,
    _unwrap,
)

if TYPE_CHECKING:
    from mcp import ClientSession

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
# Ownership ledger — tracks which entities this World run created
# =============================================================================


class OwnershipLedger:
    """Tracks entities created by the current live-test run.

    Only entities recorded via ``record()`` are eligible for teardown
    cleanup.  Pre-existing entities discovered via "already exists" are
    never deleted — only run-created ones.

    The ledger stores ``(identifier, delete_key)`` pairs keyed by entity
    type, where *delete_key* is the value needed by the corresponding
    delete tool (team ID, org name, username, etc.).

    Tokens are not tracked — Gitea requires the token ID (not the sha1
    value stored in the cache), and matching by name is unreliable.
    Token cleanup is an accepted limitation.
    """

    def __init__(self) -> None:
        self._owned: dict[str, list[tuple[str, str]]] = {}

    def record(
        self, entity_type: str, identifier: str, delete_key: str,
    ) -> None:
        """Record that this run created an entity."""
        self._owned.setdefault(entity_type, []).append(
            (identifier, delete_key)
        )

    def owned(
        self, entity_type: str,
    ) -> list[tuple[str, str]]:
        """Return ``[(identifier, delete_key), ...]`` for *entity_type*."""
        return list(self._owned.get(entity_type, []))

    def __bool__(self) -> bool:
        return bool(self._owned)


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
        self.ledger = OwnershipLedger()
        """Records entities created by this run for cleanup."""
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
        """Delete run-owned entities in reverse dependency order.

        Cleanup runs while pooled servers are still alive.  Each entity
        type is attempted independently; the first failure from each
        phase is preserved and re-raised after all phases complete.

        Order: repos → teams → orgs → users.

        Tokens are not cleaned up (we don't store token IDs), but they
        are scoped and removed when their owner user is deleted.
        """
        failures: list[tuple[str, BaseException]] = []

        # ── Phase 1: Repositories ───────────────────────────────────
        await self._delete_owned(
            "repo", "gitea_repo_delete",
            id_key="repo",  # repo identifier is "owner/name" → owner, repo
            failures=failures,
        )

        # ── Phase 2: Teams ──────────────────────────────────────────
        await self._delete_owned(
            "team", "gitea_org_delete_team",
            id_key="team_id",
            failures=failures,
        )

        # ── Phase 3: Organizations ──────────────────────────────────
        await self._delete_owned(
            "org", "gitea_org_delete",
            id_key="org",
            failures=failures,
        )

        # ── Phase 4: Users ──────────────────────────────────────────
        await self._delete_owned(
            "user", "gitea_admin_delete_user",
            id_key="username",
            failures=failures,
        )

        if failures:
            details = "; ".join(f"{key}: {exc}" for key, exc in failures)
            message = f"Live cleanup failed: {details}"
            raise RuntimeError(message)

    async def _delete_owned(
        self,
        entity_type: str,
        tool_name: str,
        id_key: str,
        failures: list[tuple[str, BaseException]],
    ) -> None:
        """Delete all owned entities of *entity_type* via *tool_name*.

        Each deletion is attempted independently.  Failures are
        collected in *failures*; the method does not raise.
        """
        owned = self.ledger.owned(entity_type)
        if not owned:
            return

        admin = await self.admin_server()
        for identifier, delete_key in owned:
            if not delete_key:
                continue
            try:
                kwargs = self._delete_args(entity_type, identifier, delete_key)
                result = await admin.call_tool(tool_name, kwargs)
                if not _is_error(result):
                    continue
                # 404 / "not found" → already deleted, not a failure
                text = _error_text(result)
                if "not found" in text.lower() or "404" in text:
                    continue
                failures.append(
                    (identifier, RuntimeError(text[:200]))
                )
            except BaseException as exc:
                failures.append((identifier, exc))

    def _delete_args(
        self, entity_type: str, identifier: str, delete_key: str,
    ) -> dict[str, Any]:
        """Build tool arguments for deleting an owned entity."""
        if entity_type == "repo":
            owner, _, repo = identifier.partition("/")
            return {"owner": owner, "repo": repo, "format": "json"}
        if entity_type == "team":
            return {"id": int(delete_key), "format": "json"}
        if entity_type == "org":
            return {"org": delete_key, "format": "json"}
        if entity_type == "user":
            return {"username": delete_key, "format": "json"}
        return {"format": "json"}

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
        self.ledger.record("user", user.username, user.username)
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
        self.ledger.record("org", username, username)
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
        self.ledger.record("team", key, str(data.get("id", "")))
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
        self.ledger.record("repo", key, key)

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
