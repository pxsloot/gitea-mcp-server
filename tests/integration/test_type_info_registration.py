"""Integration tests for register_type_tools registration wiring.

Tests that ``register_type_tools()`` correctly registers the
``resolve_type`` tool and ``gitea://types/{typeName}``
resource on a FastMCP server, handles error paths, and produces
correct output.
"""

import json

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError

from gitea_mcp_server.tools.type_info import register_type_tools


# Minimal OpenAPI 3.1 spec with two types for testing
_MINIMAL_SPEC: dict = {
    "openapi": "3.1.0",
    "info": {"title": "Test", "version": "1.0"},
    "paths": {
        "/issues/{id}": {
            "get": {
                "operationId": "issue_get_issue",
                "responses": {
                    "200": {
                        "description": "An issue",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "assignee": {"$ref": "#/components/schemas/User"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
    "components": {
        "schemas": {
            "User": {
                "type": "object",
                "description": "User represents a user",
                "properties": {
                    "id": {"type": "integer"},
                    "login": {"type": "string"},
                },
            },
            "Milestone": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                },
            },
        },
    },
}


@pytest.fixture
def mcp() -> FastMCP:
    """Return a fresh FastMCP instance for each test."""
    return FastMCP(name="TestServer")


class TestRegisterTypeToolsTool:
    """Tests for the resolve_type tool registration."""

    @pytest.mark.asyncio
    async def test_registers_tool(self, mcp: FastMCP):
        """The resolve_type tool should be registered and callable."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}

        assert "resolve_type" in tool_names

    @pytest.mark.asyncio
    async def test_tool_has_correct_annotations(self, mcp: FastMCP):
        """The resolve_type tool should have read-only and idempotent hints."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "resolve_type")

        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False

    @pytest.mark.asyncio
    async def test_tool_has_synthetic_tag(self, mcp: FastMCP):
        """The resolve_type tool should have the synthetic tag."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "resolve_type")

        assert "synthetic" in tool.tags

    @pytest.mark.asyncio
    async def test_tool_resolves_known_type(self, mcp: FastMCP):
        """resolve_type should return type info for a known type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        result = await mcp.call_tool("resolve_type", {"name": "User"})
        data = result.structured_content["result"]
        assert data["name"] == "User"
        assert "cross_references" in data

    @pytest.mark.asyncio
    async def test_tool_errors_for_unknown_type(self, mcp: FastMCP):
        """resolve_type should raise ToolError for an unknown type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        with pytest.raises(ToolError, match="not found"):
            await mcp.call_tool("resolve_type", {"name": "NonExistentType"})

    @pytest.mark.asyncio
    async def test_tool_errors_when_no_spec(self, mcp: FastMCP):
        """resolve_type should error when openapi_spec is None."""
        register_type_tools(mcp, openapi_spec=None)

        with pytest.raises(ToolError, match="empty"):
            await mcp.call_tool("resolve_type", {"name": "User"})


class TestRegisterTypeToolsResource:
    """Tests for the gitea://types/{typeName} resource registration."""

    @pytest.mark.asyncio
    async def test_registers_resource_template(self, mcp: FastMCP):
        """The type resource template should be registered."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        templates = await mcp.list_resource_templates()
        template_uris = [r.uri_template for r in templates]

        assert "gitea://types/{typeName}" in template_uris

    @pytest.mark.asyncio
    async def test_resource_returns_known_type(self, mcp: FastMCP):
        """Reading a known type should return JSON with name and schema."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        content = await mcp.read_resource("gitea://types/User")
        raw = content.contents[0].content
        data = json.loads(raw)
        assert data["name"] == "User"
        assert "schema" in data
        assert "cross_references" in data

    @pytest.mark.asyncio
    async def test_resource_returns_full_detail_by_default(self, mcp: FastMCP):
        """By default, the resource should include resolved_schema (detail='full')."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        content = await mcp.read_resource("gitea://types/User")
        raw = content.contents[0].content
        data = json.loads(raw)
        assert "resolved_schema" in data

    @pytest.mark.asyncio
    async def test_resource_errors_for_unknown_type(self, mcp: FastMCP):
        """Reading an unknown type should raise ResourceError."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        with pytest.raises(ResourceError, match="not found"):
            await mcp.read_resource("gitea://types/NonExistentType")


class TestRegisterTypeToolsCrossReferences:
    """Tests for cross-reference accuracy in the type tool and resource."""

    @pytest.mark.asyncio
    async def test_cross_references_returned_by(self, mcp: FastMCP):
        """Tool/resource should show which tools return the type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        result = await mcp.call_tool("resolve_type", {"name": "User"})
        data = result.structured_content["result"]
        refs = data["cross_references"]
        assert "issue_get_issue" in refs["returned_by"]

    @pytest.mark.asyncio
    async def test_cross_references_in_resource(self, mcp: FastMCP):
        """Resource should include the same cross-references."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        content = await mcp.read_resource("gitea://types/User")
        raw = content.contents[0].content
        data = json.loads(raw)
        refs = data["cross_references"]
        assert "issue_get_issue" in refs["returned_by"]
