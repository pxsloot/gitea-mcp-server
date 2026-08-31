"""Registered resource surface — the single source of truth for invalidation targets.

Every resource registered via ``make_api_resource`` records its addressing
facts (URI template + spec api_path) here.  Cache invalidation derives its
targets from this registry instead of hardcoded URI templates, so a URI
change can never silently break invalidation again (issue #743).

The registry is populated at resource registration time (custom wrappers
first, then auto-generated resources) and consumed by
``cache_invalidation.build_invalidation_map`` after registration completes.
Static resources (``gitea://version``, ``gitea://token/scopes``,
``gitea://server/info``) are not registered — their content is session-static
and never invalidated by writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from gitea_mcp_server.uri_utils import clean_resource_uri


@dataclass(frozen=True)
class ResourceSurfaceEntry:
    """Addressing facts for one registered resource."""

    uri_template: str
    """Full URI template as registered (may carry a ``{?query}`` suffix)."""

    api_path: str
    """Spec path the resource mirrors (e.g. ``/repos/{owner}/{repo}/issues``)."""

    method: str = "GET"
    """HTTP method of the underlying operation (always ``"GET"`` for resources)."""

    @property
    def base_uri(self) -> str:
        """URI template without the ``{?query}`` suffix — the invalidation target."""
        return clean_resource_uri(self.uri_template)


# Module-level registry: base URI template -> entry.
# Populated at resource registration time; consumed by the invalidation
# derivation after registration completes.
RESOURCE_SURFACE: dict[str, ResourceSurfaceEntry] = {}


def register_resource_surface(uri_template: str, api_path: str, method: str = "GET") -> None:
    """Record a registered resource in the surface registry.

    Args:
        uri_template: Full URI template as registered with FastMCP.
        api_path: Spec path the resource mirrors.
        method: HTTP method of the underlying operation (default ``"GET"``).
    """
    entry = ResourceSurfaceEntry(uri_template=uri_template, api_path=api_path, method=method)
    RESOURCE_SURFACE[entry.base_uri] = entry


def get_resource_surface() -> dict[str, ResourceSurfaceEntry]:
    """Return the registered resource surface (base URI -> entry)."""
    return RESOURCE_SURFACE


def clear_resource_surface() -> None:
    """Clear the registry (test isolation)."""
    RESOURCE_SURFACE.clear()


__all__ = [
    "RESOURCE_SURFACE",
    "ResourceSurfaceEntry",
    "clear_resource_surface",
    "get_resource_surface",
    "register_resource_surface",
]
