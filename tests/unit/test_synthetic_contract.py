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
