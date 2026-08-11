"""Resolve parameter name collisions between path params and body properties.

FastMCP's ``_combine_schemas_and_map_params`` detects name collisions between
path parameters and body property names, then renames the *non-body* parameter
with a ``__{location}`` suffix (e.g., ``owner__path``). This leaks FastMCP
internals into the agent-facing API surface and creates confusing UX where
agents cannot tell which ``owner``/``repo``/``index`` pair identifies the
target resource vs. the body resource.

This module resolves collisions at the spec level, *before* FastMCP processes
the spec. Body properties that collide with path parameters are renamed with
a ``body_`` prefix (e.g., ``owner`` → ``body_owner``), and the mapping is
stored in an ``x-param-rename`` extension on the operation. At runtime, a
shim in the transform pipeline modifies the ``parameter_map`` so the
``RequestDirector`` emits the correct field names in the HTTP request body.

The ``body_`` prefix is chosen because:
- It is clear: ``body_owner`` means "the owner field in the request body"
- It is consistent: same prefix for all collisions
- It does not leak FastMCP internals (unlike ``__path``)

**Description injection**: Gitea's shared component schemas (e.g.
``IssueMeta``) carry no descriptions on their properties. After renaming,
empty descriptions are filled from the colliding path parameter's
description with a ``(Request body)`` prefix (e.g. ``owner`` →
``(Request body) owner of the repo``). Collection merges operation-level
and path-item-level parameter descriptions (operation-level wins on
collision). When the path parameter also lacks a description, a generic
fallback is used (``owner field of the request body resource``). Existing
non-empty descriptions are never overwritten.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from gitea_mcp_server.constants import HTTP_METHODS_ALL

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)


def resolve_spec_ref(spec: OpenAPISpec, ref: str) -> dict[str, Any] | None:
    """Resolve a ``$ref`` pointer in a spec.

    Walks the spec tree using string path segments. Returns ``None`` if
    any segment is missing (handles malformed refs gracefully).

    Args:
        spec: Post-conversion OpenAPI 3.1 spec.
        ref: The ``$ref`` string to resolve (e.g. ``#/components/schemas/IssueMeta``).

    Returns:
        The resolved schema dict, or ``None`` if resolution fails.
    """
    # Strip leading "#/" prefix precisely (not via lstrip which strips
    # all leading "#" and "/" characters indiscriminately).
    prefix = "#/"
    if ref.startswith(prefix):
        ref = ref[len(prefix):]
    parts = ref.split("/")
    current: Any = spec
    try:
        for part in parts:
            current = current[part]
    except (KeyError, TypeError):
        return None
    return current if isinstance(current, dict) else None


def _collect_path_param_names(operation: dict[str, Any]) -> set[str]:
    """Collect path parameter names from an operation.

    Only checks operation-level parameters. Path-level parameters from
    the path item are handled separately via ``_merge_path_params``.

    Args:
        operation: The OpenAPI operation dict.

    Returns:
        Set of path parameter names.
    """
    path_params: set[str] = set()
    params = operation.get("parameters", [])
    if isinstance(params, list):
        for param in params:
            if isinstance(param, dict) and param.get("in") == "path":
                name = param.get("name", "")
                if name:
                    path_params.add(name)
    return path_params


def _get_body_schema(
    operation: dict[str, Any], spec: OpenAPISpec
) -> dict[str, Any] | None:
    """Extract the request body schema from an operation.

    Resolves ``$ref`` references to shared components (e.g. ``IssueMeta``)
    and returns a deep copy so the original component is not mutated.

    Args:
        operation: The OpenAPI operation dict.
        spec: The full OpenAPI spec for ``$ref`` resolution.

    Returns:
        The resolved body schema dict, or ``None`` if no request body exists.
    """
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return None

    content = request_body.get("content", {})
    if not isinstance(content, dict):
        return None

    json_content = content.get("application/json", {})
    if not isinstance(json_content, dict):
        return None

    body_schema = json_content.get("schema")
    if not isinstance(body_schema, dict):
        return None

    # Resolve $ref to shared component (e.g. IssueMeta)
    ref = body_schema.get("$ref")
    if ref is not None:
        resolved = resolve_spec_ref(spec, ref)
        if resolved is not None:
            body_schema = deepcopy(resolved)
            # Replace the $ref with the inlined schema
            json_content["schema"] = body_schema
            logger.debug(
                "Inlined $ref %s for collision resolution", ref,
            )

    return body_schema


def _rename_colliding_body_properties(
    body_schema: dict[str, Any],
    colliding: set[str],
    path_param_descriptions: dict[str, str] | None = None,
) -> dict[str, str]:
    """Rename colliding body properties with a ``body_`` prefix.

    Mutates ``body_schema`` in-place. Returns a mapping from new name to
    original name (e.g. ``{"body_owner": "owner"}``).

    When a renamed body property has no description, injects one derived
    from the colliding path parameter's description (if available).
    Uses ``(Request body)`` prefix to distinguish from path params.
    Falls back to a generic note when the path param also has no description.

    Args:
        body_schema: The request body schema (mutated in-place).
        colliding: Set of property names that collide with path params.
        path_param_descriptions: Optional dict mapping path param names to
            their descriptions.  Pass ``None`` (default) to skip description
            injection entirely.

    Returns:
        Dict mapping new names to original names.
    """
    rename_map: dict[str, str] = {}
    props = body_schema.get("properties", {})
    if not isinstance(props, dict):
        return rename_map

    required = body_schema.get("required", [])
    if not isinstance(required, list):
        required = []

    for prop_name in sorted(colliding):  # Sort for deterministic ordering
        if prop_name not in props:
            continue
        new_name = f"body_{prop_name}"
        prop_data = props.pop(prop_name)
        props[new_name] = prop_data
        rename_map[new_name] = prop_name

        # Inject description if the body property has none
        if (
            path_param_descriptions is not None
            and not prop_data.get("description")
        ):
            path_desc = path_param_descriptions.get(prop_name)
            if path_desc:
                prop_data["description"] = f"(Request body) {path_desc}"
            else:
                prop_data["description"] = (
                    f"{prop_name} field of the request body resource"
                )

        # Update required list if needed
        if prop_name in required:
            required.remove(prop_name)
            required.append(new_name)

    if rename_map:
        body_schema["required"] = required

    return rename_map


def _resolve_operation_collisions(
    operation: dict[str, Any],
    path_params: set[str],
    openapi_spec: OpenAPISpec,
    path_item_params: list[dict[str, Any]] | None = None,
) -> dict[str, str] | None:
    """Resolve parameter collisions for a single operation.

    Returns the rename map if collisions were resolved, ``None`` otherwise.
    Mutates ``operation`` in-place (sets ``x-param-rename`` and renames body
    properties).

    Args:
        operation: The OpenAPI operation dict (mutated in-place).
        path_params: Set of path parameter names for this operation.
        openapi_spec: The full OpenAPI spec for ``$ref`` resolution.
        path_item_params: Parameter dicts from the path item (all types).
            Used to derive descriptions for renamed body properties.

    Returns:
        The rename map (e.g. ``{"body_owner": "owner"}``) or ``None``.
    """
    if not path_params:
        return None

    body_schema = _get_body_schema(operation, openapi_spec)
    if body_schema is None:
        return None

    body_props = body_schema.get("properties", {})
    if not isinstance(body_props, dict):
        return None

    colliding = path_params & set(body_props.keys())
    if not colliding:
        return None

    # Build path param descriptions for description injection
    path_param_descriptions: dict[str, str] | None = None
    if path_item_params is not None:
        path_param_descriptions = _collect_path_param_descriptions(
            operation, path_item_params,
        )

    rename_map = _rename_colliding_body_properties(
        body_schema, colliding,
        path_param_descriptions=path_param_descriptions,
    )
    if rename_map:
        operation["x-param-rename"] = rename_map
    return rename_map if rename_map else None


def _collect_path_item_params(path_item: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect all parameters defined at the path-item level.

    Path-item-level parameters are inherited by all operations in the path
    item.  This function collects all parameter types (path, query, header);
    filtering for path-type parameters happens in ``_merge_path_params``.

    Args:
        path_item: The OpenAPI path item dict.

    Returns:
        List of parameter dicts defined at the path-item level.
    """
    result: list[dict[str, Any]] = []
    raw = path_item.get("parameters", [])
    if isinstance(raw, list):
        for p in raw:
            if isinstance(p, dict):
                result.append(p)
    return result


def _merge_path_params(
    operation_params: set[str],
    path_item_params: list[dict[str, Any]],
) -> set[str]:
    """Merge operation-level and path-item-level path parameter names.

    Args:
        operation_params: Path parameter names from the operation.
        path_item_params: Parameter dicts from the path item (all types;
            only ``in == "path"`` entries are used).

    Returns:
        Combined set of path parameter names.
    """
    result = set(operation_params)
    for p in path_item_params:
        if p.get("in") == "path":
            name = p.get("name", "")
            if name:
                result.add(name)
    return result


def _collect_path_param_descriptions(
    operation: dict[str, Any],
    path_item_params: list[dict[str, Any]],
) -> dict[str, str]:
    """Collect descriptions for path parameters from both levels.

    Extracts ``{name: description}`` from operation-level and path-item-level
    path parameters. Operation-level descriptions take precedence over
    path-item-level ones on collision.

    Args:
        operation: The OpenAPI operation dict.
        path_item_params: Parameter dicts from the path item (all types;
            only ``in == "path"`` entries with non-empty descriptions are used).

    Returns:
        Dict mapping path parameter name to its description.
        Empty dict if no descriptions are found.
    """
    descriptions: dict[str, str] = {}

    # Collect from path-item level first (lower precedence)
    for param in path_item_params:
        if param.get("in") == "path":
            name = param.get("name", "")
            desc = param.get("description", "")
            if name and desc:
                descriptions[name] = desc

    # Collect from operation level (higher precedence)
    params = operation.get("parameters", [])
    if isinstance(params, list):
        for param in params:
            if isinstance(param, dict) and param.get("in") == "path":
                name = param.get("name", "")
                desc = param.get("description", "")
                if name and desc:
                    descriptions[name] = desc

    return descriptions


def resolve_param_collisions(openapi_spec: OpenAPISpec) -> None:
    """Resolve parameter name collisions in all operations.

    Scans every operation in the spec for collisions between path parameter
    names and request body property names. When a collision is detected:

    1. The body property is renamed with a ``body_`` prefix (e.g.,
       ``owner`` → ``body_owner``).
    2. The mapping is stored in an ``x-param-rename`` extension on the
       operation, as a dict of ``{new_name: original_name}``.

    Shared component schemas (referenced via ``$ref``) are inlined (deep-copied)
    so the original component definition is not mutated.

    Mutates ``openapi_spec`` in-place. Called after spec conversion, before
    FastMCP processes the spec.

    This function is guaranteed not to raise: all internal errors are caught
    and logged.  Callers do not need a try/except wrapper.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (mutated in-place).
    """
    try:
        paths: dict[str, Any] = openapi_spec.get("paths", {}) or {}
        total_collisions = 0
        affected_ops: list[str] = []

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            path_item_params = _collect_path_item_params(path_item)

            # Every method is visited: resolution is behavior-driven, gated
            # on the *presence* of a request body (checked in
            # ``_resolve_operation_collisions`` via ``_get_body_schema``),
            # not on an HTTP method allowlist.  A method gate would couple
            # this module to assumptions about which methods carry bodies —
            # an assumption Gitea already violates (DELETE endpoints with
            # ``IssueMeta`` bodies).
            for method in HTTP_METHODS_ALL:
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue

                op_path_params = _merge_path_params(
                    _collect_path_param_names(operation),
                    path_item_params,
                )

                rename_map = _resolve_operation_collisions(
                    operation, op_path_params, openapi_spec,
                    path_item_params=path_item_params,
                )
                if rename_map:
                    total_collisions += len(rename_map)
                    op_id = operation.get("operationId", f"{method} {path}")
                    affected_ops.append(op_id)
                    logger.debug(
                        "Resolved %d param collisions for %s: %s",
                        len(rename_map),
                        op_id,
                        rename_map,
                    )

        if total_collisions:
            logger.info(
                "Resolved %d parameter name collisions across %d operations: %s",
                total_collisions,
                len(affected_ops),
                sorted(affected_ops),
            )
    except Exception:
        # Broad catch is intentional: this function is called during spec
        # loading and must never propagate.  Collision resolution is a
        # best-effort optimisation — if it fails, the old ``__path`` suffix
        # behaviour returns, which is a degraded UX but not a crash.
        # ``logger.exception`` includes the full traceback so operators
        # can diagnose the root cause.
        logger.exception("Failed to resolve parameter name collisions")


__all__ = [
    "resolve_param_collisions",
]
