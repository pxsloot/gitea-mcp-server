"""Cache invalidation for write operations in Gitea MCP Server.

This module provides functionality to invalidate cached MCP resources when
data is modified via tool calls.  It addresses the issue where resources
remain cached even after the underlying data changes.

The system works by:
1. Recording every write tool's ``(name, path, method)`` during tool
   customization (``record_write_tool``).
2. After resource registration, deriving each write tool's invalidation
   targets from the spec + the registered resource surface
   (``build_invalidation_map``) — no hardcoded URI templates.
3. After tool execution, the middleware computes concrete URIs from tool
   arguments and clears them from the cache, including query-variant reads
   recorded at read time.

Target derivation (issue #743) has two parts:

* **Path-prefix** — a write at path ``P`` invalidates every registered
  resource whose api_path is a prefix of (or equal to) ``P`` (template-
  aware, full prefix — no exceptions).  This covers the resource itself,
  its sub-resources, and ancestor aggregates (e.g. an issue write
  invalidates the repo resource).
* **Cross-tree** — a write whose operation carries ``x-modifies-type``
  (stamped by ``openapi_converter.type_references``) invalidates every
  registered resource whose response schema references that type
  (``x-resource-types``).  This covers relationships the path tree cannot
  express — e.g. label/milestone writes change issues/pulls because the
  Issue schema references Label/Milestone.

Both parts derive from the same single source of truth as resource
registration (the spec + the resource surface), so a URI change can never
silently break cache invalidation again.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import mcp
    from fastmcp.server.middleware.caching import ResponseCachingMiddleware
    from fastmcp.tools.base import ToolResult

    from gitea_mcp_server.label_service import LabelService
    from gitea_mcp_server.openapi_types import OpenAPISpec
    from gitea_mcp_server.resources.surface import ResourceSurfaceEntry

from fastmcp.server.middleware.caching import (
    _get_auth_partition_key,
)
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)

from gitea_mcp_server.constants import HTTP_METHODS_SAFE
from gitea_mcp_server.resources.surface import get_resource_surface

logger = logging.getLogger(__name__)

# Global invalidation map populated by ``build_invalidation_map`` after
# resource registration.  Maps tool name (bare operationId, not namespaced)
# -> list of resource URI templates (base form, no ``{?query}`` suffix).
TOOL_INVALIDATION_MAP: dict[str, list[str]] = {}

# Write tools recorded during tool customization (``record_write_tool``),
# consumed by ``build_invalidation_map`` after resource registration.
# Each entry is ``(tool_name, path, method)``.
_PENDING_WRITE_TOOLS: list[tuple[str, str, str]] = []

# Namespace prefix applied at query time by GiteaNamespace.  Set at
# middleware construction time from ``config.tool_prefix``; defaults to
# empty string (no prefix) so the module-level constant is never relied on
# at runtime.
_DEFAULT_TOOL_PREFIX = ""


def record_write_tool(tool_name: str, path: str, method: str) -> None:
    """Record a write tool for later invalidation-target derivation.

    Called during server initialization for each non-safe tool.  The
    targets are derived later by :func:`build_invalidation_map`, once the
    resource surface is registered.

    Args:
        tool_name: Name of the tool as registered with FastMCP.
        path: Spec path of the tool's route (e.g. ``/repos/{owner}/{repo}/issues``).
        method: HTTP method (e.g. ``"POST"``).
    """
    if method.upper() in HTTP_METHODS_SAFE:
        return
    _PENDING_WRITE_TOOLS.append((tool_name, path, method.upper()))
    logger.debug("Recorded write tool for invalidation: %s %s %s", method, path, tool_name)


def build_invalidation_map(
    openapi_spec: OpenAPISpec | None,
    resource_surface: dict[str, ResourceSurfaceEntry] | None = None,
) -> None:
    """Derive ``TOOL_INVALIDATION_MAP`` from the spec + registered surface.

    Must run AFTER resource registration (the surface is populated by
    ``make_api_resource``) and BEFORE the first tool call.  Consumes the
    pending write tools recorded by :func:`record_write_tool`.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (may be ``None`` in
            tests — cross-tree derivation is skipped without it).
        resource_surface: The registered resource surface (base URI ->
            entry).  Defaults to the module-level registry.
    """
    surface = resource_surface if resource_surface is not None else get_resource_surface()

    # Precompute each resource's referenced types once (cross-tree lookup).
    resource_types: dict[str, set[str]] = {}
    if openapi_spec is not None:
        for base_uri, entry in surface.items():
            types = _get_resource_types(openapi_spec, entry.api_path)
            if types:
                resource_types[base_uri] = types

    for tool_name, path, method in _PENDING_WRITE_TOOLS:
        targets = _derive_targets(openapi_spec, path, method, surface, resource_types)
        if targets:
            TOOL_INVALIDATION_MAP[tool_name] = targets
            logger.debug(
                "Derived cache invalidation for tool %s: %d target(s)",
                tool_name,
                len(targets),
            )
    _PENDING_WRITE_TOOLS.clear()


def _derive_targets(
    openapi_spec: OpenAPISpec | None,
    write_path: str,
    method: str,
    surface: dict[str, ResourceSurfaceEntry],
    resource_types: dict[str, set[str]],
) -> list[str]:
    """Compute the invalidation URI templates for one write tool.

    Path-prefix (full prefix, no exceptions) plus cross-tree type
    references.  Returns sorted base URI templates.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (may be ``None``).
        write_path: Spec path of the write tool's route.
        method: HTTP method (UPPER).
        surface: Registered resource surface (base URI -> entry).
        resource_types: Precomputed ``base_uri -> referenced types`` map.

    Returns:
        Sorted list of base URI templates to invalidate.
    """
    targets: set[str] = set()

    # Path-prefix: every registered resource whose api_path is a prefix of
    # (or equal to) the write path — template-aware, full prefix.
    for base_uri, entry in surface.items():
        if _path_is_prefix(entry.api_path, write_path):
            targets.add(base_uri)

    # Cross-tree: every registered resource whose response schema
    # references the type this write modifies.
    if openapi_spec is not None:
        modified = _get_modified_type(openapi_spec, write_path, method)
        if modified:
            for base_uri, types in resource_types.items():
                if modified in types:
                    targets.add(base_uri)

    return sorted(targets)


# ---------------------------------------------------------------------------
# Path matching helpers (template-aware)
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> list[str]:
    """Split a path into segments, normalizing ``{param}``/``{param*}`` to ``*``.

    ``{param}`` and ``{param*}`` are equivalent for matching purposes — the
    wildcard only affects routing (multi-segment values), not the logical
    path shape.
    """
    return ["*" if seg.startswith("{") and seg.endswith("}") else seg for seg in path.split("/")]


def _path_is_prefix(resource_path: str, write_path: str) -> bool:
    """True if ``resource_path`` is a prefix of (or equal to) ``write_path``.

    Template-aware: ``{param}``/``{param*}`` on either side match any
    segment.  A resource whose api_path is a prefix of the write path is
    invalidated by that write (full-prefix semantics — no exceptions).

    Args:
        resource_path: The resource's api_path (may be a template or a
            concrete path, e.g. the readme wrapper).
        write_path: The write tool's spec path (a template).

    Returns:
        ``True`` when the resource path is a prefix of the write path.
    """
    r = _normalize_path(resource_path)
    w = _normalize_path(write_path)
    if len(r) > len(w):
        return False
    return all(rs in (ws, "*") or ws == "*" for rs, ws in zip(r, w))


def _paths_equivalent(a: str, b: str) -> bool:
    """True if two paths match segment-wise, treating ``{param}`` as wildcards."""
    na, nb = _normalize_path(a), _normalize_path(b)
    if len(na) != len(nb):
        return False
    return all(x in (y, "*") or y == "*" for x, y in zip(na, nb))


def _find_spec_path(openapi_spec: OpenAPISpec, api_path: str) -> str | None:
    """Find the spec path template matching a resource's api_path.

    Most api_paths ARE spec paths (exact dict lookup).  Convenience
    wrappers with concrete api_paths (e.g. the readme wrapper's
    ``/repos/{owner}/{repo}/contents/README.md``) fall back to template
    matching.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        api_path: The resource's api_path.

    Returns:
        The matching spec path template, or ``None``.
    """
    paths: dict[str, Any] = openapi_spec.get("paths", {}) or {}
    if api_path in paths:
        return api_path
    for spec_path in paths:
        if _paths_equivalent(api_path, spec_path):
            return spec_path
    return None


def _get_modified_type(openapi_spec: OpenAPISpec, write_path: str, method: str) -> str | None:
    """Read ``x-modifies-type`` from the write operation (stamped pre-wrap).

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        write_path: Spec path of the write tool's route.
        method: HTTP method (UPPER).

    Returns:
        The modified type name, or ``None``.
    """
    path_item = openapi_spec.get("paths", {}).get(write_path)
    if not isinstance(path_item, dict):
        return None
    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return None
    modified = operation.get("x-modifies-type")
    return modified if isinstance(modified, str) else None


def _get_resource_types(openapi_spec: OpenAPISpec, api_path: str) -> set[str]:
    """Read ``x-resource-types`` from the GET operation matching ``api_path``.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec.
        api_path: The resource's api_path (template or concrete).

    Returns:
        Set of type names the resource's response references.
    """
    spec_path = _find_spec_path(openapi_spec, api_path)
    if spec_path is None:
        return set()
    path_item = openapi_spec.get("paths", {}).get(spec_path)
    if not isinstance(path_item, dict):
        return set()
    operation = path_item.get("get")
    if not isinstance(operation, dict):
        return set()
    types = operation.get("x-resource-types")
    return set(types) if isinstance(types, list) else set()


# ---------------------------------------------------------------------------
# Cache key computation and invalidation
# ---------------------------------------------------------------------------


def _compute_cache_key(uri: str, auth_key: str | None = None) -> str:
    """Compute the cache key for a resource URI using SHA256.

    This mirrors FastMCP's ``_make_read_resource_cache_key`` to ensure we
    compute the exact same key that the caching middleware uses.  The cache
    key is ``sha256(f"{auth_key}:{uri}")`` - the auth partition prevents
    per-token response filtering from leaking across users.

    Args:
        uri: The resource URI
        auth_key: Auth partition key.  If ``None``, the current request's
            auth key is fetched via ``_get_auth_partition_key()`` (which
            returns ``__anonymous__`` for STDIO / unauthenticated callers).

    Returns:
        Hex digest of SHA256 hash
    """
    if auth_key is None:
        auth_key = _get_auth_partition_key()
    return hashlib.sha256(f"{auth_key}:{uri}".encode()).hexdigest()


def _substitute_template(template: str, params: dict[str, Any]) -> str:
    """Substitute parameters into a URI template.

    Args:
        template: URI template with {placeholders}
        params: Dictionary of parameter values

    Returns:
        URI with placeholders replaced

    Raises:
        ValueError: If required parameters are missing
    """
    # Find all parameter names in the template (handle {param} and {param*})
    param_names = re.findall(r"\{(\w+)(?:\*)?\}", template)

    # Check for missing required parameters
    missing = [p for p in param_names if p not in params]
    if missing:
        msg = f"Missing parameters for URI template: {missing}"
        raise ValueError(msg)

    # Replace each parameter
    result = template
    for param in param_names:
        # Check if the template uses wildcard syntax {param*}
        placeholder_with_asterisk = f"{{{param}*}}"
        placeholder_standard = f"{{{param}}}"

        if placeholder_with_asterisk in template:
            value = str(params.get(param, ""))
            result = result.replace(placeholder_with_asterisk, value)
        elif placeholder_standard in template:
            value = str(params.get(param, ""))
            result = result.replace(placeholder_standard, value)

    return result


def compute_uris_to_invalidate(
    tool_name: str,
    arguments: dict[str, Any],
    tool_prefix: str = _DEFAULT_TOOL_PREFIX,
) -> list[str]:
    """Compute the list of concrete resource URIs to invalidate for a tool call.

    Lookup tries the exact name first, then strips the configured namespace
    prefix if present - the map is keyed by bare ``operationId`` while the
    middleware receives the namespaced name at runtime.

    Args:
        tool_name: Name of the tool being called
        arguments: Arguments passed to the tool
        tool_prefix: Configured namespace prefix (e.g. ``"gitea_"``).
            When non-empty and ``tool_name`` starts with it, the prefix is
            stripped before the map lookup.  Empty string means no prefix.

    Returns:
        List of concrete resource URIs to invalidate
    """
    if tool_name not in TOOL_INVALIDATION_MAP:
        if tool_prefix and tool_name.startswith(tool_prefix):
            stripped = tool_name[len(tool_prefix) :]
            if stripped in TOOL_INVALIDATION_MAP:
                tool_name = stripped
            else:
                return []
        else:
            return []

    templates = TOOL_INVALIDATION_MAP[tool_name]
    uris = []

    for template in templates:
        try:
            uri = _substitute_template(template, arguments)
            uris.append(uri)
        except ValueError as e:
            logger.debug(
                "Skipping invalidation for template %s: %s",
                template,
                e,
            )

    return uris


async def invalidate_cached_resources(
    caching_middleware: ResponseCachingMiddleware, uris: list[str], tool_name: str = ""
) -> None:
    """Invalidate cached resource responses for the given URIs.

    Args:
        caching_middleware: The ResponseCachingMiddleware instance
        uris: List of resource URIs to invalidate
        tool_name: Optional tool name for logging
    """
    if not uris:
        return

    # FastMCP does not expose a public API for cache invalidation yet.
    # Access _read_resource_cache with graceful degradation in case the
    # private attribute changes in a future FastMCP version.
    cache_adapter = getattr(caching_middleware, "_read_resource_cache", None)
    if cache_adapter is None:
        logger.warning(
            "Cache invalidation unavailable: "
            "ResponseCachingMiddleware._read_resource_cache not found. "
            "This may be caused by a FastMCP version upgrade."
        )
        return

    deleted_count = 0

    for uri in uris:
        cache_key = _compute_cache_key(uri)
        try:
            existing = await cache_adapter.get(key=cache_key)
            if existing is not None:
                await cache_adapter.delete(key=cache_key)
                deleted_count += 1
                logger.debug(
                    "Invalidated cached resource: uri=%s, cache_key=%s, tool=%s",
                    uri,
                    cache_key[:16],
                    tool_name,
                )
        except (KeyError, ValueError) as e:
            logger.warning(
                "Failed to invalidate cache for URI %s: %s",
                uri,
                e,
            )

    if deleted_count > 0:
        logger.info(
            "Cache invalidation: removed %d cached resource(s) for tool %s",
            deleted_count,
            tool_name,
        )


class CacheInvalidationMiddleware(Middleware):
    """Middleware that invalidates cached resources after write operations.

    This middleware intercepts tool calls and, after successful execution,
    invalidates any cached resources that may have been affected by the write.
    It uses the global TOOL_INVALIDATION_MAP to determine which resources
    to clear based on the tool name and arguments.

    It also observes resource reads (``on_read_resource``) to record
    query-variant URIs (e.g. ``gitea://.../issues?state=open``) under their
    base URI.  The cache is keyed by the full URI including the query
    string, so a write must clear every variant that has been read — not
    just the base URI.

    The middleware must be added AFTER the ResponseCachingMiddleware so that
    it can access and modify the cache.
    """

    def __init__(
        self,
        caching_middleware: ResponseCachingMiddleware,
        label_service: LabelService | None = None,
        tool_prefix: str = _DEFAULT_TOOL_PREFIX,
    ):
        """Initialize with a reference to the caching middleware.

        Args:
            caching_middleware: The response caching middleware whose
                               cache should be invalidated
            label_service: Optional LabelService to clear label caches on
                          label write operations.
            tool_prefix: Configured namespace prefix (e.g. ``"gitea_"``).
                Used to strip the prefix from tool names before looking up
                the invalidation map.  Empty string means no prefix.
        """
        self.caching_middleware = caching_middleware
        self._label_service = label_service
        self._tool_prefix = tool_prefix
        # base URI -> set of full concrete URIs read (including query
        # strings).  Populated by on_read_resource; consumed on write to
        # clear query-variant cache entries the base URI alone cannot reach.
        self._read_uris: dict[str, set[str]] = defaultdict(set)

    async def on_read_resource(
        self,
        context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
        call_next: CallNext[mcp.types.ReadResourceRequestParams, Any],
    ) -> Any:
        """Record the read URI so query variants can be invalidated later.

        The cache key includes the full URI (query string included), so a
        write must clear every variant that has been read.  Recording here
        is the only robust way to know which variants exist — the cache
        adapter offers no iteration.

        Args:
            context: The read context with the resource URI.
            call_next: The next middleware/resource in the chain.

        Returns:
            The resource result (unchanged).
        """
        uri = str(context.message.uri)
        if uri:
            base = uri.split("?", 1)[0]
            self._read_uris[base].add(uri)
        return await call_next(context)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
        call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls to invalidate cache after successful writes.

        Args:
            context: The call context with tool name and arguments
            call_next: The next middleware/tool in the chain

        Returns:
            The tool result
        """
        tool_name = context.message.name
        arguments = context.message.arguments or {}

        # Execute the tool
        result = await call_next(context)

        # NOTE: use getattr for backward compat with fastmcp <3.4.0
        # where ToolResult does not have an is_error attribute.
        if result and not getattr(result, "is_error", False):
            uris_to_invalidate = compute_uris_to_invalidate(
                tool_name,
                arguments,
                tool_prefix=self._tool_prefix,
            )
            if uris_to_invalidate:
                # Expand with query variants recorded at read time: the
                # cache key includes the query string, so the base URI
                # alone would leave variants stale.
                expanded = set(uris_to_invalidate)
                for base in uris_to_invalidate:
                    expanded |= self._read_uris.get(base, set())
                await invalidate_cached_resources(
                    self.caching_middleware, sorted(expanded), tool_name
                )
                # If any URI targets the labels resource, also clear the
                # LabelService's internal cache for this repo.
                if self._label_service is not None:
                    self._clear_label_service_cache(uris_to_invalidate, arguments)

        return result

    def _clear_label_service_cache(
        self,
        uris: list[str],
        arguments: dict[str, Any],  # noqa: ARG002
    ) -> None:
        """Clear LabelService cache for any label resource URIs in the list.

        Owner/repo are extracted from the URI path directly - the ``arguments``
        parameter is accepted only for API consistency and is unused.

        Args:
            uris: List of resolved resource URIs that were invalidated.
            arguments: Tool arguments used to resolve the URIs (unused).
        """
        if self._label_service is None:
            return
        # URIs follow the resolved pattern: gitea://repos/{owner}/{repo}/labels
        # Parse directly from the path rather than trying to match templates
        # with substituted parameters.
        _MIN_LABEL_URI_PARTS = 6
        for uri in uris:
            if not uri.endswith("/labels"):
                continue
            parts = uri.split("/")
            # ['gitea:', '', 'repos', owner, repo, 'labels']
            if len(parts) >= _MIN_LABEL_URI_PARTS and parts[1] == "" and parts[2] == "repos":
                owner = parts[3]
                repo = parts[4]
                if owner and repo:
                    self._label_service.clear_cache_for(owner, repo)
