"""MCP server builder utilities.

This module provides functions to assemble the FastMCP server from OpenAPI spec,
including OpenAPI provider creation with customized component handling.

Startup-time customization is orchestrated by :func:`_customize_metadata`
(via FastMCP's public ``mcp_component_fn`` hook) and delegated to four focused
phases:
- :func:`_apply_tool_identity` — title, annotations, hints, category,
  scope, cache invalidation
- :func:`_detect_has_labels` — detect array-typed labels parameter
- :func:`_compute_tool_schema` (pure) — schema derivation, response
  classification, route identity; followed by
  :func:`_apply_schema_postprocessing`,
  :func:`_apply_fallback_schemas`, and
  :func:`_inject_response_metadata` — schema mutations
- :func:`_build_customization_meta` — the ``component.meta`` contract

Runtime wrapping (validation, labels, error handling, text/binary response
wrapping, pagination) is handled by :class:`_ToolWrappingTransform` via
``provider.add_transform()`` — no private FastMCP APIs are used.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from fastmcp.dependencies import CurrentContext
from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider, OpenAPITool
from fastmcp.server.transforms import Transform
from fastmcp.telemetry import get_tracer
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.cache_invalidation import register_tool_invalidation
from gitea_mcp_server.context_utils import safe_ctx_info, safe_ctx_report_progress
from gitea_mcp_server.format import decode_base64_content
from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.models import ToolCustomization
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.pagination import add_pagination_metadata, pagination_ctx
from gitea_mcp_server.scope import derive_required_scope
from gitea_mcp_server.tools.customize import (
    _detect_has_labels,
    _is_array_response,
    _prepare_annotations,
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
    _unwrap_result_schema,
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


def _read_response_transform(
    openapi_spec: OpenAPISpec,
    path: str,
    method: str,
) -> str | None:
    """Read the ``x-response-transform`` annotation from a spec operation.

    Returns the transform name (e.g. ``"base64-decode"``) or ``None``.
    This is set by the OpenAPI converter for endpoints whose raw API
    response needs post-processing before being surfaced to agents.
    """
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return None
    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return None
    transform = operation.get("x-response-transform")
    if isinstance(transform, str) and transform:
        return transform
    return None


def _response_is_binary(openapi_spec: OpenAPISpec, path: str, method: str) -> bool:
    """Check whether an endpoint returns binary (non-text/plain, non-JSON) content.

    Returns ``True`` for ``application/zip``, ``application/octet-stream``, and
    similar binary MIME types.  These are distinct from text/plain (diffs, patches)
    — agents cannot usefully consume raw binary as text content.
    """
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return False
    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return False
    content_types = operation.get("x-original-content-types")
    if not isinstance(content_types, list):
        return False
    binary_types = {"application/zip", "application/octet-stream", "application/x-zip-compressed"}
    return any(
        ct.lower().strip() in binary_types
        for ct in content_types
    )


def _detect_contents_response(
    output_schema: dict[str, Any] | None,
    is_text_response: bool,
    response_transform: str | None,
) -> tuple[bool, str | None]:
    """Override text response flags when the resolved schema reveals a ContentsResponse.

    Gitea's ``GET /repos/.../contents/{path}`` endpoint returns JSON with
    ``encoding: "base64"`` and ``content`` (base64-encoded).  Forgejo's
    Swagger spec may not use a predictable ``$ref`` structure that the
    converter can detect, so this function checks the resolved OpenAPI 3.1
    schema as an authoritative fallback: if the inner (unwrapped) schema
    has both ``encoding`` and ``content`` properties, it overrides
    ``is_text_response`` to ``True`` and ``response_transform`` to
    ``"base64-decode"`` — the runtime pipeline then auto-decodes the
    base64 content into plain text.

    Args:
        output_schema: The wrapped output schema (or ``None``).
        is_text_response: Current text response flag from spec inspection.
        response_transform: Current response transform annotation (or ``None``).

    Returns:
        ``(is_text_response, response_transform)`` — possibly overridden.
    """
    if output_schema is None or is_text_response:
        return is_text_response, response_transform
    inner = _unwrap_result_schema(output_schema)
    if not isinstance(inner, dict):
        return is_text_response, response_transform
    props = inner.get("properties", {})
    if isinstance(props, dict) and "encoding" in props and "content" in props:
        return True, "base64-decode"
    return is_text_response, response_transform


# ---------------------------------------------------------------------------
# Metadata customisation (in-place, called by mcp_component_fn)
# ---------------------------------------------------------------------------


class _ComputedSchema(NamedTuple):
    """Schema, response classification, and route identity from the spec.

    Pure computation — no side effects.  Bundles all spec queries and
    route identity that share the same path/method into a single call.
    Consumers use typed attribute access instead of reaching into
    ``tool.meta`` or re-extracting from ``route``.
    """

    output_schema: dict[str, Any] | None
    raw_schema: dict[str, Any] | None
    is_text_response: bool
    is_binary_response: bool
    response_transform: str | None
    route_path: str
    route_method: str


def _compute_tool_schema(
    route: Any,
    openapi_spec: OpenAPISpec,
) -> _ComputedSchema:
    """Compute output schema, raw schema, and response classification.

    Six spec queries that share the same route path/method are bundled
    into a single pure function.  ContentsResponse detection (the
    authoritative ``encoding`` + ``content`` fallback for Forgejo compat)
    is applied here because it depends on the derived ``output_schema``.

    Also extracts ``route_path`` and ``route_method`` so downstream
    consumers (``_build_customization_meta``, ``_apply_schema_postprocessing``)
    do not need to reach into ``route`` directly.
    """
    path = getattr(route, "path", "")
    method = getattr(route, "method", "")

    output_schema = derive_output_schema(route, openapi_spec)
    raw_schema: dict[str, Any] | None = None
    if output_schema is not None:
        raw_schema = _get_success_schema(
            openapi_spec, path, method.lower(), resolve=False,
        )

    is_text_response = _is_text_response(openapi_spec, path, method)
    response_transform = _read_response_transform(
        openapi_spec, path, method,
    )
    is_text_response, response_transform = _detect_contents_response(
        output_schema, is_text_response, response_transform,
    )

    is_binary_response = _response_is_binary(openapi_spec, path, method)

    return _ComputedSchema(
        output_schema, raw_schema, is_text_response, is_binary_response,
        response_transform, path, method,
    )


def _apply_fallback_schemas(
    component: OpenAPITool,
    schema: _ComputedSchema,
    *,
    openapi_spec: OpenAPISpec,
) -> bool:
    """Apply fallback schemas for text/plain and no-content endpoints.

    Two conditional cases, both triggered when ``output_schema`` is ``None``:

    1. **Text/plain** — sets a lightweight ``{"result": string}`` schema
       so agents get schema guidance matching the runtime shape.
    2. **No-content** (204/205) — sets a ``{"result": null}`` schema so
       the MCP transport layer has proper guidance.

    Returns:
        ``has_no_content`` — ``True`` if a no-content fallback was applied.
    """
    output_schema = schema.output_schema
    if output_schema is not None:
        return False

    if schema.is_text_response:
        component.output_schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
        }
        return False

    has_no_content = _response_has_no_content(
        openapi_spec, schema.route_path, schema.route_method,
    )
    if has_no_content:
        component.output_schema = {
            "type": "object",
            "properties": {
                "result": {
                    "type": "null",
                    "description": "No content returned. The operation completed successfully.",
                },
            },
        }

    return has_no_content


def _inject_response_metadata(
    component: OpenAPITool,
) -> None:
    """Inject response-level metadata into ``component.output_schema``.

    Args:
        component: The ``OpenAPITool`` whose ``output_schema`` receives
            both injections.  Reading from a single source ensures the
            two injections cannot diverge after :func:`_apply_fallback_schemas`
            mutates ``component.output_schema``.

    Two independent injections, both derived from
    ``component.output_schema``:

    1. ``x-fastmcp-wrap-result`` — set on every non-``None`` schema so
       FastMCP wraps the response in ``{"result": ...}``.
    2. **Pagination metadata** (``has_more``, ``next_offset``,
       ``total_count``) — injected into array-response schemas so
       agents can discover pagination fields from the schema.
    """
    if component.output_schema is not None:
        component.output_schema["x-fastmcp-wrap-result"] = True

    if component.output_schema is not None and _is_array_response(component.output_schema):
        props = component.output_schema.setdefault("properties", {})
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


def _apply_schema_postprocessing(
    component: OpenAPITool,
    schema: _ComputedSchema,
    *,
    has_labels: bool,
    openapi_spec: OpenAPISpec,
) -> bool:
    """Orchestrate schema mutations on the component.

    Three phases:
    1. **Always** — schema assignment, validation augmentation, label
       schema updates (if applicable).
    2. **Fallbacks** — text/plain and no-content conditional schemas
       via :func:`_apply_fallback_schemas`.
    3. **Metadata** — ``x-fastmcp-wrap-result`` and pagination
       metadata via :func:`_inject_response_metadata`.

    Returns:
        ``has_no_content`` — forwarded from :func:`_apply_fallback_schemas`.
    """
    component.output_schema = schema.output_schema

    augment_schema_with_validation(component)
    if has_labels:
        update_labels_schema(component)
        component.tags = set(component.tags) | {"labels"}

    has_no_content = _apply_fallback_schemas(component, schema, openapi_spec=openapi_spec)
    _inject_response_metadata(component)

    return has_no_content


def _apply_tool_identity(
    route: Any,
    component: OpenAPITool,
) -> str | None:
    """Apply title, annotations, category, hints, scope, and invalidation.

    Mutates ``component`` in-place: sets ``annotations`` and ``tags``.
    Registers cache invalidation patterns for write methods.

    Returns:
        The derived ``required_scope`` (``str | None``).
    """
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

    return derive_required_scope(
        set(component.tags) if component.tags else None,
        method,
    )


def _build_customization_meta(
    component: OpenAPITool,
    required_scope: str | None,
    schema: _ComputedSchema,
    *,
    has_labels: bool,
    has_no_content: bool,
) -> None:
    """Build and attach the ``component.meta`` dict consumed by runtime transforms.

    Mutates ``component.meta`` in-place.  Sets ``required_scope``,
    ``output_schema_raw``, ``_customization``, and ``_META_CUSTOMIZED``.

    All per-tool metadata comes from ``schema`` (:class:`_ComputedSchema`)
    — no direct ``route`` or ``openapi_spec`` access needed.
    """
    component_meta = dict(component.meta) if component.meta else {}
    component_meta["required_scope"] = required_scope

    if schema.raw_schema is not None:
        component_meta["output_schema_raw"] = _unwrap_result_schema(schema.raw_schema)

    component_meta["_customization"] = ToolCustomization(
        has_labels=has_labels,
        is_text_response=schema.is_text_response,
        is_empty_response=has_no_content,
        is_binary_response=schema.is_binary_response,
        route_path=schema.route_path,
        route_method=schema.route_method,
        response_transform=schema.response_transform,
    )
    component_meta[_META_CUSTOMIZED] = True
    component.meta = component_meta


def _read_param_rename(
    openapi_spec: OpenAPISpec,
    path: str,
    method: str,
) -> dict[str, str] | None:
    """Read the ``x-param-rename`` mapping from a spec operation.

    This mapping is set by :func:`resolve_param_collisions` for operations
    where path parameter names collide with body property names.  It maps
    renamed body property names back to their original names.

    Args:
        openapi_spec: The OpenAPI 3.1 spec.
        path: The route path (e.g. ``/repos/{owner}/{repo}/issues/{index}/blocks``).
        method: The HTTP method (e.g. ``"POST"``).

    Returns:
        Dict mapping new names to original names (e.g. ``{"body_owner": "owner"}``),
        or ``None`` if no rename mapping exists.
    """
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return None
    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return None
    rename_map = operation.get("x-param-rename")
    if isinstance(rename_map, dict) and rename_map:
        return cast("dict[str, str]", rename_map)
    return None


def _apply_param_rename(
    route: Any,
    openapi_spec: OpenAPISpec,
) -> None:
    """Apply parameter rename mapping to ``route.parameter_map``.

    When body properties were renamed with a ``body_`` prefix to avoid
    collisions with path parameters (see :func:`resolve_param_collisions`),
    the ``parameter_map`` generated by FastMCP maps the renamed name to
    the body location with the renamed name as ``openapi_name``.  This
    function corrects the ``openapi_name`` to the original name so the
    ``RequestDirector`` emits the correct field names in the HTTP request
    body.

    For example, if ``x-param-rename`` is ``{"body_owner": "owner"}`` and
    ``route.parameter_map`` has ``{"body_owner": {"location": "body",
    "openapi_name": "body_owner"}}``, this function changes it to
    ``{"body_owner": {"location": "body", "openapi_name": "owner"}}``.

    Mutates ``route.parameter_map`` in-place.

    Args:
        route: The ``HTTPRoute`` object (has ``path``, ``method``,
            ``parameter_map`` attributes).
        openapi_spec: The OpenAPI 3.1 spec.
    """
    path = getattr(route, "path", "")
    method = getattr(route, "method", "")
    rename_map = _read_param_rename(openapi_spec, path, method)
    if not rename_map:
        return

    parameter_map = getattr(route, "parameter_map", None)
    if not isinstance(parameter_map, dict):
        return

    for new_name, original_name in rename_map.items():
        if new_name in parameter_map:
            mapping = parameter_map[new_name]
            if isinstance(mapping, dict) and mapping.get("location") == "body":
                mapping["openapi_name"] = original_name
                logger.debug(
                    "Fixed parameter_map for %s %s: %s → openapi_name=%s",
                    method,
                    path,
                    new_name,
                    original_name,
                )


def _customize_metadata(
    route: Any,
    component: OpenAPITool | Any,
    *,
    openapi_spec: OpenAPISpec,
) -> None:
    """In-place per-tool customization via FastMCP's ``mcp_component_fn`` hook.

    Delegates to five focused phases:
    1. ``_apply_tool_identity`` — title, annotations, hints, category,
       scope, cache invalidation
    2. ``_apply_param_rename`` — fix ``parameter_map`` for renamed body
       properties (collision resolution)
    3. ``_detect_has_labels`` — detect array-typed labels parameter
       (drives schema augmentation and metadata)
    4. ``_compute_tool_schema`` + ``_apply_schema_postprocessing`` —
       schema derivation, classification, and mutations
    5. ``_build_customization_meta`` — the ``component.meta`` contract
       consumed by runtime transforms.
    """
    if not isinstance(component, OpenAPITool):
        return

    required_scope = _apply_tool_identity(route, component)

    # Fix parameter_map for renamed body properties (collision resolution).
    # Must run after tool identity (which may inspect route metadata) but
    # before any schema processing that depends on parameter names.
    _apply_param_rename(route, openapi_spec)

    has_labels = _detect_has_labels(component)
    description = getattr(component, "description", "") or ""
    component.description = description

    schema = _compute_tool_schema(route, openapi_spec)
    has_no_content = _apply_schema_postprocessing(
        component, schema,
        has_labels=has_labels,
        openapi_spec=openapi_spec,
    )

    _build_customization_meta(
        component, required_scope, schema,
        has_labels=has_labels,
        has_no_content=has_no_content,
    )


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

    def _inject_params(self, tool: Tool) -> None:
        """Inject virtual params into the tool schema.

        Called once per tool at startup (via :meth:`_wrap`).  Mutates
        ``tool.parameters`` in place.

        ``format``'s default is dynamic — it comes from server config,
        not the registry.  Passed via ``default_overrides`` so the
        VirtualParam's ``.default`` stays accurate for the injection site.
        """
        inject_into(
            tool.parameters,
            tool=tool,
            default_overrides={"format": self._response_format},
        )

    def _make_transform_fn(
        self,
        tool: Tool,
        customization: ToolCustomization | None,
    ) -> Any:
        """Build the per-call :func:`transform_fn` closure for a tool.

        The returned callable receives ``**kwargs`` (the agent's arguments)
        and performs the full runtime pipeline: extract virtual params,
        run pre-hooks, resolve context, validate, execute via the HTTP
        layer, then hand off to :func:`apply_to` for post-hooks (formatting,
        sudo cleanup).

        Args:
            tool: The ``Tool`` being wrapped.
            customization: The ``ToolCustomization`` extracted once in
                :meth:`_wrap` and threaded explicitly — avoids repeated
                ``tool.meta`` lookups.

        All parameter handling — injection, extraction, pre-hook mutation,
        and post-hook formatting — is driven by the :mod:`virtual_params`
        registry.  The transform_fn is pure orchestration.
        """
        async def transform_fn(**kwargs: Any) -> ToolResult:
            # Pop all virtual params (format, detail, sudo, fetch_all,
            # content_type, etc.) — unified extraction, no special cases.
            virtual_values = extract_from(kwargs)

            # Run pre-hooks.  Hooks may mutate kwargs (e.g. content_type
            # base64-encodes ``content``).
            apply_pre_hooks(virtual_values, kwargs)

            # Resolve the current MCP Context so progress reporting and
            # structured logging work inside the pipeline.
            ctx = await self._resolve_current_context()
            result = await self._run_transform_pipeline(
                kwargs,
                tool,
                customization,
                extracted=virtual_values,
                ctx=ctx,
            )

            # Attach raw_schema to the extracted dict so format's post-hook
            # can access it for schema-aware rendering.  Not a VirtualParam —
            # pipeline metadata carried through the same channel as detail,
            # format, etc.  The hook reads it from ``all_extracted``.
            virtual_values["_raw_schema"] = (
                (tool.meta or {}).get("output_schema_raw")
            )

            # Run post-hooks: sudo clears context, format renders output.
            return apply_to(result, virtual_values)

        return transform_fn

    async def _wrap(self, tool: Tool) -> Tool:
        """Wrap a customized Tool with injected params and a runtime transform.

        Four phases:
        1. **Guard** — skip uncustomized tools (no metadata).
        2. **Extract** — pull ``ToolCustomization`` from ``tool.meta``
           so it is threaded explicitly through the runtime pipeline
           rather than each consumer reaching into ``tool.meta``.
        3. **Inject** — add virtual params to the tool schema.
           Runs once at startup via :meth:`list_tools` / :meth:`get_tool`.
        4. **Wrap** — attach the runtime :func:`transform_fn` via
           :meth:`Tool.from_tool`.  The transform_fn runs on every tool call.
        """
        meta = tool.meta or {}
        if not meta.get(_META_CUSTOMIZED):
            return tool

        customization: ToolCustomization | None = meta.get("_customization")
        if customization is None:
            logger.warning(  # pragma: no cover — only reachable with a hand-crafted tool meta that sets _customized flag but omits _customization
                "Tool %r has %r flag but empty customization metadata. "
                "Error messages may lack route context.",
                tool.name,
                _META_CUSTOMIZED,
            )

        # Phase 1: Schema augmentation (one-time, per-startup).
        self._inject_params(tool)

        # Phase 2: Build runtime behaviour (per-call).
        transform_fn = self._make_transform_fn(tool, customization)

        # Phase 3: Attach via Tool.from_tool (FastMCP Transform contract).
        return Tool.from_tool(
            tool,
            title=getattr(tool.annotations, "title", None) if tool.annotations else None,
            tags=tool.tags,
            description=tool.description,
            transform_fn=transform_fn,
            output_schema=tool.output_schema,
            meta=tool.meta,
        )

    async def _resolve_current_context(self) -> Any | None:
        """Resolve the current MCP Context if inside a request scope.

        ``CurrentContext()`` raises ``RuntimeError`` when called outside an
        active MCP session (e.g. in unit tests or in-memory
        ``mcp.call_tool()``).  This helper catches that and returns ``None``,
        matching the ``ctx=None`` contract of ``_pipeline_with_context``.

        Returns:
            The MCP ``Context`` object, or ``None`` if no session is active.
        """
        try:
            async with CurrentContext() as ctx:
                return ctx
        except RuntimeError:
            return None

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
        customization: ToolCustomization | None,
        extracted: dict[str, Any] | None = None,
        ctx: Any | None = None,
    ) -> ToolResult:
        """Run the full tool execution pipeline: validate, execute, wrap result.

        Label conversion is handled by the inner :class:`LabelTransform`
        that runs before this method is invoked via ``tool.run()``.

        ``ctx`` is resolved by the caller (``transform_fn`` in :meth:`_wrap`)
        and passed down so progress reporting and structured logging work
        inside the pipeline.  When ``ctx`` is ``None`` (no active MCP session),
        progress reporting and context logging degrade gracefully.

        Args:
            kwargs: The tool arguments from the agent.
            tool: The Tool being wrapped (provides parameter schema and meta).
            customization: The ``ToolCustomization`` extracted once in
                :meth:`_wrap` and threaded explicitly — avoids repeated
                ``tool.meta`` lookups throughout the pipeline.
            extracted: Extracted virtual parameter values (from
                :func:`~tools.virtual_params.extract_from`), passed through
                so the pipeline can invoke :ref:`loop_hooks <loop-hooks>`.
                ``None`` or empty means no loop hooks to run.
            ctx: The MCP ``Context`` object, or ``None`` if no session is
                active.  Resolved by the caller via :meth:`_resolve_current_context`.
        """
        if customization is None:
            route_path, route_method = "", ""
            is_text_response = is_empty_response = is_binary_response = False
        else:
            route_path = customization.route_path
            route_method = customization.route_method
            is_text_response = customization.is_text_response
            is_empty_response = customization.is_empty_response
            is_binary_response = customization.is_binary_response
        output_schema = tool.output_schema

        return await self._pipeline_with_context(
            kwargs,
            tool,
            ctx,
            route_path,
            route_method,
            is_text_response,
            is_empty_response,
            is_binary_response,
            output_schema,
            extracted=extracted,
        )

    async def _try_handle_text_response(
        self,
        result: ToolResult,
        tool: Tool,
    ) -> ToolResult | None:
        """Handle text/plain and base64-decode text responses.

        Two cases:
        1. Simple text/plain (diffs, patches): ``structured_content`` is
           ``None`` — wrap the raw text in ``{"result": text}``.
        2. Base64-decode (ContentsResponse): ``structured_content`` is the
           JSON body with ``encoding: "base64"`` — decode and return text.
        """
        if result.structured_content is None:
            text = next(
                (c.text for c in result.content if isinstance(c, TextContent)),
                "",
            )
            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"result": text},
            )
        # structured_content is not None → check for base64-decode
        c: ToolCustomization | None = (tool.meta or {}).get("_customization")
        response_transform = c.response_transform if c is not None else None
        if response_transform != "base64-decode":
            return None
        data = result.structured_content.get("result", {})
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        text = await decode_base64_content(data)
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"result": text},
        )

    async def _try_handle_binary_response(
        self,
        result: ToolResult,
    ) -> ToolResult | None:
        """Return structured ``content_info`` metadata for binary responses.

        Returns a ``ToolResult`` with ``content_info`` (type, size, guidance)
        or ``None`` when this handler does not apply.  Loop hooks are
        intentionally absent — binary responses are never paginated.
        """
        if result.structured_content is not None:
            return None
        text = next(
            (c.text for c in result.content if isinstance(c, TextContent)),
            "",
        )
        size = len(text.encode("utf-8")) if text else 0
        return ToolResult(
            content=[TextContent(
                type="text",
                text=f"Binary content ({size} bytes). Use format='raw' to access directly.",
            )],
            structured_content={
                "result": None,
                "content_info": {
                    "type": "binary",
                    "size": size,
                    "message": "Binary content returned. Use format='raw' to access the raw bytes.",
                },
            },
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
        is_binary_response: bool,
        output_schema: dict[str, Any] | None,
        extracted: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Run the tool execution pipeline with an optional Context.

        ``ctx`` is resolved by the caller (``transform_fn`` via
        :meth:`_resolve_current_context`) and passed through.  ``ctx`` is
        ``None`` when no request context is active (e.g. in-memory
        ``mcp.call_tool()``).

        Handles four response classes (in order):
        1. **JSON** — standard, passed through to output formatting.
        2. **Text/plain** (diffs, patches) — wraps raw text in
           ``{"result": text}``.
        3. **Base64-decode** (ContentsResponse) — detours JSON with
           base64-encoded content to plain text via
           ``decode_base64_content``.
        4. **Binary** (zip, octet-stream) — returns structured
           ``content_info`` metadata instead of raw bytes.
        5. **Empty-body** (204/205) — returns ``{"result": None}`` with
           the visible confirmation text ``Operation completed successfully.``
           so agents receive an explicit success signal.

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

            await safe_ctx_info(
                ctx,
                f"Validated {tool.name}",
                extra={"arg_keys": list(kwargs.keys()), "valid": True},
            )
        except ValidationError as e:
            await safe_ctx_info(
                ctx,
                f"Validation failed for {tool.name}: {e}",
                extra={"error": str(e)},
            )
            raise ValueError(str(e)) from e

        await safe_ctx_report_progress(ctx, progress=0.5)

        with tracer.start_as_current_span(f"{tool.name}.execute") as span:
            span.set_attribute("tool.name", tool.name)
            span.set_attribute("http.route", route_path)
            span.set_attribute("http.method", route_method)
            try:
                result = await _run_with_error_handling(
                    kwargs,
                    tool,
                    self._openapi_spec,
                    route_path,
                    route_method,
                )
            except UnicodeDecodeError:
                # Binary response — FastMCP's OpenAPITool.run() tries
                # response.text which crashes on binary data.  Return nil
                # structured_content so the binary branch below handles it.
                if is_binary_response:
                    result = ToolResult(
                        content=[TextContent(type="text", text="")],
                        structured_content=None,
                    )
                else:
                    raise

        await safe_ctx_info(
            ctx,
            f"Executed {tool.name}: {route_method} {route_path}",
            extra={"route": f"{route_method} {route_path}"},
        )

        # Text/plain + base64-decode: both paths handled by one method.
        # Simple text wrapping when structured_content is None (diffs,
        # patches); base64 decode when response_transform is set
        # (ContentsResponse).  Detected at spec time and schema time.
        if is_text_response:
            handled = await self._try_handle_text_response(result, tool)
            if handled is not None:
                return await self._apply_loop_hooks(
                    handled, kwargs, extracted, tool, route_path, route_method,
                )

        # Binary response (application/zip, application/octet-stream):
        # return structured content_info metadata instead of raw bytes.
        if is_binary_response:
            handled = await self._try_handle_binary_response(result)
            if handled is not None:
                return handled

        # Empty-body success responses (204 No Content, 205 Reset Content):
        # wrap in {"result": None} so it matches the explicit output_schema,
        # and set a visible text confirmation so agents see a success signal
        # instead of empty content.
        if (
            is_empty_response
            and isinstance(result, ToolResult)
            and result.structured_content is None
        ):
            result = ToolResult(
                content=[TextContent(type="text", text="Operation completed successfully.")],
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

                if len(result_data) > 0:
                    await safe_ctx_report_progress(ctx, progress=1.0, total=1.0)

                result = ToolResult(
                    content=[TextContent(type="text", text=str(enhanced))],
                    structured_content=enhanced,
                )
                return await self._apply_loop_hooks(
                    result, kwargs, extracted, tool, route_path, route_method,
                )

        await safe_ctx_report_progress(ctx, progress=1.0)

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
