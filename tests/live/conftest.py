"""Live integration test fixtures — require a real Gitea/Forgejo instance.

Design decisions
----------------

Each test spawns its own MCP server process (``mcp_client`` is a function-scoped
context manager, not a session fixture).  This is deliberate: server startup
(fetch spec, convert, apply scope filtering) IS part of the test.  A session-
scoped factory would skip that path.  The cost is runtime.

Tests within an act (file) are sequential.  Act I creates users; later acts
reference them.  ``--dist loadscope`` keeps module tests in the same worker.
State between sequential tests within a class uses ``pytest.*`` attributes
(see test_admin.py, test_issues_prs.py).  This is safe because those tests
are always collected and run together.

Every test creates its own token via ``create_user_token()`` (the one httpx
call in helpers.py).  This exercises the token-creation path and keeps tests
independent.  Token creation is cheap and the test instance is ephemeral.

Only repos are cleaned up (they accumulate on disk).  Users, orgs, and tokens
live on a throwaway instance wiped between sessions.

Fixtures provided
-----------------
- ``gitea_url`` (session): the Gitea instance URL
- ``admin_token`` (session): the admin bearer token from ``.env.dev.local``
- ``server_args`` (session): command + args to start the MCP server over stdio

The ``mcp_client`` async context manager starts a server process and connects
to it.  Use it directly in test bodies.
"""

from __future__ import annotations

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
    from collections.abc import AsyncIterator, Generator

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
# MCP client context manager
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


# ---------------------------------------------------------------------------
# Backward-compat fixtures (used by test_diff_endpoint.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_config(gitea_url: str, admin_token: str) -> Any:
    from tests.conftest import SimpleConfig

    return SimpleConfig(url=gitea_url, token=admin_token, log_level="ERROR")


_LIVE_SUFFIX: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_LIVE_OWNER: str = f"bot-{_LIVE_SUFFIX}"
_LIVE_OWNER_EMAIL: str = f"bot-{_LIVE_SUFFIX}@localhost.local"
_LIVE_OWNER_PASS: str = "bot-pass"
_LIVE_REPO: str = f"live-test-{_LIVE_SUFFIX}"
_LIVE_BRANCH: str = "feature/test-content"


def _create_test_data() -> tuple[int, str, str, str]:
    """Create test user + repo + PR for test_diff_endpoint.py (legacy pattern)."""
    import base64

    headers = {"Authorization": f"token {LIVE_TOKEN}"}
    api = httpx.Client(base_url=str(LIVE_URL), headers=headers, timeout=15)
    try:
        r = api.get(f"/api/v1/users/{_LIVE_OWNER}")
        if r.status_code == 404:
            r = api.post(
                "/api/v1/admin/users",
                json={
                    "username": _LIVE_OWNER,
                    "email": _LIVE_OWNER_EMAIL,
                    "password": _LIVE_OWNER_PASS,
                    "must_change_password": False,
                },
            )
            r.raise_for_status()
        sudo = {**headers, "sudo": _LIVE_OWNER}
        r = api.get(f"/api/v1/repos/{_LIVE_OWNER}/{_LIVE_REPO}")
        if r.status_code == 404:
            r = api.post(
                "/api/v1/user/repos",
                json={
                    "name": _LIVE_REPO,
                    "auto_init": True,
                    "private": False,
                    "description": "Live integration test repository",
                },
                headers=sudo,
            )
            r.raise_for_status()
        content_b64: str = base64.b64encode(
            b"## Test content\n\nCreated by live integration tests.\n"
        ).decode()
        r = api.post(
            f"/api/v1/repos/{_LIVE_OWNER}/{_LIVE_REPO}/contents/test-content.md",
            json={
                "content": content_b64,
                "message": "Add test content for live integration tests",
                "branch": "main",
                "new_branch": _LIVE_BRANCH,
            },
            headers=sudo,
        )
        r.raise_for_status()
        r = api.post(
            f"/api/v1/repos/{_LIVE_OWNER}/{_LIVE_REPO}/pulls",
            json={
                "base": "main",
                "head": _LIVE_BRANCH,
                "title": "Test PR for live integration tests",
                "body": "Created by the live integration test fixture.",
            },
            headers=sudo,
        )
        r.raise_for_status()
        pr_number: int = r.json()["number"]
        return pr_number, _LIVE_OWNER, _LIVE_REPO, _LIVE_BRANCH
    finally:
        api.close()


def _destroy_test_data() -> None:
    """Clean up test repo created by _create_test_data (legacy pattern)."""
    headers = {"Authorization": f"token {LIVE_TOKEN}", "sudo": _LIVE_OWNER}
    with httpx.Client(base_url=str(LIVE_URL), headers=headers, timeout=15) as api:
        api.delete(f"/api/v1/repos/{_LIVE_OWNER}/{_LIVE_REPO}")


@pytest.fixture(scope="module")
def live_test_data() -> Generator[tuple[int, str, str, str], None, None]:
    data = _create_test_data()
    yield data
    _destroy_test_data()
