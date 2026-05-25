"""Test that x-fastmcp-wrap-result survives the full MCP server round-trip."""

import asyncio

import pytest
from fastmcp import FastMCP
from fastmcp.client.transports.memory import FastMCPTransport
from fastmcp.tools.base import ToolResult
from mcp import ClientSession


@pytest.mark.asyncio
async def test_wrapping_enabled():
    """With x-fastmcp-wrap-result=True, verify the client receives wrapped result."""
    mcp = FastMCP("test-server")

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {
                    "resources": {"type": "array"},
                    "count": {"type": "integer"},
                },
            },
        },
        "x-fastmcp-wrap-result": True,
    })
    async def my_list() -> dict:
        return {"resources": [{"uri": "gitea://test", "name": "test"}], "count": 1}

    transport = FastMCPTransport(mcp)
    async with transport.connect_session() as session:
        assert isinstance(session, ClientSession)
        await session.initialize()
        result = await session.call_tool("my_list", {})

        print("\n=== WITH x-fastmcp-wrap-result ===")
        print(f"type(result): {type(result)}")
        print(f"result: {result!r}")
        print(f"result.content: {result.content}")
        sc = result.structuredContent if hasattr(result, 'structuredContent') else None
        meta = result._meta if hasattr(result, '_meta') else None
        print(f"result.structuredContent: {sc!r}")
        print(f"result._meta: {meta!r}")

        assert sc is not None, f"structuredContent was None, result={result!r}"
        # The structured_content should be {"result": {"resources": [...], "count": 1}}
        assert "result" in sc, f"Expected 'result' key in structuredContent, got {sc!r}"
        inner = sc["result"]
        assert inner["resources"] == [{"uri": "gitea://test", "name": "test"}]
        assert inner["count"] == 1


@pytest.mark.asyncio
async def test_wrapping_disabled():
    """Without x-fastmcp-wrap-result, verify structuredContent is NOT wrapped."""
    mcp = FastMCP("test-server")

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "resources": {"type": "array"},
            "count": {"type": "integer"},
        },
    })
    async def my_list() -> dict:
        return {"resources": [{"uri": "gitea://test", "name": "test"}], "count": 1}

    transport = FastMCPTransport(mcp)
    async with transport.connect_session() as session:
        assert isinstance(session, ClientSession)
        await session.initialize()
        result = await session.call_tool("my_list", {})

        print("\n=== WITHOUT x-fastmcp-wrap-result ===")
        print(f"type(result): {type(result)}")
        print(f"result: {result!r}")
        print(f"result.content: {result.content}")
        sc = result.structuredContent if hasattr(result, 'structuredContent') else None
        print(f"result.structuredContent: {sc!r}")

        assert sc is not None
        # The structured_content should be {"resources": [...], "count": 1} directly
        assert "resources" in sc, f"Expected 'resources' key in structuredContent, got {sc!r}"
        assert sc["count"] == 1


@pytest.mark.asyncio
async def test_wrapping_no_output_schema():
    """With no output_schema at all, verify structuredContent is NOT wrapped (dict return)."""
    mcp = FastMCP("test-server")

    @mcp.tool()
    async def my_list() -> dict:
        return {"resources": [{"uri": "gitea://test", "name": "test"}], "count": 1}

    transport = FastMCPTransport(mcp)
    async with transport.connect_session() as session:
        assert isinstance(session, ClientSession)
        await session.initialize()
        result = await session.call_tool("my_list", {})

        print("\n=== WITHOUT output_schema ===")
        print(f"type(result): {type(result)}")
        print(f"result: {result!r}")
        print(f"result.content: {result.content}")
        sc = result.structuredContent if hasattr(result, 'structuredContent') else None
        print(f"result.structuredContent: {sc!r}")

        assert sc is not None
        # No output_schema means structured_content is just the dict
        assert "resources" in sc, f"Expected 'resources' key in structuredContent, got {sc!r}"
        assert sc["count"] == 1


@pytest.mark.asyncio
async def test_call_tool_proxy_no_double_wrap():
    """A proxy tool that delegates via ctx.fastmcp.call_tool must not double-wrap.

    This simulates the actual gitea-mcp-server call_tool: a synthetic proxy
    that forwards to the real tool via ctx.fastmcp.call_tool(). The inner tool
    already wraps with x-fastmcp-wrap-result, so the proxy must pass the
    ToolResult through without adding a second wrapping layer.
    """
    from fastmcp.server.context import Context
    from fastmcp.dependencies import CurrentContext

    mcp = FastMCP("test-server")

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {
                    "items": {"type": "array"},
                    "count": {"type": "integer"},
                },
            },
        },
        "x-fastmcp-wrap-result": True,
    })
    async def my_hidden_tool(page: int = 1) -> dict:
        """A tool with wrapped output, like real OpenAPI tools."""
        return {"items": [{"id": page}], "count": 1}

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {
                "description": "Result of the tool call, wrapped in result for consistency",
            },
        },
        "x-fastmcp-wrap-result": True,
    })
    async def call_tool_proxy(
        name: str,
        arguments: dict | None = None,
        ctx: Context = CurrentContext(),
    ):
        """Like the real call_tool: forwards to ctx.fastmcp.call_tool()."""
        return await ctx.fastmcp.call_tool(name, arguments)

    transport = FastMCPTransport(mcp)
    async with transport.connect_session() as session:
        assert isinstance(session, ClientSession)
        await session.initialize()

        # Baseline: direct call to the hidden tool
        direct = await session.call_tool("my_hidden_tool", {"page": 2})
        sc_direct = direct.structuredContent
        assert sc_direct == {"result": {"items": [{"id": 2}], "count": 1}}, (
            f"Direct call: {sc_direct}"
        )

        # Proxy call: invoke the hidden tool through call_tool_proxy
        proxy = await session.call_tool("call_tool_proxy", {
            "name": "my_hidden_tool",
            "arguments": {"page": 2},
        })
        sc_proxy = proxy.structuredContent
        assert sc_proxy == {"result": {"items": [{"id": 2}], "count": 1}}, (
            f"Proxy call: {sc_proxy}"
        )

        # Direct and proxy must return identical structure
        assert sc_direct == sc_proxy, (
            f"Mismatch: direct={sc_direct}, proxy={sc_proxy}"
        )

        # Verify NO double-wrapping: result is {"result": {...}}
        # NOT {"result": {"result": {...}}}
        assert "result" not in sc_proxy["result"], (
            f"Double-wrapped! structuredContent={sc_proxy}"
        )


@pytest.mark.asyncio
async def test_call_tool_proxy_array_result():
    """Proxy must handle array results (inner tool returns a list)."""
    from fastmcp.server.context import Context
    from fastmcp.dependencies import CurrentContext

    mcp = FastMCP("test-server")

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
            },
        },
        "x-fastmcp-wrap-result": True,
    })
    async def my_array_tool() -> list:
        """Returns a list, should be wrapped in {"result": [...]}."""
        return [{"id": 1}, {"id": 2}, {"id": 3}]

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {},
        },
        "x-fastmcp-wrap-result": True,
    })
    async def call_tool_proxy(
        name: str,
        arguments: dict | None = None,
        ctx: Context = CurrentContext(),
    ):
        return await ctx.fastmcp.call_tool(name, arguments)

    transport = FastMCPTransport(mcp)
    async with transport.connect_session() as session:
        assert isinstance(session, ClientSession)
        await session.initialize()

        # Direct call (baseline)
        direct = await session.call_tool("my_array_tool", {})
        sc_direct = direct.structuredContent
        assert sc_direct == {"result": [{"id": 1}, {"id": 2}, {"id": 3}]}, (
            f"Direct array: {sc_direct}"
        )

        # Proxy call
        proxy = await session.call_tool("call_tool_proxy", {
            "name": "my_array_tool",
            "arguments": {},
        })
        sc_proxy = proxy.structuredContent
        assert sc_proxy == {"result": [{"id": 1}, {"id": 2}, {"id": 3}]}, (
            f"Proxy array: {sc_proxy}"
        )

        assert sc_direct == sc_proxy


@pytest.mark.asyncio
async def test_call_tool_proxy_handles_no_arguments():
    """Proxy must work when called with arguments=None (omitted)."""
    from fastmcp.server.context import Context
    from fastmcp.dependencies import CurrentContext

    mcp = FastMCP("test-server")

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {"type": "string"},
        },
        "x-fastmcp-wrap-result": True,
    })
    async def simple_tool() -> str:
        return "hello"

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {},
        },
        "x-fastmcp-wrap-result": True,
    })
    async def call_tool_proxy(
        name: str,
        arguments: dict | None = None,
        ctx: Context = CurrentContext(),
    ):
        return await ctx.fastmcp.call_tool(name, arguments)

    transport = FastMCPTransport(mcp)
    async with transport.connect_session() as session:
        assert isinstance(session, ClientSession)
        await session.initialize()

        proxy = await session.call_tool("call_tool_proxy", {
            "name": "simple_tool",
        })
        sc = proxy.structuredContent
        assert sc == {"result": "hello"}, f"Got: {sc}"
