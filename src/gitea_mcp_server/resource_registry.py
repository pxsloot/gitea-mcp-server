"""Resource Registry for centralized resource management."""

from dataclasses import dataclass
from typing import Any, Callable

from fastmcp import FastMCP


@dataclass
class ResourceDef:
    """Definition of a resource.

    Attributes:
        uri: The resource URI template (e.g., "gitea://repos/{owner}/{repo}")
        func: The async function that returns the resource content
        mime_type: The MIME type of the resource (e.g., "application/json", "text/markdown")
        tags: Set of tags for categorizing resources
        meta: Optional metadata dictionary (e.g., cache_ttl)
    """

    uri: str
    func: Callable
    mime_type: str
    tags: set[str]
    meta: dict[str, Any] | None = None


class ResourceRegistry:
    """Centralized registry for MCP resources.

    Allows registration, lookup, and listing of resources.Provides an
    apply_to method to register all resources with a FastMCP instance.
    """

    def __init__(self) -> None:
        self._resources: dict[str, ResourceDef] = {}

    def register(
        self,
        uri: str,
        func: Callable,
        mime_type: str,
        tags: set[str],
        meta: dict[str, Any] | None = None,
        allow_override: bool = False,
    ) -> None:
        """Register a resource.

        Args:
            uri: Resource URI template
            func: Resource function
            mime_type: MIME type
            tags: Set of tags
            meta: Optional metadata
            allow_override: If True, allow overwriting existing resource with same URI

        Raises:
            ValueError: If URI already registered and allow_override is False
        """
        if uri in self._resources and not allow_override:
            raise ValueError(f"Resource with URI '{uri}' is already registered")
        self._resources[uri] = ResourceDef(uri, func, mime_type, tags, meta)

    def get(self, uri: str) -> ResourceDef | None:
        """Get a resource definition by URI."""
        return self._resources.get(uri)

    def list_resources(self) -> list[ResourceDef]:
        """List all registered resources."""
        return list(self._resources.values())

    def list_templates(self) -> list[ResourceDef]:
        """List resource templates (URIs containing at least one '{')."""
        return [r for r in self._resources.values() if "{" in r.uri]

    def get_by_tag(self, tag: str) -> list[ResourceDef]:
        """Get all resources that have the given tag."""
        return [r for r in self._resources.values() if tag in r.tags]

    def apply_to(self, mcp: FastMCP) -> None:
        """Register all resources with a FastMCP instance."""
        for resource in self._resources.values():
            # Build kwargs: mime_type, tags, and optionally meta
            kwargs: dict[str, Any] = {"mime_type": resource.mime_type, "tags": resource.tags}
            if resource.meta is not None:
                kwargs["meta"] = resource.meta
            mcp.resource(resource.uri, **kwargs)(resource.func)
