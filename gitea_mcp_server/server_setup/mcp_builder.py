"""MCP server builder utilities.

This module provides functions to assemble the FastMCP server from OpenAPI spec,
including OpenAPI provider creation with customized component handling.

Metadata customization is done via OpenAPIProvider's public ``mcp_component_fn``.
Runtime wrapping (validation, labels, error handling) is done via a provider-level
:class:`Transform` (``provider.add_transform()``) - no private FastMCP APIs are used.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from fastmcp.dependencies import CurrentContext
from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider, OpenAPITool
from fastmcp.server.transforms import Transform
from fastmcp.telemetry import get_tracer
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.cache_invalidation import register_tool_invalidation
from gitea_mcp_server.constants import DETAIL_PARAM_SCHEMA
from gitea_mcp_server.format import apply_format
from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.pagination import add_pagination_metadata, pagination_ctx
from gitea_mcp_server.scope import derive_required_scope
from gitea_mcp_server.tools.customize import (
    _is_array_response,
    _prepare_annotations,
    _prepare_description,
    add_inferred_hints,
    categorize_tool,
    compute_invalidation_patterns,
    generate_tool_title,
)
from gitea_mcp_server.tools.errors import _run_validation, _run_with_error_handling
from gitea_mcp_server.tools.label_transform import LabelTransform
from gitea_mcp_server.tools.labels import update_labels_schema
from gitea_mcp_server.tools.schemas import (
    _get_success_schema,
    _is_text_response,
    _response_has_no_content,
    derive_output_schema,
)
from gitea_mcp_server.tools.virtual_params import (
    apply_pre_hooks,
    apply_to,
    extract_from,
    get_loop_hooks,
    inject_into,
)
from gitea_mcp_server.validation import ValidationError, augment_schema_with_validation

if TYPE_CHECKING:

    from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)

_META_CUSTOMIZED = "_customization_applied"
"""Flag in component.meta to avoid double-wrapping by the transform."""


# ---------------------------------------------------------------------------
# Metadata customisation (in-place, called by mcp_component_fn)
# ---------------------------------------------------------------------------


def _customize_metadata(
    route: Any,
    component: OpenAPITool | Any,
    *,
    openapi_spec: OpenAPISpec,
) -> None:
    """In-place metadata customisation for every OpenAPI component.

    Called during ``OpenAPIProvider.__init__`` via the public
    ``mcp_component_fn`` hook.  Only touches public attributes.
    """
    if not isinstance(component, OpenAPITool):
        return

    title = generate_tool_title(route)
    annotations = _prepare_annotations(component, title)
    add_inferred_hints(route, annotations)
    component.annotations = annotations

    category = categorize_tool(route.path)
    component.tags = (set(component.tags) if component.tags else set()) | {category}

    method = getattr(route, "method", None)
    if method:
        patterns = compute_invalidation_patterns(route.path, method)
        if patterns:
            register_tool_invalidation(component.name, patterns)

    required_scope = derive_required_scope(
        set(component.tags) if component.tags else None,
        method,
    )

    description, has_labels = _prepare_description(component)
    component.description = description

    output_schema = derive_output_schema(route, openapi_spec)
    component.output_schema = output_schema

    # Store unresolved schema for compact example generation.
    # Nested $ref pointers stay intact, so the example generator can
    # emit type names instead of inlining entire referenced schemas.
    raw_schema: dict[str, Any] | None = None
    if output_schema is not None:
        raw_schema = _get_success_schema(
            openapi_spec,
            getattr(route, "path", ""),
            getattr(route, "method", "").lower(),
            resolve=False,
        )

    augment_schema_with_validation(component)
    if has_labels:
        update_labels_schema(component)
        component.tags = set(component.tags) | {"labels"}

    is_text_response = _is_text_response(
        openapi_spec,
        getattr(route, "path", ""),
        getattr(route, "method", ""),
    )

    # Lightweight fallback schema for text/plain endpoints so agents
    # get schema guidance matching the {"result": text} runtime shape.
    if output_schema is None and is_text_response:
        output_schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
        }
        component.output_schema = output_schema

    # Detect endpoints whose success response has no body content (e.g. 204
    # No Content).  Set a minimal schema so the MCP transport layer has
    # proper guidance, and store the flag for runtime wrapping.
    has_no_content = False
    if output_schema is None and not is_text_response:
        has_no_content = _response_has_no_content(
            openapi_spec,
            getattr(route, "path", ""),
            getattr(route, "method", ""),
        )
        if has_no_content:
            output_schema = {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "null",
                        "description": "No content returned. The operation completed successfully.",
                    },
                },
            }
            component.output_schema = output_schema

    if component.output_schema is not None:
        component.output_schema["x-fastmcp-wrap-result"] = True

    if output_schema is not None and _is_array_response(output_schema):
        props = output_schema.setdefault("properties", {})
        props["has_more"] = {
            "type": "boolean",
            "description": "Whether more pages exist",
        }
        props["next_offset"] = {
            "type": "integer",
            "description": "Page number for next page, if any",
        }
        props["total_count"] = {
            "type": "integer",
            "description": "Total item count from server, if available",
        }

    component_meta = dict(component.meta) if component.meta else {}
    component_meta["required_scope"] = required_scope

    if raw_schema is not None:
        component_meta["output_schema_raw"] = raw_schema

    component_meta["_customization"] = {
        "has_labels": has_labels,
        "is_text_response": is_text_response,
        "is_empty_response": has_no_content,
        "route_path": getattr(route, "path", ""),
        "route_method": getattr(route, "method", ""),
    }
    component_meta[_META_CUSTOMIZED] = True
    component.meta = component_meta


# ---------------------------------------------------------------------------
# Runtime wrapping (provider-level Transform, public API)
# ---------------------------------------------------------------------------


class _ToolWrappingTransform(Transform):
    """Provider-level transform that wraps OpenAPITools with runtime behaviour.

    Accessed via ``provider.add_transform()`` - part of FastMCP's public API.
    Handles: virtual parameter inject/extract, argument validation, error
    handling, text-response wrapping, and pagination metadata injection.

    Label conversion is delegated to :class:`LabelTransform`, which is
    registered as an *inner* transform so it runs after validation but
    before the HTTP call.
    """

    def __init__(
        self,
        openapi_spec: OpenAPISpec,
        response_format: str = "markdown",
    ) -> None:
        self._openapi_spec = openapi_spec
        self._response_format = response_format

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [await self._wrap(t) for t in tools]

    async def get_tool(
        self,
        name: str,
        call_next: Any,
        *,
        version: Any = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if tool is None:
            return None
        return await self._wrap(tool)

    async def _wrap(self, tool: Tool) -> Tool:
        meta = tool.meta or {}
        if not meta.get(_META_CUSTOMIZED):
            return tool

        customization = meta.get("_customization", {})
        if not customization:
            logger.warning(
                "Tool %r has %r flag but empty customization metadata. "
                "Error messages may lack route context.",
                tool.name,
                _META_CUSTOMIZED,
            )

        # Inject any future virtual params into the tool schema.  The
        # ``format`` parameter is handled explicitly below, not here.
        inject_into(tool.parameters)

        # Inject ``format`` as a first-class parameter (promoted - not a
        # generic virtual param).  The default is server-wide configuration.
        fmt_default = self._response_format
        props = tool.parameters.setdefault("properties", {})
        if "format" not in props:
            props["format"] = {
                "type": "string",
                "enum": ["json", "markdown", "raw"],
                "default": fmt_default,
                "description": (
                    "Response format control.  "
                    f'"json" - raw JSON (default: {fmt_default}).  '
                    '"markdown" - formatted tables for human/agent reading.  '
                    '"raw" - unprocessed API response.'
                ),
            }

        # Inject ``detail`` (promoted, alongside ``format``).  Controls
        # markdown rendering depth — ``"concise"`` collapses nested
        # objects to ``$ref:TypeName`` labels, ``"full"`` renders
        # everything recursively.
        if "detail" not in props:
            props["detail"] = dict(DETAIL_PARAM_SCHEMA)

        async def transform_fn(**kwargs: Any) -> ToolResult:
            # Pop virtual params before the HTTP execution path - they are
            # not real API parameters and must not reach the Gitea API.
            virtual_values = extract_from(kwargs)

            # Run pre-hooks for extracted virtual params.  The ``sudo``
            # pre-hook stores the target username in an async context var
            # so the HTTP request hook (in client.py) can inject the
            # ``?sudo=<username>`` query parameter.
            apply_pre_hooks(virtual_values)

            # Pop ``format`` and ``detail`` explicitly (promoted params
            # that reach the output layer, not the HTTP execution path).
            fmt = kwargs.pop("format", fmt_default)
            detail = kwargs.pop("detail", "full")
            result = await self._run_transform_pipeline(
                kwargs,
                tool,
                extracted=virtual_values,
            )
            result = apply_to(result, virtual_values)

            # For raw format, return the API response as-is.
            if fmt == "raw":
                return result

            # Format: detail shrinks the data, format renders it.
            # Pagination metadata is orthogonal — attached after rendering.
            raw_schema = (tool.meta or {}).get("output_schema_raw")
            data = result.structured_content.get("result") if result.structured_content else None
            if data is None:
                return result

            # raw_schema is the wrapped output schema ({result: ...});
            # extract the inner schema for schema-aware collapse
            # (detail=concise needs the $ref pointers from the inner schema).
            inner_schema = raw_schema.get("properties", {}).get("result") if raw_schema else None
            formatted = apply_format(data, fmt, detail=detail, schema=inner_schema)
            # Preserve original structured_content (carries pagination
            # metadata and uncollapsed data for programmatic access).
            formatted.structured_content = result.structured_content
            formatted.meta = result.meta
            return formatted

        return Tool.from_tool(
            tool,
            title=getattr(tool.annotations, "title", None) if tool.annotations else None,
            tags=tool.tags,
            description=tool.description,
            transform_fn=transform_fn,
            output_schema=tool.output_schema,
            meta=tool.meta,
        )

    async def _apply_loop_hooks(  # noqa: PLR0913
        self,
        result: ToolResult,
        kwargs: dict[str, Any],
        extracted: dict[str, Any] | None,
        tool: Tool,
        route_path: str,
        route_method: str,
    ) -> ToolResult:
        """Run registered loop hooks on a ToolResult.

        Called after HTTP execution and pagination metadata have been
        applied, **before** returning the result.  Loop hooks receive an
        ``execute_fn`` callable so they can re-invoke the HTTP execution
        path with updated arguments (e.g. incremented ``page``).

        The ``execute_fn`` callable validates its kwargs (same as the
        initial pipeline) so malformed re-execution arguments are caught
        early rather than reaching the Gitea API.

        .. note::

            Each hook is responsible for its own termination (stop when
            ``has_more`` is false or a page returns fewer items than the
            page size).  No built-in iteration limit exists — that is
            intentional; the loop logic belongs in the hook.

        Returns the (potentially modified) ``ToolResult``.
        """
        if not extracted:
            return result

        async def _execute_fn(inner_kwargs: dict[str, Any]) -> ToolResult:
            # Validate re-execution kwargs the same way the initial
            # pipeline validates them (idempotent, catches errors
            # early instead of relying on the Gitea API to reject them).
            try:
                _run_validation(
                    inner_kwargs,
                    tool.parameters.get("required"),
                    tool.parameters.get("properties"),
                )
            except ValidationError as e:
                raise ValueError(str(e)) from e

            result = await _run_with_error_handling(
                inner_kwargs,
                tool,
                self._openapi_spec,
                route_path,
                route_method,
            )

            # Add pagination metadata so loop hooks (e.g. _fetch_all_loop)
            # can read has_more / next_offset / total_count on subsequent
            # pages — same wrapping that _pipeline_with_context applies
            # to the initial page.
            if (
                _is_array_response(tool.output_schema)
                and isinstance(result, ToolResult)
                and result.structured_content is not None
            ):
                data = result.structured_content.get("result")
                if isinstance(data, list):
                    page = inner_kwargs.get("page", 1)
                    limit = inner_kwargs.get("per_page") or inner_kwargs.get("limit", 100)
                    total_count = pagination_ctx.get().get("total_count")
                    enhanced = add_pagination_metadata(
                        result.structured_content,
                        page,
                        limit,
                        total_count=total_count,
                    )
                    result = ToolResult(
                        content=result.content,
                        structured_content=enhanced,
                        meta=result.meta,
                    )

            return result

        for _name, (_value, hook) in get_loop_hooks(extracted).items():
            result = await hook(result, _value, kwargs, _execute_fn)

        return result

    async def _run_transform_pipeline(
        self,
        kwargs: dict[str, Any],
        tool: Tool,
        extracted: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Run the full tool execution pipeline: validate, execute, wrap result.

        Label conversion is handled by the inner :class:`LabelTransform`
        that runs before this method is invoked via ``tool.run()``.

        Args:
            kwargs: The tool arguments from the agent.
            tool: The Tool being wrapped (provides parameter schema and meta).
            extracted: Extracted virtual parameter values (from
                :func:`~tools.virtual_params.extract_from`), passed through
                so the pipeline can invoke :ref:`loop_hooks <loop-hooks>`.
                ``None`` or empty means no loop hooks to run.
        """
        meta = tool.meta or {}
        customization = meta.get("_customization", {})
        route_path: str = customization.get("route_path", "")
        route_method: str = customization.get("route_method", "")
        is_text_response = customization.get("is_text_response", False)
        is_empty_response = customization.get("is_empty_response", False)
        output_schema = tool.output_schema

        # Resolve the current MCP Context if inside a request.
        # CurrentContext() is an async context manager - outside a request
        # context it raises RuntimeError, which we catch gracefully.
        try:
            async with CurrentContext() as ctx:
                return await self._pipeline_with_context(
                    kwargs,
                    tool,
                    ctx,
                    route_path,
                    route_method,
                    is_text_response,
                    is_empty_response,
                    output_schema,
                    extracted=extracted,
                )
        except RuntimeError:
            return await self._pipeline_with_context(
                kwargs,
                tool,
                None,
                route_path,
                route_method,
                is_text_response,
                is_empty_response,
                output_schema,
                extracted=extracted,
            )

    async def _pipeline_with_context(  # noqa: PLR0913
        self,
        kwargs: dict[str, Any],
        tool: Tool,
        ctx: Any,
        route_path: str,
        route_method: str,
        is_text_response: bool,
        is_empty_response: bool,
        output_schema: dict[str, Any] | None,
        extracted: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Run the tool execution pipeline with an optional Context.

        Separated from _run_transform_pipeline so the CurrentContext() async
        context manager is entered before any pipeline work (which may itself
        be async).  ``ctx`` is ``None`` when no request context is active.

        Args:
            extracted: Extracted virtual param values from
                :func:`~tools.virtual_params.extract_from`.  Passed through
                so that loop hooks (``VirtualParam.loop_hook``) can be
                invoked after the HTTP call and pagination metadata.
        """
        tracer = get_tracer()

        try:
            with tracer.start_as_current_span(f"{tool.name}.validate") as span:
                _run_validation(
                    kwargs,
                    tool.parameters.get("required"),
                    tool.parameters.get("properties"),
                )
                span.set_attribute("tool.name", tool.name)
                span.set_attribute("validation.arg_count", len(kwargs))

            if ctx is not None:
                await ctx.info(
                    f"Validated {tool.name}",
                    extra={"arg_keys": list(kwargs.keys()), "valid": True},
                )
        except ValidationError as e:
            if ctx is not None:
                await ctx.info(
                    f"Validation failed for {tool.name}: {e}",
                    extra={"error": str(e)},
                )
            raise ValueError(str(e)) from e

        if ctx is not None:
            await ctx.report_progress(progress=0.5)

        with tracer.start_as_current_span(f"{tool.name}.execute") as span:
            span.set_attribute("tool.name", tool.name)
            span.set_attribute("http.route", route_path)
            span.set_attribute("http.method", route_method)
            result = await _run_with_error_handling(
                kwargs,
                tool,
                self._openapi_spec,
                route_path,
                route_method,
            )

        if ctx is not None:
            await ctx.info(
                f"Executed {tool.name}: {route_method} {route_path}",
                extra={"route": f"{route_method} {route_path}"},
            )

        if (
            is_text_response
            and isinstance(result, ToolResult)
            and result.structured_content is None
        ):
            text = next(
                (c.text for c in result.content if isinstance(c, TextContent)),
                "",
            )
            result = ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"result": text},
            )
            return await self._apply_loop_hooks(
                result, kwargs, extracted, tool, route_path, route_method,
            )

        # Empty-body success responses (204 No Content, 205 Reset Content):
        # wrap in {"result": None} so it matches the explicit output_schema.
        if (
            is_empty_response
            and isinstance(result, ToolResult)
            and result.structured_content is None
        ):
            result = ToolResult(
                content=[TextContent(type="text", text="")],
                structured_content={"result": None},
            )
            return await self._apply_loop_hooks(
                result, kwargs, extracted, tool, route_path, route_method,
            )

        if (
            _is_array_response(output_schema)
            and isinstance(result, ToolResult)
            and result.structured_content is not None
        ):
            result_data = result.structured_content.get("result")
            if isinstance(result_data, list):
                page = kwargs.get("page", 1)
                per_page = kwargs.get("per_page") or kwargs.get("limit", 100)
                total_count = pagination_ctx.get().get("total_count")
                enhanced = add_pagination_metadata(
                    result.structured_content,
                    page,
                    per_page,
                    total_count=total_count,
                )

                if ctx is not None and len(result_data) > 0:
                    await ctx.report_progress(progress=1.0, total=1.0)

                result = ToolResult(
                    content=[TextContent(type="text", text=str(enhanced))],
                    structured_content=enhanced,
                )
                return await self._apply_loop_hooks(
                    result, kwargs, extracted, tool, route_path, route_method,
                )

        if ctx is not None:
            await ctx.report_progress(progress=1.0)

        return await self._apply_loop_hooks(
            result, kwargs, extracted, tool, route_path, route_method,
        )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_openapi_provider(
    openapi_spec: OpenAPISpec,
    gitea_client: "GiteaClient",
    label_service: LabelService,
    excluded_routes: "set[tuple[str, str]] | None" = None,
    response_format: str = "markdown",
) -> OpenAPIProvider:
    """Create an ``OpenAPIProvider`` with customised metadata + runtime wrapping.

    Uses only public FastMCP APIs:
    * ``route_map_fn`` -- exclude filtered operations (deprecated, scope-, and
      config-excluded) before component creation.  Filtering is decided once at
      spec-prep time (see ``spec_loader.load_and_convert_spec``) and passed in
      as ``excluded_routes``.
    * ``mcp_component_fn`` -- in-place metadata customisation.
    * ``provider.add_transform(…)`` -- runtime behaviour wrapping.

    No private ``_tools``, ``_route``, or ``_read_resource_cache`` access.

    Args:
        response_format: Default response format for tool output
            ("markdown", "json", or "raw").  Passed to
            ``_ToolWrappingTransform`` so it never needs to call
            ``Config.get()`` at wrap time.
    """
    excluded_routes = excluded_routes or set()
    client = gitea_client.client

    def _route_filter(route: Any, _mcp_type: MCPType) -> MCPType | None:
        if (route.path, route.method) in excluded_routes:
            logger.debug("Excluding filtered endpoint: %s %s", route.method, route.path)
            return MCPType.EXCLUDE
        return None

    provider = OpenAPIProvider(
        openapi_spec=cast("dict[str, Any]", openapi_spec),
        client=client,
        route_map_fn=_route_filter,
        mcp_component_fn=lambda route, component: _customize_metadata(
            route,
            component,
            openapi_spec=openapi_spec,
        ),
    )

    # Innermost transform: label conversion (after validation, before HTTP).
    # Registered first so outer transforms pass through label-wrapped tools.
    label_transform = LabelTransform(
        label_service=label_service,
        gitea_client=gitea_client,
    )
    provider.add_transform(label_transform)

    # Outer transform: virtual params, validation, error handling, wrapping.
    transform = _ToolWrappingTransform(
        openapi_spec=openapi_spec,
        response_format=response_format,
    )
    provider.add_transform(transform)

    return provider


__all__ = [
    "create_openapi_provider",
]
