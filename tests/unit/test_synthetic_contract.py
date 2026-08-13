"""Tests for the shared contract applied to synthetic tools."""

from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.tools.synthetic_contract import (
    PAGINATION_SCHEMA_PROPERTIES,
    SyntheticToolSpec,
    make_impl_executor,
    paginated_output_schema,
    register_all_synthetic_tools,
    register_synthetic_tool,
)


class TestRegisterAllSyntheticTools:
    """Tests for the declarative spec list registration loop."""

    @pytest.mark.asyncio
    async def test_registers_wrapped_and_unwrapped_specs(self) -> None:
        """Wrapped specs ride the contract; unwrapped (proxy) specs register plainly."""
        from gitea_mcp_server.tools.synthetic_contract import _SYNTHETIC_EXECUTORS

        mcp = FastMCP("test")

        async def wrapped_impl(query: str = "x") -> ToolResult:
            return ToolResult(structured_content={"result": []})

        async def proxy_impl(name: str) -> ToolResult:
            return ToolResult(structured_content={"result": name})

        register_all_synthetic_tools(mcp, [
            SyntheticToolSpec(
                impl=wrapped_impl,
                name="wrapped_tool",
                paginated=True,
                tags={"synthetic"},
                output_schema={"type": "object", "properties": {"result": {"type": "array"}}},
            ),
            SyntheticToolSpec(
                impl=proxy_impl,
                name="proxy_tool",
                wrap=False,
                tags={"synthetic"},
            ),
        ])

        tools = await mcp.list_tools()
        by_name = {t.name: t for t in tools}
        assert set(by_name) == {"wrapped_tool", "proxy_tool"}

        # Wrapped spec stamps the marker + registers an executor; unwrapped does not.
        wrapped_meta = by_name["wrapped_tool"].meta or {}
        assert wrapped_meta.get("_contract_wrap") is True
        executor_id = wrapped_meta.get("_executor_id")
        # The registry key is server-scoped (id(mcp):name) so multiple servers
        # in one process never cross-resolve executors.
        assert isinstance(executor_id, str)
        assert executor_id.endswith(":wrapped_tool")
        assert executor_id in _SYNTHETIC_EXECUTORS
        assert (by_name["proxy_tool"].meta or {}).get("_contract_wrap") is None

        # Paginated envelope declared for the wrapped spec.
        schema = by_name["wrapped_tool"].output_schema
        assert schema is not None
        assert set(PAGINATION_SCHEMA_PROPERTIES) <= set(schema["properties"])

    def test_tool_options_omits_none_fields(self) -> None:
        """tool_options() carries only the declared mcp.tool() options."""
        spec = SyntheticToolSpec(impl=lambda: None, name="t", tags={"synthetic"})
        options = spec.tool_options()
        assert options == {"name": "t", "tags": {"synthetic"}}
        assert "description" not in options
        assert "output_schema" not in options


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

    def _register_example(
        self,
        mcp: FastMCP,
        *,
        limit_max: int | None = None,
        virtual_params: set[str] | None = None,
        impl: Any = None,
    ) -> Any:
        """Register a paginated example tool and return (fn, executor)."""
        if impl is None:

            async def impl(page: int = 1, limit: int = 10) -> ToolResult:
                return ToolResult(structured_content={"result": []})

        executor = make_impl_executor(impl, paginated=True, limit_max=limit_max)
        register_synthetic_tool(
            mcp,
            executor=executor,
            paginated=True,
            limit_max=limit_max,
            virtual_params=virtual_params,
            output_schema={"type": "object", "properties": {"result": {}}},
        )(impl)
        return impl, executor

    @pytest.mark.asyncio
    async def test_rejects_invalid_page_and_limit(self) -> None:
        """Paginated synthetic tools reject the same invalid values as API tools."""
        mcp = FastMCP("test")
        _, executor = self._register_example(mcp)

        invalid_values = (
            ({"page": 0}, "page"),
            ({"page": "1"}, "page"),
            ({"limit": 0}, "limit"),
            ({"limit": "10"}, "limit"),
            ({"limit": 101}, "limit"),
        )
        for kwargs, field in invalid_values:
            with pytest.raises(ValidationError) as exc_info:
                await executor(kwargs, {}, None)
            assert exc_info.value.field == field

    @pytest.mark.asyncio
    async def test_schema_declares_pagination_metadata(self) -> None:
        """Registered paginated tools expose metadata in their output schema."""
        mcp = FastMCP("test")
        self._register_example(mcp)

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").output_schema
        assert schema is not None
        assert set(PAGINATION_SCHEMA_PROPERTIES) <= set(schema["properties"])

    @pytest.mark.asyncio
    async def test_boundary_rejects_with_friendly_error_not_pydantic(self) -> None:
        """Calls must surface the server error, not pydantic's.

        Regression test: declaring bounds via ``Field(ge=..., le=...)`` made
        pydantic reject invalid values at the boundary with a raw
        ``ValidationError`` (leaking ``errors.pydantic.dev`` URLs to agents)
        before the executor's friendly validation ran.  Bounds are now
        declared with ``Field(json_schema_extra=...)`` — schema-only — so the
        executor runs and raises ``gitea_mcp_server.exceptions.ValidationError``
        with the same message surface autogen tools use.
        """
        mcp = FastMCP("test")
        _, executor = self._register_example(mcp)

        # Bounds are still declared in the schema (agents discover via tool_info).
        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").parameters
        assert schema["properties"]["page"].get("minimum") == 1
        assert schema["properties"]["limit"].get("minimum") == 1
        assert schema["properties"]["limit"].get("maximum") == 100

        # But the boundary error comes from the server validation, not pydantic.
        for kwargs, expected in (
            ({"page": 0}, "page must be >= 1"),
            ({"limit": 101}, "limit must be <= 100"),
        ):
            with pytest.raises(ValidationError) as exc_info:
                await executor(kwargs, {}, None)
            assert expected in str(exc_info.value)
            assert "pydantic.dev" not in str(exc_info.value)
            assert "less_than_equal" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_limit_max_extends_default_page_size_bound(self) -> None:
        """A custom ``limit_max`` raises the upper bound for a tool's limit.

        ``read_doc`` paginates guide lines rather than API items, so its
        documented maximum is 200 while the default ``PAGE_SIZE_MAX`` is 100.
        The per-tool bound must not reject values inside the tool's own
        contract.
        """
        mcp = FastMCP("test")
        _, executor = self._register_example(mcp, limit_max=200)

        # Within the tool's own bound — must be accepted.
        await executor({"page": 1, "limit": 200}, {}, None)
        # Above it — must be rejected.
        with pytest.raises(ValidationError) as exc_info:
            await executor({"page": 1, "limit": 201}, {}, None)
        assert exc_info.value.field == "limit"

    @pytest.mark.asyncio
    async def test_limit_max_declared_in_parameter_schema(self) -> None:
        """The real limit bound is machine-readable in the tool's schema.

        ``tool_info`` and the ``gitea://tool/{name}/schema`` resource render
        the parameter schema; the ``maximum`` declared there is how agents
        discover per-tool page-size bounds instead of hardcoded doc numbers.
        """
        mcp = FastMCP("test")
        self._register_example(mcp, limit_max=200)

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").parameters
        limit_param = schema["properties"]["limit"]
        assert limit_param.get("maximum") == 200
        assert limit_param.get("minimum") == 1

    @pytest.mark.asyncio
    async def test_bounds_declaration_preserves_annotated_descriptions(
        self,
    ) -> None:
        """Bound injection must keep the parameter descriptions agents rely on.

        Regression test: the bounds declaration is additive — it appends a
        ``Field(json_schema_extra=...)`` to the ``page``/``limit`` annotations
        instead of rebuilding them.  The string form
        (``Annotated[int, "text"]``) must be promoted to
        ``Field(description=...)`` so the description survives alongside the
        injected bounds, and ``page`` must gain a ``minimum`` matching autogen
        tools.
        """
        from typing import Annotated

        mcp = FastMCP("test")

        async def impl(
            page: Annotated[int, "Page number (1-based, default 1)"] = 1,
            limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        ) -> ToolResult:
            return ToolResult(structured_content={"result": []})

        self._register_example(mcp, limit_max=200, impl=impl)

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").parameters
        props = schema["properties"]
        # Descriptions survive the additive bounds declaration.
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
    async def test_bounds_declaration_preserves_existing_field_metadata(
        self,
    ) -> None:
        """Non-description Field metadata survives — nothing is dropped.

        Regression test for the wholesale-replacement trap: bounds injection
        must never rebuild the annotation in a way that silently discards
        metadata (description, examples, …) already declared on ``page`` /
        ``limit``.  Everything the tool author wrote stays; bounds are
        appended.
        """
        from typing import Annotated

        from pydantic import Field

        mcp = FastMCP("test")

        async def impl(
            page: Annotated[
                int,
                Field(
                    description="Page number (1-based, default 1)",
                    examples=[1, 2],
                ),
            ] = 1,
            limit: Annotated[
                int,
                Field(
                    description="Lines per page (default 50)",
                    examples=[50, 100],
                ),
            ] = 50,
        ) -> ToolResult:
            return ToolResult(structured_content={"result": []})

        self._register_example(mcp, limit_max=200, impl=impl)

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").parameters
        props = schema["properties"]
        assert props["page"]["description"] == "Page number (1-based, default 1)"
        assert props["page"]["examples"] == [1, 2]
        assert props["limit"]["description"] == "Lines per page (default 50)"
        assert props["limit"]["examples"] == [50, 100]
        assert props["page"]["minimum"] == 1
        assert props["limit"]["minimum"] == 1
        assert props["limit"]["maximum"] == 200

    @pytest.mark.asyncio
    async def test_bounds_declaration_keeps_docstring_description(self) -> None:
        """Docstring-derived parameter descriptions survive bound injection."""
        mcp = FastMCP("test")

        async def impl(page: int = 1, limit: int = 50) -> ToolResult:
            """Read a workflow guide.

            Args:
                page: Page number (1-based, default 1).
                limit: Lines per page (default 50).
            """
            return ToolResult(structured_content={"result": []})

        self._register_example(mcp, limit_max=200, impl=impl)

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").parameters
        props = schema["properties"]
        assert props["page"]["description"] == "Page number (1-based, default 1)."
        assert props["limit"]["description"] == "Lines per page (default 50)."
        assert props["limit"]["maximum"] == 200

    @pytest.mark.asyncio
    async def test_default_limit_max_is_page_size_max(self) -> None:
        """Without ``limit_max`` the bound stays at the default 100."""
        mcp = FastMCP("test")
        _, executor = self._register_example(mcp)

        await executor({"page": 1, "limit": 100}, {}, None)
        with pytest.raises(ValidationError) as exc_info:
            await executor({"page": 1, "limit": 101}, {}, None)
        assert exc_info.value.field == "limit"

        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").parameters
        assert schema["properties"]["limit"].get("maximum") == 100

    @pytest.mark.asyncio
    async def test_bounds_declaration_skips_absent_pagination_params(self) -> None:
        """A paginated tool without ``page``/``limit`` registers unchanged.

        The bounds declaration is keyed on the params actually present in the
        signature: a tool that declares neither ``page`` nor ``limit`` (or
        only one of them) must register without bounds injection and without
        inventing parameters.  Runtime validation stays a no-op for the
        missing param (``validate_pagination`` skips ``None``).
        """
        mcp = FastMCP("test")

        async def impl(query: str = "x") -> ToolResult:
            return ToolResult(structured_content={"result": []})

        self._register_example(mcp, limit_max=200, impl=impl)
        tools = await mcp.list_tools()
        schema = next(tool for tool in tools if tool.name == "impl").parameters
        props = schema["properties"]
        assert props["query"]["type"] == "string"
        assert "page" not in props
        assert "limit" not in props

        # The executor must still run (validation skips absent params).
        executor = make_impl_executor(impl, paginated=True, limit_max=200)
        result = await executor({"query": "y"}, {}, None)
        assert result.structured_content == {"result": []}

    @pytest.mark.asyncio
    async def test_executor_marks_result_formatted(self) -> None:
        """The executor marks results _formatted so the post-hook skips them."""
        mcp = FastMCP("test")
        _, executor = self._register_example(mcp)

        result = await executor({"page": 1, "limit": 10}, {}, None)
        assert result.meta == {"_formatted": True}

    @pytest.mark.asyncio
    async def test_executor_resupplies_declared_virtual_params(self) -> None:
        """Popped virtual params the impl declares are passed back to it."""
        seen: dict[str, Any] = {}
        mcp = FastMCP("test")

        async def impl(format: str = "markdown", page: int = 1) -> ToolResult:
            seen["format"] = format
            return ToolResult(structured_content={"result": []})

        executor = make_impl_executor(impl, paginated=True)
        await executor({"page": 2}, {"format": "json"}, None)
        assert seen["format"] == "json"
