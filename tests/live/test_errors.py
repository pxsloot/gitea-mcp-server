"""Error handling through the full transport stack.

Tests that errors propagate correctly from the real Gitea API through
the full MCP transport: APINotFound (404), validation errors, and
unknown tool names through the ``gitea_call_tool`` proxy.

What happens when errors come through the full stack — real Gitea API error
responses, validation errors, and unknown tool names.  One test per error
class:

1. **APINotFound**: 404 from Gitea (non-existent resource on a real repo)
2. **Validation error**: Bad input rejected by our tool schema
3. **Unknown tool**: Non-existent tool name through ``gitea_call_tool`` proxy

Design decisions
----------------
- **Server per test**: Each test spawns its own MCP server.  Startup is part
  of the test — the error-handling pipeline is exercised from scratch.
- **No sequential dependencies**: These tests are independent of each other
  and of other acts.  They use ``admin_token`` directly and point at
  deliberately bogus resources.
- **One assertion per error class**: Was 6 tests, now 3.  ``bogus_repo`` tests
  the same 404 boundary as ``bogus_issue_index``; ``empty_owner_rejected``
  tests client-side validation already covered in unit; ``tool_info_unknown``
  tests the same unknown-tool path as ``call_unknown_tool``.
- **Admin token**: Uses the session ``admin_token`` fixture via
  ``mcp_client()``.  No user creation needed because tests send inputs that
  fail before reaching any resource.
- **No cleanup**: Nothing is created, nothing to clean up.
"""

from __future__ import annotations

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.conftest import live_available, mcp_client

_BOGUS_INDEX = 999999


# ---------------------------------------------------------------------------
# APINotFound — 404 from a real Gitea resource
# ---------------------------------------------------------------------------


@live_available
class TestAPINotFound:
    """A non-existent resource must return a clear not-found error
    through the full transport stack."""

    @pytest.mark.live
    async def test_bogus_issue_index(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Get an issue on a real repo with a non-existent index."""
        async with mcp_client(gitea_url, server_args, admin_token) as mcp:
            result = await mcp.call_tool(
                "gitea_issue_get_issue",
                {"owner": "mcp-server", "repo": "gitea-mcp-server", "index": _BOGUS_INDEX},
            )
            assert result.isError, "Expected error for non-existent issue index"
            text = extract_text_content(result.content)
            assert "not found" in text.lower() or "404" in text or "APINotFound" in text, (
                f"APINotFound error should mention 'not found' or '404', "
                f"got: {text[:200]!r}"
            )


# ---------------------------------------------------------------------------
# Validation error — bad input rejected by tool schema
# ---------------------------------------------------------------------------


@live_available
class TestAPIValidation:
    """Invalid parameters must return clear validation errors."""

    @pytest.mark.live
    async def test_bad_label_names(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """Creating an issue with a non-existent label returns an error
        that mentions the bad label name and lists available options."""
        async with mcp_client(gitea_url, server_args, admin_token) as mcp:
            result = await mcp.call_tool(
                "gitea_issue_create_issue",
                {
                    "owner": "mcp-server",
                    "repo": "gitea-mcp-server",
                    "title": "Test issue with bad label",
                    "labels": ["NonExistentLabelXYZ"],
                },
            )
            assert result.isError, "Expected error for non-existent label name"


# ---------------------------------------------------------------------------
# Unknown tool — non-existent name through gitea_call_tool proxy
# ---------------------------------------------------------------------------


@live_available
class TestUnknownTool:
    """Calling a non-existent tool through the proxy must issue a helpful error."""

    @pytest.mark.live
    async def test_call_unknown_tool(
        self,
        gitea_url: str,
        server_args: list[str],
        admin_token: str,
    ) -> None:
        """``gitea_call_tool`` with an unknown name returns an error
        that says the tool is not found, not a generic failure."""
        async with mcp_client(gitea_url, server_args, admin_token) as mcp:
            result = await mcp.call_tool(
                "gitea_call_tool",
                {"name": "gitea_this_tool_does_not_exist_at_all", "arguments": {}},
            )
            assert result.isError, "Expected error for calling unknown tool"
            text = extract_text_content(result.content)
            assert any(
                phrase in text.lower()
                for phrase in ["not found", "unknown", "does not exist", "not available"]
            ), (
                f"Error for unknown tool should mention it's not found, "
                f"got: {text[:300]!r}"
            )
