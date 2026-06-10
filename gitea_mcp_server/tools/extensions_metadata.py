"""Extension metadata transform — applies YAML extension overrides to tool metadata at query time.

Works *with* FastMCP's transform pattern: intercepts ``list_tools`` and
``get_tool`` to patch tool descriptions and titles from the extensions
config, supporting both prefixed and unprefixed tool names.
"""

from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec


class ExtensionMetadataTransform(Transform):
    """Apply YAML extension overrides to tool metadata at query time.

    Matches tool names against the extensions dict using both prefixed
    and unprefixed forms so it works correctly regardless of whether
    a ``Namespace`` transform has already run.

    Args:
        tool_names: Dict mapping tool names to override metadata,
            e.g. ``{"search": {"description": "...", "title": "..."}}``.
        prefix: Namespace prefix (e.g. ``"gitea_"``) to also match prefixed names.
    """

    def __init__(self, tool_names: dict[str, dict[str, Any]], prefix: str = "") -> None:
        self._lookup: dict[str, dict[str, Any]] = {}
        for name, meta in tool_names.items():
            self._lookup[name] = meta
            if prefix:
                self._lookup[f"{prefix}{name}"] = meta

    def _apply(self, tool: Tool) -> Tool:
        override = self._lookup.get(tool.name)
        if override is None:
            return tool
        updates: dict[str, Any] = {}
        if "description" in override:
            updates["description"] = override["description"]
        return tool.model_copy(update=updates)

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._apply(t) for t in tools]

    async def get_tool(
        self, name: str, call_next: Any, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if tool is None:
            return None
        return self._apply(tool)


__all__ = ["ExtensionMetadataTransform"]
