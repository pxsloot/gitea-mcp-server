"""Live integration test fixtures — require a real Gitea/Forgejo instance.

Design decisions
----------------

Two server-access patterns are available:

1. **``mcp_client`` context manager** (function-scoped, backward-compatible):
   Each test spawns its own MCP server process.  Server startup (fetch
   spec, convert, apply scope filtering) IS part of the test.  The cost
    is runtime -- ~1.5-2.5s per test.

2. **``world`` fixture** (function-scoped, backed by module-level state
   cache):  A ``World`` object that pools MCP server connections within
   a single test's event loop and caches state (users, repos, labels,
   etc.) across tests.  ``need_repo`` creates the repo once and returns
   the cached ``RepoState`` instantly on subsequent calls — setup is
   declarative and free after the first test.

   Server connections are per-test (loop-bound — ``asyncio`` connections
   can't be shared across ``function``-scoped test loops).  The speed
   win comes from eliminating redundant setup tool calls, not from
   cross-test connection pooling.

Prefer the ``world`` fixture for new and rewritten tests.  The
``mcp_client`` context manager remains for tests that need a completely
fresh server (``test_errors.py``).

Fixtures provided
-----------------
- ``gitea_url`` (session): the Gitea instance URL
- ``admin_token`` (session): the admin bearer token from ``.env.dev.local``
- ``server_args`` (session): command + args to start the MCP server over stdio
- ``world`` (function): a ``World`` with lazy state graph + fresh server per test
- ``mcp_client`` (async context manager): spawn a server, yield a session
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.live.world import World

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load credentials
# ---------------------------------------------------------------------------

_env_path = Path(".env.dev.local")
if _env_path.exists():
    load_dotenv(_env_path, override=True)

LIVE_URL: str | None = os.getenv("GITEA_URL")
LIVE_TOKEN: str | None = os.getenv("GITEA_TOKEN")

# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------


def _gitea_reachable() -> bool:
    if not LIVE_URL or not LIVE_TOKEN:
        return False
    try:
        r = httpx.get(
            f"{LIVE_URL}/api/v1/user",
            headers={"Authorization": f"token {LIVE_TOKEN}"},
            timeout=5,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
        return False
    else:
        return r.status_code == 200


live_available = pytest.mark.skipif(
    not _gitea_reachable(),
    reason=(
        "Live Gitea instance not available. "
        "Start one with `./gitea_dev_start.sh` and ensure "
        ".env.dev.local is present."
    ),
)

# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gitea_url() -> str:
    assert LIVE_URL is not None, "GITEA_URL not set in .env.dev.local"
    return LIVE_URL


@pytest.fixture(scope="session")
def admin_token() -> str:
    assert LIVE_TOKEN is not None, "GITEA_TOKEN not set in .env.dev.local"
    return LIVE_TOKEN


@pytest.fixture(scope="session")
def server_args() -> list[str]:
    bin_path = shutil.which("gitea-mcp")
    if bin_path:
        return [bin_path]
    return [sys.executable, "-m", "gitea_mcp_server"]


# ---------------------------------------------------------------------------
# World fixture — function-scoped, backed by module-level state cache
# ---------------------------------------------------------------------------
# Design note: ``asyncio_default_test_loop_scope=function`` means each
# test runs in its own event loop.  Server connections (stdio subprocess
# + ClientSession) are loop-bound and can't be shared across test loops.
# But the World's *state* (users, repos, tokens, labels) is pure Python
# data — we cache it in module-level storage so ``need_repo``, ``need_*``
# return instantly on subsequent calls.
#
# Each test gets a fresh World instance with its own server connection,
# but the state is shared.  Setup becomes declarative and free after the
# first test that creates a given state node.

# Module-level state cache — survives across test function event loops.
_world_state: World | None = None
_world_bootstrapped: bool = False


@pytest.fixture
async def world(
    gitea_url: str, admin_token: str, server_args: list[str],
) -> AsyncIterator[World]:
    """Function-scoped World with lazy state graph.

    The underlying state (users, orgs, repos, tokens) is cached at module
    level — created once and shared across all tests in this module.
    Each test gets its own server connection via ``_begin_session()``.

    Bootstrap (user/org/team creation + token minting) runs once on the
    first call.  Subsequent tests skip this — the state is already in
    the cache.
    """
    global _world_state, _world_bootstrapped  # noqa: PLW0603

    from tests.live.world import World

    if _world_state is None:
        _world_state = World(gitea_url, admin_token, server_args)

    w = _world_state

    # Bootstrap once — users, orgs, teams, token round-trip
    if not _world_bootstrapped:
        if not _gitea_reachable():
            pytest.skip("Live Gitea instance not available.")

        logger.info("World — bootstrapping users, org, team (once per module)")
        await w.start()
        _world_bootstrapped = True
        logger.info("World ready — %d users, %d orgs bootstrapped",
                     len(w._users), len(w._orgs))

    # Begin per-test session — fresh server connections in this loop
    w._begin_session()
    try:
        yield w
    finally:
        await w._end_session()


# ---------------------------------------------------------------------------
# MCP client context manager (backward-compatible, per-test server)
# ---------------------------------------------------------------------------


@contextmanager
def _suppress_anyio_cleanup() -> Any:
    """Context manager that catches ``anyio`` cleanup errors on teardown.

    ``stdio_client`` uses ``anyio.TaskGroup`` internally.  When a test
    fails and the context manager unwinds, the task-group cleanup may
    raise ``BaseExceptionGroup`` with "cancel scope entered in different
    task" errors.  These are harmless — they happen because
    ``pytest-asyncio`` may run the teardown in a different task than the
    setup.  This helper suppresses them.
    """
    try:
        yield
    except BaseExceptionGroup as beg:
        # Filter out anyio cancel-scope errors, re-raise everything else
        others = [e for e in beg.exceptions
                  if "Attempted to exit cancel scope" not in str(e)]
        if others:
            msg = f"{len(others)} non-cleanup exception(s)"
            raise BaseExceptionGroup(msg, others)


@asynccontextmanager
async def mcp_client(
    gitea_url: str,
    server_args: list[str],
    token: str,
) -> AsyncIterator[ClientSession]:
    """Async context manager: start an MCP server, yield a connected session.

    Use this directly inside test functions::

        async with mcp_client(gitea_url, server_args, token) as mcp:
            result = await mcp.call_tool("gitea_user_get_current", {})

    The context manager handles cleanup gracefully even when the test
    inside it raises an exception — anyio cancel-scope noise on teardown
    is suppressed.

    For new tests, prefer the ``world`` fixture — state is cached across
    tests and setup is declarative.
    """
    with _suppress_anyio_cleanup():
        async with stdio_client(
            StdioServerParameters(
                command=server_args[0],
                args=server_args[1:],
                env={
                    **os.environ,
                    "GITEA_URL": gitea_url,
                    "GITEA_TOKEN": token,
                    "TRANSPORT_TYPE": "stdio",
                },
            )
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
