"""Live integration tests: text/plain diff endpoint over real transport.

Exercises the full production path for the ``repoDownloadPullDiffOrPatch``
endpoint across two real transport layers:

1. **fastmcp.Client** over stdio (the primary agent-facing client)
2. **Raw MCP SDK** ``ClientSession`` over stdio

All scenarios require a real Gitea instance and real MCP server binary
— see ``tests/live/conftest.py`` for setup instructions.

See https://git.home.lan/mcp-server/gitea-mcp-server/issues/437
"""

from __future__ import annotations

import os

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.live.conftest import live_available

# ---------------------------------------------------------------------------
# Scenario 1 — fastmcp.Client over stdio
# ---------------------------------------------------------------------------


@live_available
class TestFastMCPClientTransport:
    """Drive the diff endpoint through ``fastmcp.Client`` over stdio."""

    @pytest.mark.live
    async def test_text_diff_no_validation_error(
        self,
        live_config: object,
        server_args: list[str],
        live_test_data: tuple[int, str, str, str],
    ) -> None:
        """A text/plain diff over stdio must not raise ``Output validation error``."""
        pr_number, owner, repo, _branch = live_test_data

        env = dict(os.environ)
        env["GITEA_URL"] = str(live_config.url)
        env["GITEA_TOKEN"] = str(live_config.token)
        env["TRANSPORT_TYPE"] = "stdio"

        transport = StdioTransport(command=server_args[0], args=server_args[1:], env=env)

        async with Client(transport) as client:
            result = await client.call_tool(
                "gitea_repo_download_pull_diff_or_patch",
                {
                    "owner": owner,
                    "repo": repo,
                    "index": pr_number,
                    "diffType": "diff",
                },
                raise_on_error=False,
            )

        assert not result.is_error, (
            "Expected no output validation error over stdio, got: "
            f"{result.content[0].text if result.content else 'empty'}"
        )
        text = result.content[0].text if result.content else ""
        assert "diff --git" in text, (
            f"Expected raw diff text in result, got: {text[:120]!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 2 — raw MCP SDK ClientSession over stdio
# ---------------------------------------------------------------------------


@live_available
class TestRawMCPClientSession:
    """Drive the diff endpoint through the raw MCP SDK ``ClientSession``."""

    @pytest.mark.live
    async def test_mcp_sdk_transport_no_validation_error(
        self,
        live_config: object,
        server_args: list[str],
        live_test_data: tuple[int, str, str, str],
    ) -> None:
        """Same test, raw MCP SDK ``ClientSession`` instead of fastmcp.Client."""
        pr_number, owner, repo, _branch = live_test_data

        env = dict(os.environ)
        env["GITEA_URL"] = str(live_config.url)
        env["GITEA_TOKEN"] = str(live_config.token)
        env["TRANSPORT_TYPE"] = "stdio"

        params = StdioServerParameters(
            command=server_args[0],
            args=server_args[1:],
            env=env,
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool(
                    "gitea_repo_download_pull_diff_or_patch",
                    {
                        "owner": owner,
                        "repo": repo,
                        "index": pr_number,
                        "diffType": "diff",
                    },
                )

        assert not result.isError, (
            "Expected no error via MCP SDK transport, got: "
            f"{result.content[0].text[:200] if result.content else 'empty'}"
        )
        text = result.content[0].text if result.content else ""
        assert "diff --git" in text, (
            f"Expected raw diff text, got: {text[:120]!r}"
        )


# Two scenarios cover the diff endpoint: fastmcp.Client (Scenario 1) and
# raw MCP SDK ClientSession (Scenario 2).
