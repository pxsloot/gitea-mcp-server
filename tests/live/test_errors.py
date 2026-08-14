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

from tests.live.conftest import live_available, mcp_client
from tests.live.quality import ErrorContent

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
            await ErrorContent(("not found",)).verify(
                mcp,
                "gitea_issue_get_issue",
                {},
                result,
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
        """A label-bearing issue request returns an API error through MCP."""
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
            await ErrorContent(("api request failed",)).verify(
                mcp,
                "gitea_issue_create_issue",
                {},
                result,
            )


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
            await ErrorContent(("not found",)).verify(
                mcp,
                "gitea_call_tool",
                {},
                result,
            )
