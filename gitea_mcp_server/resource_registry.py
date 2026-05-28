"""Passive catalog for tracking registered MCP resources.

This registry does **not** perform registration; it simply records metadata
about resources that have already been registered with FastMCP via
``mcp.resource()``.

It is used by ``resources.py`` to track what has been registered and by
``server_setup/resource_registry.py`` to orchestrate the two registration
phases (auto-generated then custom overrides).

Usage:
    registry = ResourceRegistry()
    # After calling mcp.resource()(...):
    registry.record(uri, func, mime_type, tags, meta)
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ResourceDef:
    """Metadata for a registered MCP resource."""

    uri: str
    func: Callable[..., str]
    mime_type: str
    tags: set[str]
    meta: dict[str, Any] | None = None


class ResourceRegistry:
    """A catalog of registered resources for querying and documentation."""

    def __init__(self) -> None:
        self._defs: dict[str, ResourceDef] = {}

    def record(
        self,
        uri: str,
        func: Callable,
        mime_type: str,
        tags: set[str],
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Record a resource that has been registered with FastMCP.

        Args:
            uri: The resource URI template
            func: The async function that returns the resource content
            mime_type: MIME type of the resource
            tags: Set of tags for categorization
            meta: Optional metadata dict (e.g., cache_ttl)
        """
        self._defs[uri] = ResourceDef(uri, func, mime_type, tags, meta)

    def list_resources(self) -> list[ResourceDef]:
        """List all registered resources."""
        return list(self._defs.values())

    def get_by_tag(self, tag: str) -> list[ResourceDef]:
        """Get resources that have the specified tag."""
        return [r for r in self._defs.values() if tag in r.tags]

    def get_by_uri(self, uri: str) -> ResourceDef | None:
        """Get a resource definition by URI."""
        return self._defs.get(uri)
