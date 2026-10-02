"""The ``x-param-rename`` contract — the single rename map for spec parameters.

Rule A (``openapi_converter.normalize``) renames non-snake_case parameter
*definitions* to snake_case and records the mapping in an ``x-param-rename``
extension on the operation.  Collision resolution
(``openapi_converter.param_collision``) records its body-property renames in
the same extension.  The map is ``{normalized_name: original_wire_name}`` —
e.g. ``{"repository_id": "repository-id"}`` or ``{"page_name": "pageName"}``.

The route template keeps the **original wire spelling** (``{repository-id}``,
``{pageName}``), so a placeholder has two valid spellings: the wire form in the
template and the normalized form in Python.  Every consumer that must bridge
the two reads this map — it is the contract, not a string transform:

- ``server_setup.mcp_builder._apply_param_rename`` corrects the tool
  ``parameter_map`` so the HTTP request sends the original wire name.
- ``resources.factory.make_api_resource`` classifies handler kwargs as path
  params (the normalized kwarg maps back to the wire placeholder).
- ``cache_invalidation`` resolves invalidation templates to the wire form the
  cache keyed.

This module is a **leaf**: it depends only on ``openapi_types`` and does no
spec mutation.  It is the single home for reading the contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec


def read_param_rename(
    openapi_spec: OpenAPISpec | None,
    path: str,
    method: str,
) -> dict[str, str] | None:
    """Read the ``x-param-rename`` mapping from a spec operation.

    The mapping is set by :func:`resolve_param_collisions` for operations
    where path parameter names collide with body property names, and by
    :func:`normalize_spec` for non-snake_case parameter renames.  It maps
    renamed (normalized) names back to their original wire names.

    Args:
        openapi_spec: The OpenAPI 3.1 spec, or ``None`` (no spec available —
        returns ``None``).
        path: The route path (e.g. ``/activitypub/repository-id/{repository-id}``).
        method: The HTTP method (case-insensitive).

    Returns:
        Dict mapping normalized names to original wire names (e.g.
        ``{"repository_id": "repository-id"}``), or ``None`` if no rename
        mapping exists.
    """
    if openapi_spec is None:
        return None
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


__all__ = [
    "read_param_rename",
]
