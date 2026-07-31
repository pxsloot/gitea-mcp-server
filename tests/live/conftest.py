"""Live integration test fixtures — require a real Gitea/Forgejo instance.

Design decisions
----------------

Two server-access patterns are available:

1. **``mcp_client`` context manager** (function-scoped, backward-compatible):
   Each test spawns its own MCP server process.

2. **``world`` fixture** (session-scoped):  A ``World`` object with pooled
   MCP servers and lazy state graph.  With ``asyncio_default_test_loop_scope
   = session``, all tests share one event loop, so server connections live
   across all modules.  ``need_repo`` creates the repo once and returns
   the cached ``RepoState`` instantly on subsequent calls.

Prefer the ``world`` fixture for new and rewritten tests.
The suite runs sequentially (no xdist) — one World serves all modules.

Fixtures provided
-----------------
- ``gitea_url`` (session): the Gitea instance URL
- ``admin_token`` (session): the admin bearer token from ``.env.dev.local``
- ``server_args`` (session): command + args to start the MCP server over stdio
- ``world`` (session): a ``World`` with pooled servers and lazy state graph
- ``mcp_client`` (async context manager): spawn a server, yield a session
"""

from __future__ import annotations

import asyncio
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
# World fixture — session-scoped, pooled servers across all modules
# ---------------------------------------------------------------------------
# With ``asyncio_default_test_loop_scope = session``, all tests share
# one event loop.  The World's server connections are created once and
# survive across all modules.  State (users, orgs, repos, tokens) is
# also cached across the entire session.


@pytest.fixture(scope="session")
async def world(
    gitea_url: str, admin_token: str, server_args: list[str],
) -> AsyncIterator[World]:
    """Session-scoped World with pooled MCP servers and lazy state graph.

    One World per test session.  Bootstraps canonical users, org, and
    team on first use.  Pooled servers (one per token scope) stay alive
    for all tests across all modules.
    """
    from tests.live.world import World

    if not _gitea_reachable():
        pytest.skip("Live Gitea instance not available.")

    w = World(gitea_url, admin_token, server_args)

    logger.info("World — bootstrapping users, org, team (per session)")
    await w.start()
    logger.info("World ready — %d users, %d orgs bootstrapped",
                 len(w._users), len(w._orgs))

    yield w

    logger.info("World — closing pooled servers")
    await w.stop()


# ---------------------------------------------------------------------------
# Async leak detection — guard against session-scoped loop pollution
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _detect_async_leaks(request: pytest.FixtureRequest) -> Any:
    """Fail if a live test leaves user-created asyncio tasks behind.

    With ``asyncio_default_test_loop_scope = "session"``, all live
    tests share one event loop.  A leaked task from test A can cause
    mysterious failures in test B twenty tests later.

    Framework-internal tasks (``Task-N`` default names, ``mcp.*``
    library names) and World server pool tasks (``world-server-*``)
    are skipped.  Only tasks with explicit user-set names trigger a
    failure.
    """
    loop = asyncio.get_running_loop()
    pre = asyncio.all_tasks(loop)
    yield
    post = asyncio.all_tasks(loop)
    leaked = [
        t for t in post - pre
        if not t.done()
        and not t.get_name().startswith("Task-")       # framework default
        and not t.get_name().startswith("world-server-")  # server pool
        and not t.get_name().startswith("mcp.")         # MCP library internals
    ]
    if leaked:
        names = ", ".join(t.get_name() for t in leaked)
        pytest.fail(
            f"Leaked {len(leaked)} user task(s) after "
            f"{request.node.name}: {names}"
        )


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

    For new tests, prefer the ``world`` fixture — pooled servers stay
    alive for the entire session.
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
