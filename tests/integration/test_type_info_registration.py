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

from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform
from gitea_mcp_server.tools.synthetic_contract import get_executor_registry
from gitea_mcp_server.tools.type_info import register_type_tools
from tests.helpers.mcp_results import get_structured

# Minimal OpenAPI 3.1 spec with two types for testing
_MINIMAL_SPEC: OpenAPISpec = {
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
    """Return a fresh FastMCP instance for each test.

    The contract spine (``_ToolWrappingTransform``) is registered so
    synthetic tools render their raw ``ExecutionResult`` through the single
    result pipeline — matching the real server assembly.
    """
    server = FastMCP(name="TestServer")
    contract_transform = _ToolWrappingTransform(
        openapi_spec=_MINIMAL_SPEC,
        response_format="markdown",
        synthetic_executors=get_executor_registry(server),
    )
    server.add_transform(contract_transform)
    return server


class TestRegisterTypeToolsTool:
    """Tests for the resolve_type tool registration."""

    @pytest.mark.asyncio
    async def test_registers_tool(self, mcp: FastMCP) -> None:
        """The resolve_type tool should be registered and callable."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}

        assert "resolve_type" in tool_names

    @pytest.mark.asyncio
    async def test_tool_has_correct_annotations(self, mcp: FastMCP) -> None:
        """The resolve_type tool should have read-only and idempotent hints."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "resolve_type")

        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False

    @pytest.mark.asyncio
    async def test_tool_has_synthetic_tag(self, mcp: FastMCP) -> None:
        """The resolve_type tool should have the synthetic tag."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "resolve_type")

        assert "synthetic" in tool.tags

    @pytest.mark.asyncio
    async def test_tool_detail_default_is_concise(self, mcp: FastMCP) -> None:
        """The schema default for ``detail`` matches the impl default (issue #727).

        ``detail`` is excluded from the registry allowlist (like
        ``tool_info``), so the impl's own ``"concise"`` default is the single
        source — the schema must not advertise the registry's ``"full"``.
        """
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "resolve_type")

        detail_schema = tool.parameters["properties"]["detail"]
        assert detail_schema["default"] == "concise"

    @pytest.mark.asyncio
    async def test_tool_resolves_known_type(self, mcp: FastMCP) -> None:
        """resolve_type should return type info for a known type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        result = await mcp.call_tool("resolve_type", {"name": "User"})
        data = get_structured(result)["result"]
        assert data["name"] == "User"
        assert "cross_references" in data

    @pytest.mark.asyncio
    async def test_tool_errors_for_unknown_type(self, mcp: FastMCP) -> None:
        """resolve_type should raise ToolError for an unknown type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        with pytest.raises(ToolError, match="not found"):
            await mcp.call_tool("resolve_type", {"name": "NonExistentType"})

    @pytest.mark.asyncio
    async def test_tool_errors_when_no_spec(self, mcp: FastMCP) -> None:
        """resolve_type should error when openapi_spec is None."""
        register_type_tools(mcp, openapi_spec=None)

        with pytest.raises(ToolError, match="empty"):
            await mcp.call_tool("resolve_type", {"name": "User"})


class TestRegisterTypeToolsResource:
    """Tests for the gitea://types/{typeName} resource registration."""

    @pytest.mark.asyncio
    async def test_registers_resource_template(self, mcp: FastMCP) -> None:
        """The type resource template should be registered."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        templates = await mcp.list_resource_templates()
        template_uris = [r.uri_template for r in templates]

        assert "gitea://types/{typeName}" in template_uris

    @pytest.mark.asyncio
    async def test_resource_returns_known_type(self, mcp: FastMCP) -> None:
        """Reading a known type should return JSON with name and schema."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        content = await mcp.read_resource("gitea://types/User")
        raw = content.contents[0].content
        data = json.loads(raw)
        assert data["name"] == "User"
        assert "schema" in data
        assert "cross_references" in data

    @pytest.mark.asyncio
    async def test_resource_returns_full_detail_by_default(self, mcp: FastMCP) -> None:
        """By default, the resource should include resolved_schema (detail='full')."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        content = await mcp.read_resource("gitea://types/User")
        raw = content.contents[0].content
        data = json.loads(raw)
        assert "resolved_schema" in data

    @pytest.mark.asyncio
    async def test_resource_errors_for_unknown_type(self, mcp: FastMCP) -> None:
        """Reading an unknown type should raise ResourceError."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        with pytest.raises(ResourceError, match="not found"):
            await mcp.read_resource("gitea://types/NonExistentType")


class TestRegisterTypeToolsCrossReferences:
    """Tests for cross-reference accuracy in the type tool and resource."""

    @pytest.mark.asyncio
    async def test_cross_references_returned_by(self, mcp: FastMCP) -> None:
        """Tool/resource should show which tools return the type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        result = await mcp.call_tool("resolve_type", {"name": "User"})
        data = get_structured(result)["result"]
        refs = data["cross_references"]
        assert "issue_get_issue" in refs["returned_by"]

    @pytest.mark.asyncio
    async def test_cross_references_in_resource(self, mcp: FastMCP) -> None:
        """Resource should include the same cross-references."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        content = await mcp.read_resource("gitea://types/User")
        raw = content.contents[0].content
        data = json.loads(raw)
        refs = data["cross_references"]
        assert "issue_get_issue" in refs["returned_by"]


class TestRegisterTypeToolsCaseInsensitive:
    """Tests for case-insensitive type-name resolution."""

    @pytest.mark.asyncio
    async def test_tool_resolves_lowercase(self, mcp: FastMCP) -> None:
        """resolve_type('user') resolves the 'User' type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        result = await mcp.call_tool("resolve_type", {"name": "user"})
        data = get_structured(result)["result"]
        assert data["name"] == "User"

    @pytest.mark.asyncio
    async def test_tool_resolves_mixed_case(self, mcp: FastMCP) -> None:
        """resolve_type('uSeR') resolves the 'User' type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        result = await mcp.call_tool("resolve_type", {"name": "uSeR"})
        data = get_structured(result)["result"]
        assert data["name"] == "User"

    @pytest.mark.asyncio
    async def test_resource_resolves_lowercase(self, mcp: FastMCP) -> None:
        """Reading gitea://types/user resolves the 'User' type."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        content = await mcp.read_resource("gitea://types/user")
        raw = content.contents[0].content
        data = json.loads(raw)
        assert data["name"] == "User"


class TestRegisterTypeToolsPrefix:
    """Tests for tool_prefix applied to cross-referenced tool names."""

    @pytest.mark.asyncio
    async def test_cross_refs_prefixed_in_tool(self, mcp: FastMCP) -> None:
        """With a prefix, returned_by carries the gitea_ prefix."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC, tool_prefix="gitea_")

        result = await mcp.call_tool("resolve_type", {"name": "User"})
        data = get_structured(result)["result"]
        refs = data["cross_references"]
        assert "gitea_issue_get_issue" in refs["returned_by"]
        assert "issue_get_issue" not in refs["returned_by"]

    @pytest.mark.asyncio
    async def test_cross_refs_prefixed_in_resource(self, mcp: FastMCP) -> None:
        """With a prefix, the resource cross-refs carry the gitea_ prefix."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC, tool_prefix="gitea_")

        content = await mcp.read_resource("gitea://types/User")
        raw = content.contents[0].content
        data = json.loads(raw)
        refs = data["cross_references"]
        assert "gitea_issue_get_issue" in refs["returned_by"]

    @pytest.mark.asyncio
    async def test_no_prefix_leaves_cross_refs_bare(self, mcp: FastMCP) -> None:
        """Without a prefix, cross-refs stay bare (backward compatible)."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        result = await mcp.call_tool("resolve_type", {"name": "User"})
        data = get_structured(result)["result"]
        refs = data["cross_references"]
        assert "issue_get_issue" in refs["returned_by"]


class TestRegisterTypeToolsDefensivePaths:
    """Defensive branches in the type tool (resolve_type_info edge cases).

    ``resolve_type_info`` always returns a dict with dict ``cross_references``
    for a canonical-resolved type, so these branches are unreachable through
    normal spec construction.  They are pinned here by patching
    ``resolve_type_info`` to return the edge-case values.
    """

    @pytest.mark.asyncio
    async def test_non_dict_cross_references_left_unchanged(
        self,
        mcp: FastMCP,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When cross_references is not a dict, prefixing leaves the info alone."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC, tool_prefix="gitea_")

        def _fake_resolve(
            spec: OpenAPISpec,
            type_index: dict,
            type_name: str,
            detail: str = "concise",
        ) -> dict:
            return {"name": type_name, "cross_references": "not-a-dict"}

        monkeypatch.setattr(
            "gitea_mcp_server.tools.type_info.resolve_type_info",
            _fake_resolve,
        )
        result = await mcp.call_tool("resolve_type", {"name": "User"})
        data = get_structured(result)["result"]
        assert data["cross_references"] == "not-a-dict"

    @pytest.mark.asyncio
    async def test_resolve_type_info_none_raises_not_found(
        self,
        mcp: FastMCP,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When resolve_type_info returns None, a clear not-found error is raised."""
        register_type_tools(mcp, openapi_spec=_MINIMAL_SPEC)

        monkeypatch.setattr(
            "gitea_mcp_server.tools.type_info.resolve_type_info",
            lambda *args, **kwargs: None,
        )
        with pytest.raises(ToolError, match="not found"):
            await mcp.call_tool("resolve_type", {"name": "User"})
