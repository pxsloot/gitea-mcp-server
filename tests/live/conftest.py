"""Live integration test fixtures — require a real Gitea instance.

These tests connect to a real Gitea/Forgejo server, create test data
(repos, branches, PRs), run MCP tool calls over stdio, and clean up.

Environment setup
-----------------
- ``.env.dev.local`` (auto-loaded, written by ``gitea_dev_start.sh``)
  or environment variables: ``GITEA_URL``, ``GITEA_TOKEN``,
  ``GITEA_ADMIN_USER`` (default ``admin-user``).

Usage::

    # Start a test Gitea instance (first time or from scratch)
    ./gitea_dev_start.sh

    # Run live tests
    uv run pytest tests/live/ -v

Each test in this directory is marked with ``pytest.mark.live`` and
will **skip** if no reachable Gitea instance is found.
"""

from __future__ import annotations

import base64
import os
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

from tests.conftest import SimpleConfig

# ---------------------------------------------------------------------------
# Load credentials from .env.dev.local (written by gitea_dev_start.sh)
# ---------------------------------------------------------------------------

_env_path = Path(".env.dev.local")
if _env_path.exists():
    # override=True ensures .env.dev.local takes precedence over env vars
    # that may have been set by the user's shell/IDE (e.g. pointing to a
    # production instance).  The dev-local file is written by
    # ``gitea_dev_start.sh`` and is the authoritative source for live tests.
    load_dotenv(_env_path, override=True)

LIVE_URL: str | None = os.getenv("GITEA_URL")
LIVE_TOKEN: str | None = os.getenv("GITEA_TOKEN")
_LIVE_ADMIN: str = os.getenv("GITEA_ADMIN_USER", "admin-user")

# ---------------------------------------------------------------------------
# Connectivity check (evaluated at collection time)
# ---------------------------------------------------------------------------


def _gitea_reachable() -> bool:
    """Return True if the Gitea instance is reachable with a valid token."""
    if not LIVE_URL or not LIVE_TOKEN:
        return False
    try:
        r = httpx.get(
            f"{LIVE_URL}/api/v1/user",
            headers={"Authorization": f"token {LIVE_TOKEN}"},
            timeout=5,
        )
        return r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
        return False


live_available = pytest.mark.skipif(
    not _gitea_reachable(),
    reason=(
        "Live Gitea instance not available. "
        "Start one with `./gitea_dev_start.sh` and ensure "
        ".env.dev.local is present."
    ),
)

# ---------------------------------------------------------------------------
# Configuration fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_config() -> SimpleConfig:
    """Configuration pointing at a real Gitea instance.

    Loads URL and token from ``.env.dev.local`` (preferred) or environment
    variables ``GITEA_URL`` and ``GITEA_TOKEN``.
    """
    return SimpleConfig(
        url=str(LIVE_URL),
        token=str(LIVE_TOKEN),
        log_level="ERROR",
    )


# ---------------------------------------------------------------------------
# Server binary discovery
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server_args() -> list[str]:
    """Command and args to start the MCP server over stdio.

    Uses the ``gitea-mcp`` console script entry point (defined in
    ``pyproject.toml`` as ``gitea_mcp_server.server:main``).
    Falls back to ``python -m gitea_mcp_server`` for compatibility.
    """
    bin_path = shutil.which("gitea-mcp")
    if bin_path:
        return [bin_path]
    # Fallback: try python -m (may not work if no __main__.py exists)
    return [sys.executable, "-m", "gitea_mcp_server"]


# ---------------------------------------------------------------------------
# Test data lifecycle — create user + repo + branch + PR, then clean up
# ---------------------------------------------------------------------------

# Use a fixed suffix so parallel workers don't collide.
_LIVE_SUFFIX: str = os.getenv("PYTEST_XDIST_WORKER", "local")
_LIVE_OWNER: str = f"bot-{_LIVE_SUFFIX}"
_LIVE_OWNER_EMAIL: str = f"bot-{_LIVE_SUFFIX}@localhost.local"
_LIVE_OWNER_PASS: str = "bot-pass"
_LIVE_REPO: str = f"live-test-{_LIVE_SUFFIX}"
_LIVE_BRANCH: str = "feature/test-content"


def _create_test_data() -> tuple[int, str, str, str]:
    """Create user, repo, branch-with-content, and PR via the admin REST API.

    Returns ``(pr_number, owner, repo, branch)`` so tests can use them
    directly without worrying about test data naming.
    """
    headers = {"Authorization": f"token {LIVE_TOKEN}"}
    api = httpx.Client(base_url=str(LIVE_URL), headers=headers, timeout=15)

    try:
        # Create the test user if it doesn't exist yet.
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

        # Create the repo with auto_init (has an initial commit on main).
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

        # Create a file on a new branch to produce a diff.
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

        # Create the pull request.
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
    """Delete the test repo (removes branches, PRs, everything)."""
    headers = {"Authorization": f"token {LIVE_TOKEN}", "sudo": _LIVE_OWNER}
    with httpx.Client(base_url=str(LIVE_URL), headers=headers, timeout=15) as api:
        api.delete(f"/api/v1/repos/{_LIVE_OWNER}/{_LIVE_REPO}")


@pytest.fixture(scope="module")
def live_test_data() -> tuple[int, str, str, str]:
    """Create and tear down test data for a live integration test session.

    Returns ``(pr_number, owner, repo, branch)``.

    The fixture is module-scoped so all tests in one file share the same
    test data — creating a PR is expensive.
    """
    data = _create_test_data()
    yield data
    _destroy_test_data()
