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

:func:`path_param_map` is the shared view for the last two: it reads the
operation's map, restricts it to the template's actual placeholders, and
exposes both directions (``arg_to_wire`` for classification, ``wire_to_arg``
for substitution) so the inversion and filtering rule live in one place.

This module is a **leaf**: it depends only on ``openapi_types`` and does no
spec mutation.  It is the single home for reading the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

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


@dataclass(frozen=True)
class PathParamMap:
    """Both directions of the wire↔normalized mapping for a path template.

    Built by :func:`path_param_map`, restricted to the template's actual
    placeholders.  Identity entries are included for unchanged placeholders,
    so a lookup is uniform (no ``None`` branch in the hot path).

    Attributes:
        arg_to_wire: Normalized argument key -> wire placeholder, for
            classifying a handler kwarg as a path param
            (``repository_id`` -> ``repository-id``).
        wire_to_arg: Wire placeholder -> normalized argument key, for
            substituting a template to the wire form the cache keyed
            (``repository-id`` -> ``repository_id``).
    """

    arg_to_wire: dict[str, str]
    wire_to_arg: dict[str, str]


def path_param_map(
    openapi_spec: OpenAPISpec | None,
    path: str,
    method: str,
    placeholders: Iterable[str],
) -> PathParamMap:
    """Build the wire↔normalized path-param map for a template.

    Reads the operation's ``x-param-rename`` (normalized -> wire), inverts it,
    and restricts both directions to ``placeholders`` — the template's actual
    path placeholders.  Restricting means a body/query rename can never be
    routed into ``path_params``, and a placeholder with no rename gets an
    identity entry.

    Args:
        openapi_spec: The OpenAPI 3.1 spec, or ``None`` (identity map).
        path: The route path to read the rename map from.
        method: The HTTP method (case-insensitive).
        placeholders: The template's path-placeholder names (wire spelling).

    Returns:
        A :class:`PathParamMap` with both directions.
    """
    rename_map = read_param_rename(openapi_spec, path, method) or {}
    # rename_map is normalized -> wire; invert to wire -> normalized.
    wire_to_arg = {wire: normalized for normalized, wire in rename_map.items()}
    arg_to_wire: dict[str, str] = {}
    wire_to_arg_restricted: dict[str, str] = {}
    for name in placeholders:
        arg = wire_to_arg.get(name, name)
        wire_to_arg_restricted[name] = arg
        arg_to_wire[arg] = name
    return PathParamMap(arg_to_wire=arg_to_wire, wire_to_arg=wire_to_arg_restricted)


__all__ = [
    "PathParamMap",
    "path_param_map",
    "read_param_rename",
]
