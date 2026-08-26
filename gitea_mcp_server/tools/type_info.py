"""Type introspection tool and resource.

Provides ``resolve_type`` (synthetic tool) and ``gitea://types/{typeName}``
(resource) for resolving ``$ref:TypeName`` references that appear in
``tool_info`` output.

Core logic:
  - ``build_type_index()`` — walks the OpenAPI spec once at startup to build
    a type index with cross-references (which tools return/accept each type).
  - ``resolve_type_info()`` — resolves a named type to compact schema +
    cross-references.

Registration:
  - ``register_type_tools()`` — registers both the tool and resource on a
    FastMCP server instance.
"""

import json
import logging
from typing import Annotated, Any, Literal, cast

from fastmcp.server.context import Context

from gitea_mcp_server.constants import DETAIL_PARAM_SCHEMA_CONCISE
from gitea_mcp_server.context_utils import safe_ctx_info, safe_ctx_report_progress
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.meta import ResourceMeta
from gitea_mcp_server.tools.result_pipeline import ExecutionResult
from gitea_mcp_server.tools.schemas import (
    collect_refs,
    deep_resolve_schema,
    resolve_ref,
)
from gitea_mcp_server.tools.synthetic_contract import (
    SyntheticToolSpec,
    register_all_synthetic_tools,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Core: type index building
# ============================================================================


def _walk_response_refs(
    openapi_spec: OpenAPISpec,
    responses: Any,
    operation_id: str,
    type_index: dict[str, dict[str, Any]],
) -> None:
    """Walk response content for ``$ref`` usage and record ``returned_by``."""
    if not isinstance(responses, dict):
        return
    for code in ("200", "201"):
        response = responses.get(code)
        if not isinstance(response, dict):
            continue
        if "$ref" in response:
            resolved = resolve_ref(openapi_spec, response["$ref"])
            if isinstance(resolved, dict):
                response = resolved
        content = response.get("content", {})
        if not isinstance(content, dict):
            continue
        json_content = content.get("application/json", {})
        if not isinstance(json_content, dict):
            continue
        schema = json_content.get("schema")
        if isinstance(schema, dict):
            for ref in collect_refs(schema):
                if ref in type_index:
                    type_index[ref].setdefault("returned_by", []).append(operation_id)


def _walk_parameter_refs(
    parameters: Any,
    operation_id: str,
    type_index: dict[str, dict[str, Any]],
) -> None:
    """Walk operation parameters for ``$ref`` usage and record ``accepted_by``."""
    if not isinstance(parameters, list):
        return
    for param in parameters:
        if not isinstance(param, dict):
            continue
        param_schema = param.get("schema")
        if isinstance(param_schema, dict):
            for ref in collect_refs(param_schema):
                if ref in type_index:
                    type_index[ref].setdefault("accepted_by", []).append(operation_id)


def _walk_request_body_refs(
    request_body: Any,
    operation_id: str,
    type_index: dict[str, dict[str, Any]],
) -> None:
    """Walk request body for ``$ref`` usage and record ``accepted_by``."""
    if not isinstance(request_body, dict):
        return
    body_content = request_body.get("content", {})
    if not isinstance(body_content, dict):
        return
    for media_item in body_content.values():
        if not isinstance(media_item, dict):
            continue
        body_schema = media_item.get("schema")
        if isinstance(body_schema, dict):
            for ref in collect_refs(body_schema):
                if ref in type_index:
                    type_index[ref].setdefault("accepted_by", []).append(operation_id)


def _walk_operation_refs(
    openapi_spec: OpenAPISpec,
    operation: dict[str, Any],
    operation_id: str,
    type_index: dict[str, dict[str, Any]],
) -> None:
    """Walk a single operation's response + parameters and record ``$ref`` usage.

    Updates ``type_index`` in-place with ``returned_by`` and ``accepted_by``
    cross-references for each type found.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        operation: The operation dict to walk.
        operation_id: The operation's ``operationId`` (for cross-references).
        type_index: Mutable type index built by :func:`build_type_index`.
    """
    _walk_response_refs(openapi_spec, operation.get("responses", {}), operation_id, type_index)
    _walk_parameter_refs(operation.get("parameters", []), operation_id, type_index)
    _walk_request_body_refs(operation.get("requestBody"), operation_id, type_index)


def build_type_index(openapi_spec: OpenAPISpec) -> dict[str, dict[str, Any]]:
    """Walk the OpenAPI spec and build a type index with cross-references.

    Extracts every type from ``components/schemas``, then walks all
    operations to discover which types are returned or accepted by each
    tool.

    The returned dict maps::

        {type_name: {
            "schema": <raw schema dict>,
            "referenced_types": [TypeName, ...],
            "returned_by": [operationId, ...],
            "accepted_by": [operationId, ...],
        }}

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec.

    Returns:
        Type index dict (may be empty if spec has no ``components/schemas``).
    """
    components = openapi_spec.get("components", {})
    schemas = components.get("schemas", {})
    if not isinstance(schemas, dict):
        return {}

    type_index: dict[str, dict[str, Any]] = {}

    # First pass: register all types and their nested refs
    for type_name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        schema_refs = collect_refs(schema)
        type_index[type_name] = {
            "schema": schema,
            "referenced_types": sorted(schema_refs),
            "returned_by": [],
            "accepted_by": [],
        }

    # Second pass: walk all operations for cross-references
    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId", "")
            if not operation_id:
                continue
            _walk_operation_refs(openapi_spec, operation, operation_id, type_index)

    # Deduplicate cross-references
    for entry in type_index.values():
        entry["returned_by"] = sorted(set(entry.get("returned_by", [])))
        entry["accepted_by"] = sorted(set(entry.get("accepted_by", [])))

    return type_index


# ============================================================================
# Core: type resolution
# ============================================================================


def resolve_type_info(
    openapi_spec: OpenAPISpec,
    type_index: dict[str, dict[str, Any]],
    type_name: str,
    detail: str = "concise",
) -> dict[str, Any] | None:
    """Resolve and return type info for a named type.

    Produces a compact type summary with ``$ref`` placeholders (like
    ``tool_info``'s ``output_example``), plus cross-references showing
    which tools return or accept this type.

    When ``detail="full"``, the result also includes the fully-resolved
    output schema (``resolved_schema``).

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        type_index: Type index built by :func:`build_type_index`.
        type_name: The type name to resolve (e.g. ``"User"``, ``"Milestone"``).
        detail: ``"concise"`` (default) or ``"full"``.

    Returns:
        Type info dict, or ``None`` if the type is not found.
    """
    if type_name not in type_index:
        return None

    entry = type_index[type_name]
    schema = entry["schema"]

    if not isinstance(schema, dict):
        return None

    description: str = schema.get("description", "") or ""

    # Build compact example with $ref placeholders.
    # Deferred import to avoid circular: examples → schemas → this module
    from gitea_mcp_server.tools.examples import schema_to_compact_example  # noqa: PLC0415

    compact = schema_to_compact_example(schema, openapi_spec=openapi_spec)

    result: dict[str, Any] = {
        "name": type_name,
        "description": description,
        "schema": compact,
        "cross_references": {
            "returned_by": entry.get("returned_by", []),
            "accepted_by": entry.get("accepted_by", []),
            "referenced_types": entry.get("referenced_types", []),
        },
    }

    if detail == "full":
        result["resolved_schema"] = deep_resolve_schema(schema, openapi_spec)

    return result


# ============================================================================
# Registration
# ============================================================================


def register_type_tools(  # noqa: PLR0915 - registration function: builds the type index, two nested closures (tool + resource), and the tool/resource specs; extracting would scatter the registration pattern
    mcp: Any,
    openapi_spec: OpenAPISpec | None = None,
    tool_prefix: str = "",
) -> None:
    """Register the ``resolve_type`` tool and ``gitea://types/{typeName}`` resource.

    The tool lets agents resolve ``$ref:TypeName`` references they see in
    ``tool_info`` output.  The resource provides cached reads of the same
    data.

    Both are registration-time closures over the built type index.

    Type-name lookup is **case-insensitive**: ``resolve_type("label")`` and
    ``resolve_type("Label")`` resolve the same type.  Cross-references
    (``returned_by``/``accepted_by``) carry the configured ``tool_prefix``
    (e.g. ``gitea_``) so the listed tool names are directly callable.

    Args:
        mcp: The FastMCP server instance.
        openapi_spec: Post-conversion OpenAPI 3.1 spec, or ``None`` (tools
            will return a helpful error).
        tool_prefix: Namespace prefix (e.g. ``"gitea_"``) applied to
            cross-referenced tool names.
    """
    from gitea_mcp_server.tools.customize import synthetic_annotations  # noqa: PLC0415
    from gitea_mcp_server.tools.errors import raise_value_error  # noqa: PLC0415

    _MAX_TYPES_IN_RESOURCE_DESC = 10

    # Build the type index once at registration time.
    type_index: dict[str, dict[str, Any]] = {}
    available_types: list[str] = []
    # Case-insensitive lookup: lowercase name -> canonical type name.
    type_index_lower: dict[str, str] = {}
    if openapi_spec is not None:
        type_index = build_type_index(openapi_spec)
        available_types = sorted(type_index.keys())
        type_index_lower = {name.lower(): name for name in type_index}

    def _resolve_canonical(name: str) -> str | None:
        """Resolve a (possibly mixed-case) type name to its canonical form."""
        return type_index_lower.get(name.lower())

    def _prefix_cross_refs(info: dict[str, Any]) -> dict[str, Any]:
        """Prefix cross-referenced tool names with ``tool_prefix``.

        ``returned_by``/``accepted_by`` hold operationIds (e.g.
        ``issue_get_issue``); the agent-facing tool names carry the
        ``gitea_`` prefix.  Prefixing makes the listed names directly
        callable.  ``referenced_types`` are type names, not tools — left
        unchanged.
        """
        if not tool_prefix:
            return info
        cross = info.get("cross_references", {})
        if not isinstance(cross, dict):
            return info
        for key in ("returned_by", "accepted_by"):
            names = cross.get(key, [])
            if isinstance(names, list):
                cross[key] = [f"{tool_prefix}{n}" for n in names]
        return info

    async def _resolve_type_impl(
        name: Annotated[
            str,
            "Type name to resolve (e.g. 'User', 'Milestone', 'Label'). "
            "Case-insensitive. Use search_tools or list_resources to find tools "
            "that reference types.",
        ],
        ctx: Context,
        detail: Annotated[
            # Keep in sync with DETAIL_PARAM_SCHEMA/DETAIL_PARAM_SCHEMA_CONCISE enum in constants.py
            Literal["concise", "full"],
            str(DETAIL_PARAM_SCHEMA_CONCISE["description"]),
        ] = "concise",
    ) -> ExecutionResult:
        """Resolve a $ref type name to its schema and cross-references."""
        if not type_index:
            msg = "Type index is empty. The OpenAPI spec may not be available."
            await safe_ctx_info(ctx, "Type index empty — spec not available at registration")
            raise_value_error(msg)

        await safe_ctx_info(
            ctx,
            f"Resolving type '{name}' (detail={detail})",
            extra={"type_name": name, "detail": detail},
        )

        canonical = _resolve_canonical(name)
        if canonical is None:
            msg = (
                f"Type '{name}' not found. "
                "Use search_resources('type') or "
                "call resolve_type with one of the tool's $ref:TypeName markers."
            )
            await safe_ctx_info(
                ctx,
                f"Type '{name}' not found",
                extra={"type_name": name, "found": False},
            )
            raise_value_error(msg)

        # Guard above guarantees openapi_spec was available at registration.
        spec = cast("OpenAPISpec", openapi_spec)
        info = resolve_type_info(
            spec,
            type_index,
            canonical,
            detail=detail,
        )
        if info is None:
            msg = (
                f"Type '{name}' not found. "
                "Use search_resources('type') or "
                "call resolve_type with one of the tool's $ref:TypeName markers."
            )
            await safe_ctx_info(
                ctx,
                f"Type '{name}' not found",
                extra={"type_name": name, "found": False},
            )
            raise_value_error(msg)

        info = _prefix_cross_refs(info)

        await safe_ctx_report_progress(ctx, progress=1.0)
        logger.debug(
            "Resolved type '%s' (%d cross-refs)", name, len(info.get("cross_references", {}))
        )

        return ExecutionResult(data=info, shape="object")

    register_all_synthetic_tools(
        mcp,
        [
            SyntheticToolSpec(
                impl=_resolve_type_impl,
                name="resolve_type",
                description=(
                    "Resolve a ``$ref`` type name to its schema and cross-references. "
                    "When ``tool_info`` shows ``$ref:TypeName`` in its output_example, "
                    "use this tool to discover what fields that type contains. "
                    "Returns a compact type summary (with ``$ref`` placeholders for "
                    "nested types) and cross-references showing which tools return "
                    "or accept this type.\n\n"
                    '**Resource**: ``read_resource("gitea://types/{TypeName}")`` '
                    "for a cached JSON read."
                ),
                tags={"synthetic", "type-schema"},
                annotations=synthetic_annotations(read_only=True, open_world=False),
                output_schema={
                    "type": "object",
                    "properties": {
                        "result": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Type name"},
                                "description": {
                                    "type": "string",
                                    "description": "Type description from the spec",
                                },
                                "schema": {
                                    "anyOf": [
                                        {"type": "object"},
                                        {"type": "array"},
                                        {"type": "string"},
                                        {"type": "boolean"},
                                        {"type": "number"},
                                        {"type": "null"},
                                    ],
                                    "description": "Compact type summary with $ref placeholders (primitives for scalar types)",
                                },
                                "resolved_schema": {
                                    "type": "object",
                                    "description": "Fully-resolved JSON Schema (included only when detail='full')",
                                },
                                "cross_references": {
                                    "type": "object",
                                    "properties": {
                                        "returned_by": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "Tools that return this type",
                                        },
                                        "accepted_by": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "Tools that accept this type as input",
                                        },
                                        "referenced_types": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "Types this type references via $ref",
                                        },
                                    },
                                },
                            },
                            "example": {
                                "name": "User",
                                "description": "User represents a user",
                                "schema": {
                                    "id": 0,
                                    "login": "user",
                                    "full_name": "Full Name",
                                    "avatar_url": {"$ref": "string|null"},
                                },
                                "cross_references": {
                                    "returned_by": ["issue_get_issue", "user_get_current"],
                                    "accepted_by": ["admin_create_user"],
                                    "referenced_types": [],
                                },
                            },
                        },
                    },
                },
                # ``detail`` is excluded from the registry allowlist (like
                # ``tool_info``): the impl's own signature default
                # (``"concise"``) is the single source, so the schema and
                # runtime agree when the agent omits ``detail``.
                virtual_params={"format"},
            ),
        ],
    )

    # ── gitea://types/{typeName} resource ──────────────────────────────

    async def _type_resource(
        typeName: str,
        ctx: Context,
    ) -> str:
        """Get a type schema for a $ref type by name.

        Returns the type's full schema with resolved `$ref` definitions,
        cross-references, and resolved schema — identical to
        ``resolve_type(name, detail="full")`` but available as a cached
        resource.

        Args:
            typeName: Type name (e.g. "User", "Milestone", "Label").

        Returns:
            JSON string with type info (schema, cross-references).
        """
        await safe_ctx_info(
            ctx,
            f"Reading type resource '{typeName}'",
            extra={"type_name": typeName},
        )

        canonical = _resolve_canonical(typeName)
        if not type_index or canonical is None:
            msg = (
                f"Type '{typeName}' not found. "
                "Use search_resources('type') to discover valid type names."
            )
            raise ValueError(msg)

        # Same invariant: non-empty type_index means spec was available.
        spec = cast("OpenAPISpec", openapi_spec)
        # Resource always returns full detail (tools manage concise/full).
        info = resolve_type_info(
            spec,
            type_index,
            canonical,
            detail="full",
        )
        info = _prefix_cross_refs(info) if info else info
        return json.dumps(info, indent=2) if info else "{}"

    # No required_scope: the type index is built from the OpenAPI spec
    # which is fetched from a public endpoint — reading it is scope-free.
    # Tools and resources outside the agent's token scope are filtered at
    # spec-prep time (route_map_fn), so the type index cannot leak data
    # from unreachable endpoints.
    _type_meta = ResourceMeta(
        required_scope=None, size_hint="medium", default_detail="full"
    ).to_dict()

    mcp.resource(
        uri="gitea://types/{typeName}",
        name="type_schema",
        description=(
            "Get the full schema (always detail='full') for a $ref type "
            "by name.  Use after tool_info when you see ``$ref:TypeName`` "
            "and need to discover the type's fields.  For compact output, "
            "call ``resolve_type(name, detail='concise')`` instead. "
            f"Available types: {', '.join(available_types[:_MAX_TYPES_IN_RESOURCE_DESC])}"
            + ("..." if len(available_types) > _MAX_TYPES_IN_RESOURCE_DESC else "")
            + (
                ". Use search_resources('type') or search_resources for more."
                if len(available_types) > _MAX_TYPES_IN_RESOURCE_DESC
                else ""
            )
        ),
        mime_type="application/json",
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        meta=_type_meta,
        tags={"synthetic", "type-schema", "schema"},
    )(_type_resource)


__all__ = [
    "build_type_index",
    "register_type_tools",
    "resolve_type_info",
]
