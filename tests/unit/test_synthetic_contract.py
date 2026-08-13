"""Tests for the shared contract applied to synthetic tools."""

from typing import Any

import pytest
from fastmcp import FastMCP

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.tools.synthetic_contract import (
    PAGINATION_SCHEMA_PROPERTIES,
    paginated_output_schema,
    register_synthetic_tool,
)


class TestPaginatedOutputSchema:
    """Tests for the synthetic pagination output schema helper."""

    def test_declares_runtime_metadata_without_mutating_input(self) -> None:
        """The schema must describe every pagination key returned at runtime."""
        original: dict[str, Any] = {
            "type": "object",
            "properties": {"result": {"type": "array"}},
        }

        actual = paginated_output_schema(original)

        assert set(PAGINATION_SCHEMA_PROPERTIES) <= set(actual["properties"])
        assert set(original["properties"]) == {"result"}


class TestSyntheticToolRegistration:
    """Tests for shared synthetic registration behaviour."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_page_and_limit(self) -> None:
        """Paginated synthetic tools reject the same invalid values as API tools."""
        mcp = FastMCP("test")

        @register_synthetic_tool(
            mcp,
            paginated=True,
            output_schema={"type": "object", "properties": {"result": {}}},
        )
        async def example(page: int = 1, limit: int = 10) -> dict[str, Any]:
            return {"result": []}

        invalid_values = (
            ({"page": 0}, "page"),
            ({"page": "1"}, "page"),
            ({"limit": 0}, "limit"),
            ({"limit": "10"}, "limit"),
            ({"limit": 101}, "limit"),
        )
        for kwargs, field in invalid_values:
            with pytest.raises(ValidationError) as exc_info:
                await example(**kwargs)
            assert exc_info.value.field == field

    @pytest.mark.asyncio
    async def test_schema_declares_pagination_metadata(self) -> None:
        """Registered paginated tools expose metadata in their output schema."""
        mcp = FastMCP("test")

        @register_synthetic_tool(
            mcp,
            paginated=True,
            output_schema={"type": "object", "properties": {"result": {}}},
        )
        async def example(page: int = 1, limit: int = 10) -> dict[str, Any]:
            return {"result": []}

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "example").output_schema
        assert schema is not None
        assert set(PAGINATION_SCHEMA_PROPERTIES) <= set(schema["properties"])

    @pytest.mark.asyncio
    async def test_limit_max_extends_default_page_size_bound(self) -> None:
        """A custom ``limit_max`` raises the upper bound for a tool's limit.

        ``read_doc`` paginates guide lines rather than API items, so its
        documented maximum is 200 while the default ``PAGE_SIZE_MAX`` is 100.
        The per-tool bound must not reject values inside the tool's own
        contract.
        """
        mcp = FastMCP("test")

        @register_synthetic_tool(
            mcp,
            paginated=True,
            limit_max=200,
            output_schema={"type": "object", "properties": {"result": {}}},
        )
        async def example(page: int = 1, limit: int = 50) -> dict[str, Any]:
            return {"result": []}

        # Within the tool's own bound — must be accepted.
        await example(limit=200)
        # Above it — must be rejected.
        with pytest.raises(ValidationError) as exc_info:
            await example(limit=201)
        assert exc_info.value.field == "limit"

    @pytest.mark.asyncio
    async def test_limit_max_declared_in_parameter_schema(self) -> None:
        """The real limit bound is machine-readable in the tool's schema.

        ``tool_info`` and the ``gitea://tool/{name}/schema`` resource render
        the parameter schema; the ``maximum`` declared there is how agents
        discover per-tool page-size bounds instead of hardcoded doc numbers.
        """
        mcp = FastMCP("test")

        @register_synthetic_tool(
            mcp,
            paginated=True,
            limit_max=200,
            output_schema={"type": "object", "properties": {"result": {}}},
        )
        async def example(page: int = 1, limit: int = 50) -> dict[str, Any]:
            return {"result": []}

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "example").parameters
        limit_param = schema["properties"]["limit"]
        assert limit_param.get("maximum") == 200
        assert limit_param.get("minimum") == 1

    @pytest.mark.asyncio
    async def test_annotation_rewrite_preserves_descriptions_and_page_bound(
        self,
    ) -> None:
        """Bound injection must keep the parameter descriptions agents rely on.

        Regression test: rewriting the ``limit`` annotation to
        ``Field(ge=1, le=limit_max)`` used to drop the description that the
        tool declared via ``Annotated[int, "text"]`` or a docstring ``Args``
        entry.  The bound must be added without losing the description, and
        ``page`` must gain a ``minimum`` matching autogen tools.
        """
        from typing import Annotated

        mcp = FastMCP("test")

        @register_synthetic_tool(
            mcp,
            paginated=True,
            limit_max=200,
            output_schema={"type": "object", "properties": {"result": {}}},
        )
        async def example(
            page: Annotated[int, "Page number (1-based, default 1)"] = 1,
            limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        ) -> dict[str, Any]:
            return {"result": []}

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "example").parameters
        props = schema["properties"]
        # Descriptions survive the annotation rewrite.
        assert props["page"]["description"] == "Page number (1-based, default 1)"
        assert (
            props["limit"]["description"]
            == "Maximum results per page (1-100, default 10)"
        )
        # page gains the autogen-style minimum; limit keeps both bounds.
        assert props["page"]["minimum"] == 1
        assert props["limit"]["minimum"] == 1
        assert props["limit"]["maximum"] == 200

    @pytest.mark.asyncio
    async def test_annotation_rewrite_keeps_docstring_description(self) -> None:
        """Docstring-derived parameter descriptions survive bound injection."""
        mcp = FastMCP("test")

        @register_synthetic_tool(
            mcp,
            paginated=True,
            limit_max=200,
            output_schema={"type": "object", "properties": {"result": {}}},
        )
        async def example(page: int = 1, limit: int = 50) -> dict[str, Any]:
            """Read a workflow guide.

            Args:
                page: Page number (1-based, default 1).
                limit: Lines per page (default 50).
            """
            return {"result": []}

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "example").parameters
        props = schema["properties"]
        assert props["page"]["description"] == "Page number (1-based, default 1)."
        assert props["limit"]["description"] == "Lines per page (default 50)."
        assert props["limit"]["maximum"] == 200

    @pytest.mark.asyncio
    async def test_default_limit_max_is_page_size_max(self) -> None:
        """Without ``limit_max`` the bound stays at the default 100."""
        mcp = FastMCP("test")

        @register_synthetic_tool(
            mcp,
            paginated=True,
            output_schema={"type": "object", "properties": {"result": {}}},
        )
        async def example(page: int = 1, limit: int = 10) -> dict[str, Any]:
            return {"result": []}

        await example(limit=100)
        with pytest.raises(ValidationError) as exc_info:
            await example(limit=101)
        assert exc_info.value.field == "limit"

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "example").parameters
        assert schema["properties"]["limit"].get("maximum") == 100
