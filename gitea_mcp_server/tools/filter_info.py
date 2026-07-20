"""Filter info computation — predicts which tools are filtered and why.

Computes the ``x-mcp-filtered-tools`` extension data during spec
preparation. Synthetic tools (search_tools, tool_info, call_tool) use
this data to give agents actionable error messages instead of generic
"not found".

As of Phase 2 of the Spec-Level Filtering milestone (#472), filtering
happens at spec-prep time, not via runtime transforms.  The logic here
mirrors the spec-level filtering applied via ``route_map_fn`` (see
``spec_loader.load_and_convert_spec`` and
``mcp_builder.create_openapi_provider``):
    - deprecated endpoints
    - config-based exclude/include (exclusion config)
    - scope-based filtering (token scopes)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from gitea_mcp_server.constants import HTTP_METHODS_ALL
from gitea_mcp_server.scope import derive_required_scope, has_sufficient_scope
from gitea_mcp_server.tools.exclusion import matches_any

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)


def _is_excluded(
    op_id: str,
    tags: set[str],
    exclusion_config: dict[str, list[str]],
    tool_prefix: str = "",
) -> bool:
    """Check if an operation is excluded by exclusion config.

    Include overrides exclude: if the operation matches any include pattern,
    it passes through even if it also matches an exclude pattern.
    """
    exclude = exclusion_config.get("exclude", [])
    include = exclusion_config.get("include", [])
    if not exclude and not include:
        return False
    # Exclusion patterns are written against the *final* (prefixed) tool name
    # (e.g. ``gitea_admin_*``).  At spec-prep time the operationId is still bare
    # (``admin_get_users``), so match the prefixed form.  This mirrors the old
    # runtime exclusion, where the tool name was already prefixed when the
    # ExclusionTransform ran.  Passing an empty tool_prefix to ``matches_any``
    # avoids double-prefixing.
    prefixed_id = f"{tool_prefix}{op_id}"
    is_included = matches_any(prefixed_id, tags, include, "")
    is_excluded = matches_any(prefixed_id, tags, exclude, "")
    return is_excluded and not is_included


def _get_filter_reason(  # noqa: PLR0913 - all params needed for the check
    op_id: str,
    tags: set[str],
    method: str,
    operation: dict[str, Any],
    available_scopes: set[str] | None,
    exclusion_config: dict[str, list[str]],
    tool_prefix: str = "",
) -> dict[str, Any] | None:
    """Determine why an operation is filtered, or None if visible.

    Checks are evaluated in this priority order — the first match wins:
        1. Deprecated  (strongest signal — API-level)
        2. Excluded    (server config)
        3. Scope       (token capability)

    A tool that is both deprecated AND scope-restricted is reported as
    "deprecated" rather than "scope".  This ordering means that cleaning
    up deprecated endpoints (removing them from the spec) can reveal
    scope restrictions that were previously masked.

    Args:
        op_id: The operationId (snake_case tool name w/o prefix).
        tags: Tags from the operation.
        method: HTTP method (lowercase).
        operation: The full operation dict from the spec.
        available_scopes: Set of scopes the token has, or None (no scope data).
        exclusion_config: Exclusion configuration dict.
        tool_prefix: Namespace prefix (e.g. ``gitea_``).

    Returns:
        Dict with reason and metadata, or None if the operation is visible.
    """
    # 1. Deprecated
    if operation.get("deprecated", False):
        return {"reason": "deprecated"}

    # 2. Config exclusion
    if (exclusion_config.get("exclude") or exclusion_config.get("include")) and _is_excluded(
        op_id, tags, exclusion_config, tool_prefix
    ):
        return {"reason": "excluded"}

    # 3. Scope filtering
    if available_scopes is not None:
        required = derive_required_scope(tags, method.upper())
        if required is not None and not has_sufficient_scope(required, available_scopes):
            return {
                "reason": "scope",
                "required_scope": required,
            }

    return None


def compute_filtered_tools_info(
    openapi_spec: OpenAPISpec,
    available_scopes: set[str] | None = None,
    exclusion_config: dict[str, list[str]] | None = None,
    tool_prefix: str = "",
) -> dict[str, Any]:
    """Compute filter-prediction data from the spec.

    Iterates every operation in the spec and determines if it would be
    filtered by deprecation, scope, or exclusion config.  Returns the
    structured dict used by synthetic tools and for Phase 2 filtering.

    Args:
        openapi_spec: The OpenAPI 3.1 spec (post-conversion, pre-provider).
        available_scopes: Set of scopes the token has.  ``None`` means
            "no scope data" — no tools are predicted as scope-filtered.
        exclusion_config: Exclusion patterns from YAML config, or None.
        tool_prefix: Namespace prefix (e.g. ``"gitea_"``) for exclusion
            pattern matching against prefixed names.

    Returns:
        Structured dict with this shape::

            {
                "available_scopes": ["read:repository", ...],
                "exclusion_config": {"exclude": [...], "include": [...]},
                "filtered": {
                    "admin_create_user": {
                        "reason": "scope",
                        "required_scope": "sudo",
                    },
                    "repo_deprecated_endpoint": {
                        "reason": "deprecated",
                    },
                    "some_excluded_tool": {
                        "reason": "excluded",
                    },
                },
            }
    """
    exclusion_config = exclusion_config or {"exclude": [], "include": []}
    paths: dict[str, Any] = openapi_spec.get("paths", {}) or {}

    result: dict[str, Any] = {
        "available_scopes": sorted(available_scopes) if available_scopes else [],
        "exclusion_config": {
            "exclude": exclusion_config.get("exclude", []),
            "include": exclusion_config.get("include", []),
        },
        "filtered": {},
    }

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS_ALL or not isinstance(operation, dict):
                continue

            op_id: str = operation.get("operationId", "")
            if not op_id:
                continue

            tags = set(operation.get("tags", []) or [])

            reason = _get_filter_reason(
                op_id=op_id,
                tags=tags,
                method=method,
                operation=operation,
                available_scopes=available_scopes,
                exclusion_config=exclusion_config,
                tool_prefix=tool_prefix,
            )
            if reason is not None:
                result["filtered"][op_id] = reason

    if result["filtered"]:
        logger.info(
            "Computed filter info: %d filtered operations",
            len(result["filtered"]),
        )
    else:
        logger.debug("No filtered operations found")

    return result


def get_filtered_tool_info(
    name: str,
    filtered_tools_info: dict[str, Any] | None,
    tool_prefix: str = "",
) -> dict[str, Any] | None:
    """Look up a tool in the filter-prediction data and return its filter info.

    Accepts both prefixed (``gitea_issue_create_issue``) and bare
    (``issue_create_issue``) tool names.  Strips the prefix to find the
    operationId, then looks it up.

    Args:
        name: The tool name, possibly with prefix.
        filtered_tools_info: The filter-prediction data dict
            (as returned by ``compute_filtered_tools_info``), or None.
        tool_prefix: The namespace prefix (e.g. ``"gitea_"``).

    Returns:
        The filter info dict, or None if the tool is not filtered or
        the data is missing.
    """
    if not filtered_tools_info:
        return None

    filtered: dict[str, Any] = filtered_tools_info.get("filtered", {})
    if not filtered:
        return None

    # Strip prefix to get the operationId
    op_id = name
    if tool_prefix and op_id.startswith(tool_prefix):
        op_id = op_id[len(tool_prefix) :]

    # Try the stripped name first, then the original
    return filtered.get(op_id) or filtered.get(name)


def build_filtered_tools_message(
    name: str,
    filter_entry: dict[str, Any],
    filtered_tools_info: dict[str, Any] | None = None,
) -> str:
    """Build a human-readable message explaining why a tool is filtered.

    Args:
        name: The tool name as the agent typed it.
        filter_entry: The filter info entry from
            ``filtered_tools_info["filtered"][op_id]``.
        filtered_tools_info: The full filter-prediction data
            (for context like ``available_scopes``).

    Returns:
        An agent-facing message.
    """
    reason: str = filter_entry.get("reason", "unknown")

    if reason == "scope":
        required: str = filter_entry.get("required_scope", "unknown")
        available: list[str] = (
            filtered_tools_info.get("available_scopes", []) if filtered_tools_info else []
        )

        msg = (
            f"Tool '{name}' exists but is restricted by your token scopes. "
            f"Required scope: '{required}'. "
        )
        if available:
            msg += f"Your token has: {', '.join(available)}. "
        msg += "Use `search_tools()` to see all tools available to you."
        return msg

    if reason == "excluded":
        return (
            f"Tool '{name}' is excluded by server configuration. "
            "Contact your server administrator for access. "
            "Use `search_tools()` to see all available tools."
        )

    if reason == "deprecated":
        return (
            f"Tool '{name}' has been deprecated by the Gitea API and is "
            "no longer available."
        )

    return (
        f"Tool '{name}' is not available (filter reason: {reason}). "
        "Use `search_tools()` to see all available tools."
    )


__all__ = [
    "build_filtered_tools_message",
    "compute_filtered_tools_info",
    "get_filtered_tool_info",
]
