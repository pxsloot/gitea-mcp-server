"""MCP server builder utilities.

This module provides functions to assemble the FastMCP server from OpenAPI spec,
including OpenAPI provider creation with customized component handling.

Metadata customization is done via OpenAPIProvider's public ``mcp_component_fn``.
Runtime wrapping (validation, labels, error handling) is done via a provider-level
:class:`Transform` (``provider.add_transform()``) — no private FastMCP APIs are used.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider, OpenAPITool
from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.cache_invalidation import register_tool_invalidation
from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.pagination import pagination_ctx
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
from gitea_mcp_server.tools.labels import _convert_labels, update_labels_schema
from gitea_mcp_server.tools.schemas import _is_text_response, derive_output_schema
from gitea_mcp_server.validation import ValidationError, augment_schema_with_validation

if TYPE_CHECKING:
    from httpx import AsyncClient

    from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)

_META_CUSTOMIZED = "_customization_applied"
"""Flag in component.meta to avoid double-wrapping by the transform."""


# ---------------------------------------------------------------------------
# Phase 1 — metadata customisation (in-place, called by mcp_component_fn)
# ---------------------------------------------------------------------------


def _customize_metadata(
    route: Any,
    component: OpenAPITool | Any,
    *,
    openapi_spec: dict[str, Any],
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

    augment_schema_with_validation(component)
    if has_labels:
        update_labels_schema(component)

    is_text_response = _is_text_response(
        openapi_spec,
        getattr(route, "path", ""),
        getattr(route, "method", "").lower(),
    )

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
    component_meta.setdefault("fastmcp", {}).setdefault("_internal", {})[
        "required_scope"
    ] = required_scope

    component_meta["_customization"] = {
        "has_labels": has_labels,
        "is_text_response": is_text_response,
        "route_path": getattr(route, "path", ""),
        "route_method": getattr(route, "method", ""),
    }
    component_meta[_META_CUSTOMIZED] = True
    component.meta = component_meta


# ---------------------------------------------------------------------------
# Phase 2 — runtime wrapping (provider-level Transform, public API)
# ---------------------------------------------------------------------------


class _ToolWrappingTransform(Transform):
    """Provider-level transform that wraps OpenAPITools with runtime behaviour.

    Accessed via ``provider.add_transform()`` — part of FastMCP's public API.
    Handles: argument validation, label conversion, error handling,
    text-response wrapping, and pagination metadata injection.
    """

    def __init__(
        self,
        label_manager: LabelManager,
        openapi_spec: dict[str, Any],
        gitea_client: "GiteaClient | None" = None,
    ) -> None:
        self._label_manager = label_manager
        self._openapi_spec = openapi_spec
        self._gitea_client = gitea_client

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

        async def transform_fn(**kwargs: Any) -> Any:
            return await self._run_transform_pipeline(kwargs, tool)

        return Tool.from_tool(
            tool,
            title=getattr(tool.annotations, "title", None) if tool.annotations else None,
            tags=tool.tags,
            description=tool.description,
            transform_fn=transform_fn,
            output_schema=tool.output_schema,
            meta=tool.meta,
        )

    async def _run_transform_pipeline(
        self,
        kwargs: dict[str, Any],
        tool: Tool,
    ) -> ToolResult | Any:
        """Run the full tool execution pipeline: validate, convert labels, execute, wrap result.

        Args:
            kwargs: The tool arguments from the agent.
            tool: The Tool being wrapped (provides parameter schema and meta).
        """
        meta = tool.meta or {}
        customization = meta.get("_customization", {})
        route_path: str = customization.get("route_path", "")
        route_method: str = customization.get("route_method", "")
        has_labels = customization.get("has_labels", False)
        is_text_response = customization.get("is_text_response", False)
        output_schema = tool.output_schema

        try:
            _run_validation(
                kwargs,
                tool.parameters.get("required"),
                tool.parameters.get("properties"),
            )
            await _convert_labels(kwargs, has_labels, self._label_manager, self._gitea_client)
        except ValidationError as e:
            raise ValueError(str(e)) from e
        result = await _run_with_error_handling(
            kwargs, tool, self._openapi_spec, route_path, route_method,
        )

        if is_text_response and isinstance(result, ToolResult) and result.structured_content is None:
            text = next(
                (c.text for c in result.content if isinstance(c, TextContent)),
                "",
            )
            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"result": text},
            )

        if _is_array_response(output_schema) and isinstance(result, ToolResult) and result.structured_content is not None:
            result_data = result.structured_content.get("result")
            if isinstance(result_data, list):
                page = kwargs.get("page", 1)
                per_page = kwargs.get("per_page") or kwargs.get("limit", 100)
                has_more = len(result_data) == per_page if per_page else False
                next_offset = page + 1 if has_more else None
                enhanced = dict(result.structured_content)
                enhanced["has_more"] = has_more
                enhanced["next_offset"] = next_offset
                enhanced["total_count"] = pagination_ctx.get().get("total_count")
                return ToolResult(
                    content=[TextContent(type="text", text=str(enhanced))],
                    structured_content=enhanced,
                )

        return result


# ---------------------------------------------------------------------------
# Deprecated route filtering
# ---------------------------------------------------------------------------

_HTTP_METHODS = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head", "trace",
})


def _get_deprecated_routes(openapi_spec: dict[str, Any]) -> set[tuple[str, str]]:
    """Extract set of ``(path, UPPER_METHOD)`` for deprecated operations."""
    deprecated: set[tuple[str, str]] = set()
    paths = openapi_spec.get("paths", {})
    if not isinstance(paths, dict):
        return deprecated
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            if operation.get("deprecated", False):
                deprecated.add((path, method.upper()))
    if deprecated:
        logger.info(
            "Found %d deprecated operations to exclude",
            len(deprecated),
            extra={"deprecated_routes": sorted(deprecated)},
        )
    return deprecated


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_openapi_provider(
    openapi_spec: dict[str, Any],
    client: "AsyncClient",
    label_manager: LabelManager,
    gitea_client: "GiteaClient | None" = None,
) -> OpenAPIProvider:
    """Create an ``OpenAPIProvider`` with customised metadata + runtime wrapping.

    Uses only public FastMCP APIs:
    * ``route_map_fn`` -- exclude deprecated endpoints before component creation.
    * ``mcp_component_fn`` -- in-place metadata customisation.
    * ``provider.add_transform(…)`` -- runtime behaviour wrapping.

    No private ``_tools``, ``_route``, or ``_read_resource_cache`` access.
    """
    deprecated_routes = _get_deprecated_routes(openapi_spec)

    def _deprecated_route_filter(
        route: Any, _mcp_type: MCPType
    ) -> MCPType | None:
        if (route.path, route.method) in deprecated_routes:
            logger.debug("Excluding deprecated endpoint: %s %s", route.method, route.path)
            return MCPType.EXCLUDE
        return None

    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
        route_map_fn=_deprecated_route_filter,
        mcp_component_fn=lambda route, component: _customize_metadata(
            route,
            component,
            openapi_spec=openapi_spec,
        ),
    )

    transform = _ToolWrappingTransform(
        label_manager=label_manager,
        openapi_spec=openapi_spec,
        gitea_client=gitea_client,
    )
    provider.add_transform(transform)

    return provider


__all__ = [
    "create_openapi_provider",
]
