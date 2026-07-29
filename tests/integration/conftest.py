"""Shared fixtures for integration tests.

Provides reusable fixtures that reduce boilerplate when writing behavioural
integration tests.  Two patterns are supported:

1. **Override ``base_spec`` per test class** - the most common pattern.
   Define a class-level fixture that adds paths to the spec, then use the
   ``mcp_server`` fixture.  API mock routes are added inside the test body
   via module-level ``respx.get()`` / ``respx.post()`` etc. (the fixture
   keeps the global respx router active for the duration of the test).

   .. code-block:: python

       class TestIssues:
           @pytest.fixture
           def base_spec(self, base_spec):
               base_spec["paths"]["/repos/{owner}/{repo}/issues"] = {
                   "get": {
                       "operationId": "issueListIssues",
                       "summary": "List issues",
                       "responses": {"200": {"description": "Success"}},
                   }
               }
               return base_spec

           async def test_list_issues(self, mcp_server):
               respx.get("https://git.example.com/api/v1/repos/owner/repo/issues")\\
                   .respond(200, json=[{"number": 1, "title": "Bug"}])
               result = await mcp_server.call_tool(
                   "gitea_issue_list_issues",
                   {"owner": "owner", "repo": "repo"},
               )
               assert "Bug" in result[0].text

2. **Full manual control** - for tests that need custom config or mock setup
   before ``create_mcp_server`` runs (e.g., permission filtering).  Use
   ``create_test_server`` inside your own ``respx`` context.  Because these
   tests don't rely on module-level ``respx.get()``, they can use the
   isolated ``respx.mock()`` context manager.

   .. code-block:: python

       async def test_permission_filtering(self, simple_config, base_spec):
           config = SimpleConfig(…, tool_filtering_enabled=True)
           async with respx.mock() as mock:
               mock.get(f"{config.url}/swagger.v1.json").respond(200, json=base_spec)
               mock.get(f"{config.base_url}/user").respond(200, json={"login": "dev", "admin": False})
               server = await create_test_server(config, base_spec)
               tools = await server.list_tools()
               assert not any("admin" in t.name for t in tools)

.. note::

   The ``mcp_server`` / ``search_mcp_server`` fixtures use
   ``respx.start()`` / ``stop()`` (not ``respx.mock()``) because
   module-level ``respx.get()`` relies on a **global** ``MockRouter``
   singleton.  ``respx.mock()`` creates an isolated router that module-level
   calls cannot reach.  The ``try/finally`` pattern ensures routes are
   always cleaned up, even on test failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig
from tests.helpers.spec_fixtures import base_spec as _base_spec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp import FastMCP

    from gitea_mcp_server.openapi_types import SwaggerV2Spec

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_TEST_URL = "https://git.example.com"
"""Default Gitea URL used in integration tests."""


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_config() -> SimpleConfig:
    """Standard test configuration — uses all SimpleConfig defaults.

    Override in a test class to customise (e.g. enable filtering)::

        @pytest.fixture
        def simple_config(self):
            return SimpleConfig(BASE_TEST_URL, "test_token",
                                tool_filtering_enabled=True)
    """
    return SimpleConfig(url=BASE_TEST_URL, token="test_token")


@pytest.fixture
def lazy_config() -> SimpleConfig:
    """Configuration with lazy loading enabled.

    Use this instead of ``simple_config`` when tests need synthetic tools
    (``search_tools``, ``tool_info``, etc.).
    """
    return SimpleConfig(
        url=BASE_TEST_URL,
        token="test_token",
        enable_lazy_loading=True,
    )


# ---------------------------------------------------------------------------
# Spec fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def base_spec() -> SwaggerV2Spec:
    """Minimal valid Swagger 2.0 spec with no endpoints.

    Delegates to the shared fixture in ``tests.helpers.spec_fixtures``.
    Override in a test class or module to add paths:

    .. code-block:: python

        @pytest.fixture
        def base_spec(self, base_spec):
            base_spec["paths"]["/repos/{owner}/{repo}/issues"] = {
                "get": {
                    "operationId": "issueListIssues",
                    "summary": "List issues",
                    "responses": {"200": {"description": "Success"}},
                }
            }
            return base_spec
    """
    return _base_spec()


# ---------------------------------------------------------------------------
# Server factory  (call inside your own respx context)
# ---------------------------------------------------------------------------


async def create_test_server(
    config: SimpleConfig,
    spec: dict,
) -> FastMCP:
    """Create a fully wired MCP server for testing.

    **Must be called inside a ``respx.mock()`` context** that includes a route
    for ``/swagger.v1.json`` returning the given *spec*.

    Parameters
    ----------
    config:
        Test configuration (typically a ``SimpleConfig`` instance).
    spec:
        Swagger 2.0 spec dict (typically the ``base_spec`` fixture).

    Returns
    -------
    FastMCP
        The configured server instance.
    """
    gitea_client = GiteaClient(config)
    return await create_mcp_server(gitea_client)


# ---------------------------------------------------------------------------
# Pre-wired server for the default config
# ---------------------------------------------------------------------------


@pytest.fixture
async def mcp_server(
    simple_config: SimpleConfig,
    base_spec: dict,
) -> AsyncIterator[FastMCP]:
    """Pre-wired MCP server for the default configuration.

    The swagger spec fetch is mocked from ``base_spec``.  Additional
    ``respx`` routes can be registered inside the test body via the
    module-level ``respx.get()`` / ``respx.post()`` / etc. API -
    the fixture activates the **global** ``respx.router`` so that
    module-level calls add routes to the same active router.

    .. code-block:: python

        async def test_something(mcp_server):
            respx.get("https://git.example.com/api/v1/repos/owner/repo")\\
                .respond(200, json={"name": "repo"})
            result = await mcp_server.call_tool("gitea_repo_get", …)
    """
    # NOTE: We use ``respx.start()`` / ``stop()`` (not ``respx.mock()``)
    # because module-level ``respx.get() / post() / ...`` calls in test
    # bodies rely on a **global** ``MockRouter`` singleton set by
    # ``respx.start()``.  ``respx.mock()`` creates an isolated mock that
    # does NOT set the global — so module-level route registration would
    # silently target the wrong router.
    #
    # Safety: ``respx.start()`` always creates a fresh mock (discarding
    # any stale state).  The ``try/finally`` ensures ``respx.stop()`` is
    # called even on test failure, which unpatches httpx and clears all
    # routes.  No state leaks between tests.
    respx.start()
    try:
        respx.get(f"{simple_config.url}/swagger.v1.json").respond(200, json=base_spec)
        server = await create_test_server(simple_config, base_spec)
        yield server
    finally:
        respx.stop(clear=True, reset=True)


@pytest.fixture
async def search_mcp_server(
    lazy_config: SimpleConfig,
    base_spec: dict,
) -> AsyncIterator[FastMCP]:
    """Pre-wired MCP server with lazy loading enabled.

    Use this fixture when tests need the synthetic tools (``search_tools``,
    ``call_tool``, ``tool_info``, ``list_resources``, etc.) to be visible.

    See ``mcp_server`` fixture docstring for notes on the
    ``respx.start()`` / ``stop()`` pattern.
    """
    respx.start()
    try:
        respx.get(f"{lazy_config.url}/swagger.v1.json").respond(200, json=base_spec)
        server = await create_test_server(lazy_config, base_spec)
        yield server
    finally:
        respx.stop(clear=True, reset=True)
