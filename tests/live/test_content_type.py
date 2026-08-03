"""Content-type handling: text input, base64 decode, binary metadata.

Verifies three new capabilities from #626:

1. ``content_type="text"`` on create/update file tools — agents pass
   plain text, the server base64-encodes before the Gitea API call.
2. ``gitea_repo_get_contents`` auto-decodes base64 content — agents
   receive plain text, not raw ContentsResponse JSON.
3. ``gitea_repo_get_archive`` does not crash on binary content — the
   server handles ``application/zip`` responses gracefully.
"""

from __future__ import annotations

import os

import pytest

from tests.live.assertions import (
    assert_content,
    assert_keys,
    assert_result_ok,
)
from tests.live.conftest import live_available
from tests.live.workflows import Workflow
from tests.live.world import DEV, SCOPE_WRITE, World

_REPO_BASE = "live-content-type"
# Use unique suffix per worker to avoid collisions in parallel runs.
# xdist sets PYTEST_XDIST_WORKER; fall back to pid for sequential runs.
_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", str(os.getpid()))[-8:]
_REPO = f"{_REPO_BASE}-{_WORKER_ID}"
_FILE = "test-ct-output.txt"
_FILE_DECODE = "test-ct-decode.txt"
_FILE_DECODE_RAW = "test-ct-decode-raw.txt"
_FILE_COMPAT = "backward-compat.txt"
_CONTENT = "Hello from content_type=text param\nLine two.\n"


# ---------------------------------------------------------------------------
# Shared repo bootstrap — reuse across test classes
# ---------------------------------------------------------------------------

async def _ensure_repo_with_text_file(world: World) -> None:
    """Create repo via the World graph.  Returns ``None``.

    Uses ``Workflow.ensure_repo`` which goes through the World's
    dependency graph for idempotent bootstrap.
    """
    workflow = Workflow(world)
    # This creates user, repo, and caches — subsequent calls reuse.
    await workflow.ensure_repo(
        DEV.username, _REPO, user=DEV, scopes=SCOPE_WRITE,
        auto_init=True, description="content_type test repo",
    )


# ---------------------------------------------------------------------------
# content_type="text" — agent passes plain text, server encodes
# ---------------------------------------------------------------------------


@live_available
class TestContentTypeText:
    """Tests for the ``content_type`` param on create/update file tools."""

    @pytest.mark.live
    async def test_create_file_with_content_type_text(self, world: World) -> None:
        """Create a file with ``content_type="text"`` — plain text, no base64.

        The ``content_type`` param tells the server to encode ``content``
        before the API call.  Response is FileResponse (commit + content).
        """
        await _ensure_repo_with_text_file(world)
        mcp = await world.server_for(DEV, SCOPE_WRITE)

        result = await mcp.call_tool("gitea_repo_create_file", {
            "owner": DEV.username,
            "repo": _REPO,
            "filepath": _FILE,
            "content": _CONTENT,
            "content_type": "text",
            "message": "test content_type=text param",
            "format": "json",
        })
        data = assert_result_ok(result)
        assert_keys(data, "commit", "content")
        assert_content(data["content"], name=_FILE)

    @pytest.mark.live
    async def test_create_file_defaults_to_base64(self, world: World) -> None:
        """Default ``content_type="base64"`` — backward compatible.

        When ``content_type`` is omitted, ``content`` must be base64-encoded
        by the caller.  The server passes it through unchanged.
        """
        await _ensure_repo_with_text_file(world)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        import base64
        encoded = base64.b64encode(b"backward-compat test\n").decode()

        result = await mcp.call_tool("gitea_repo_create_file", {
            "owner": DEV.username,
            "repo": _REPO,
            "filepath": _FILE_COMPAT,
            "content": encoded,
            "message": "test default base64",
            "format": "json",
        })
        data = assert_result_ok(result)
        assert_content(data["content"], name=_FILE_COMPAT)


# ---------------------------------------------------------------------------
# base64 decode — get_contents returns plain text
# ---------------------------------------------------------------------------


@live_available
class TestBase64Decode:
    """Tests for auto-decoding of base64 ContentResponse on get_contents."""

    @pytest.mark.live
    async def test_get_contents_returns_text_not_json(self, world: World) -> None:
        """Read a file with get_contents — expect decoded text, not JSON.

        The server detects ContentsResponse endpoints (schema has both
        ``encoding`` and ``content`` properties) and auto-decodes the
        base64 ``content`` field.  Agents should receive a plain string
        matching the original file content.
        """
        await _ensure_repo_with_text_file(world)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        # Ensure the test file exists
        result = await mcp.call_tool("gitea_repo_create_file", {
            "owner": DEV.username, "repo": _REPO,
            "filepath": _FILE_DECODE, "content": _CONTENT,
            "content_type": "text",
            "message": "file for decode test",
            "format": "json",
        })
        assert_result_ok(result)

        result = await mcp.call_tool("gitea_repo_get_contents", {
            "owner": DEV.username,
            "repo": _REPO,
            "filepath": _FILE_DECODE,
            "format": "json",
        })
        assert not result.isError, (
            f"Tool call failed: {result.content}"
        )
        # After base64 decode: plain text in structuredContent.result
        data = result.structuredContent.get("result") if result.structuredContent else None
        assert isinstance(data, str), (
            f"Expected decoded plain text (str), "
            f"got {type(data).__name__}: {data!r}"
        )
        assert data == _CONTENT, (
            f"Decoded content mismatch.\n"
            f"Expected: {_CONTENT!r}\n"
            f"Got:      {data!r}"
        )

    @pytest.mark.live
    async def test_get_contents_raw_format_is_text(self, world: World) -> None:
        """``format='raw'`` returns decoded text directly (no JSON wrapper).

        The base64 decode runs in the pipeline, which applies before format
        handling.  ``raw`` bypasses JSON/Markdown formatting but not content
        transformation — the decoded text is what agents receive.
        """
        await _ensure_repo_with_text_file(world)
        mcp = await world.server_for(DEV, SCOPE_WRITE)
        # Ensure the test file exists
        result = await mcp.call_tool("gitea_repo_create_file", {
            "owner": DEV.username, "repo": _REPO,
            "filepath": _FILE_DECODE_RAW, "content": _CONTENT,
            "content_type": "text",
            "message": "file for raw test",
            "format": "json",
        })
        assert_result_ok(result)

        result = await mcp.call_tool("gitea_repo_get_contents", {
            "owner": DEV.username,
            "repo": _REPO,
            "filepath": _FILE_DECODE_RAW,
            "format": "raw",
        })
        # Raw format returns decoded text directly
        assert not result.isError, f"Tool call failed: {result.content}"
        data = result.structuredContent.get("result") if result.structuredContent else None
        assert isinstance(data, str), (
            f"Raw format: expected decoded text, got {type(data).__name__}: {data!r}"
        )
        assert data == _CONTENT


# ---------------------------------------------------------------------------
# Binary response — archive returns metadata, not crash
# ---------------------------------------------------------------------------


@live_available
class TestBinaryArchive:
    """Tests for binary response handling on archive endpoints."""

    @pytest.mark.live
    async def test_archive_raw_format_works(self, world: World) -> None:
        """``format='raw'`` on archive fetches without crashing.

        Binary content (application/zip) must not crash the server with
        a UnicodeDecodeError.  With ``format='raw'``, the response passes
        through directly — the agent is responsible for handling the bytes.
        """
        await _ensure_repo_with_text_file(world)
        mcp = await world.server_for(DEV, SCOPE_WRITE)

        result = await mcp.call_tool("gitea_repo_get_archive", {
            "owner": DEV.username,
            "repo": _REPO,
            "archive": "main.zip",
            "format": "raw",
        })
        # raw format should not crash — it bypasses formatting
        assert not result.isError, (
            f"Raw archive fetch failed: {result.content}"
        )
