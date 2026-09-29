"""Live registration-completeness over the real spec (#801, Tier 2).

The hermetic completeness check
(``tests/integration/test_registration_contract.py``) runs over the committed
``tests/swagger.v1.json`` fixture.  This live test runs the same contract over
the *real* server's fetched spec, over the raw MCP SDK transport: every exposed
tool and registered resource must carry the typed registration record on its
MCP ``_meta``.

A dedicated server is started with ``ENABLE_LAZY_LOADING=false`` so
``list_tools`` returns the full autogen + synthetic surface (the default lazy
config lists only the pinned synthetic tools).  CI's live job is
``continue-on-error: true``, so this is a real-spec backstop, not the merge
gate.
"""

from __future__ import annotations

from typing import Any

import pytest

from gitea_mcp_server.registration import (
    REGISTRATION_KEY,
    ResourceRegistration,
    ToolRegistration,
)
from tests.live.conftest import live_available, mcp_client


def _tool_problems(meta: dict[str, Any] | None) -> list[str]:
    """Return the ways a tool's wire ``_meta`` lacks a complete record."""
    raw = (meta or {}).get(REGISTRATION_KEY)
    if not isinstance(raw, dict):
        return ["no registration record"]
    return ToolRegistration.from_dict(raw).validate()


def _resource_problems(meta: dict[str, Any] | None) -> list[str]:
    """Return the ways a resource's wire ``_meta`` lacks a complete record."""
    raw = (meta or {}).get(REGISTRATION_KEY)
    if not isinstance(raw, dict):
        return ["no registration record"]
    return ResourceRegistration.from_dict(raw).validate()


@live_available
class TestLiveRegistrationContract:
    """The real-spec surface carries complete registration records."""

    @pytest.mark.live
    async def test_every_exposed_tool_and_resource_has_a_record(
        self,
        gitea_url: str,
        admin_token: str,
        server_args: list[str],
    ) -> None:
        """Every listed tool/resource carries a complete record on the wire.

        The record travels as the MCP ``Tool._meta.registration`` /
        ``Resource._meta.registration``, so this exercises the transport-level
        contract that an in-memory ``call_tool`` bypasses.
        """
        async with mcp_client(
            gitea_url,
            server_args,
            admin_token,
            env_overrides={"ENABLE_LAZY_LOADING": "false"},
        ) as session:
            tools = (await session.list_tools()).tools
            resources = (await session.list_resources()).resources
            templates = (await session.list_resource_templates()).resourceTemplates

        assert tools, "expected a non-empty live tool surface"

        tool_failures = {tool.name: p for tool in tools if (p := _tool_problems(tool.meta))}
        assert not tool_failures, f"live tool records incomplete: {tool_failures}"

        resource_failures: dict[str, list[str]] = {}
        for resource in resources:
            if problems := _resource_problems(resource.meta):
                resource_failures[str(resource.uri)] = problems
        for template in templates:
            if problems := _resource_problems(template.meta):
                resource_failures[template.uriTemplate] = problems
        assert not resource_failures, f"live resource records incomplete: {resource_failures}"
