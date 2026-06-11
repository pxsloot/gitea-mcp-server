"""Extension metadata transform — applies YAML extension overrides to tool metadata at query time.

Works *with* FastMCP's transform pattern: intercepts ``list_tools`` and
``get_tool`` to patch tool descriptions, titles, tags, and annotation
hints from the extensions config, supporting both prefixed and
unprefixed tool names.
"""

from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool, ToolAnnotations
from fastmcp.utilities.versions import VersionSpec

# Fields that live directly on FastMCPComponent/Tool (title, description, tags).
# Note: tags comes from YAML as a list[str] but Tool accepts set[str]
# via Pydantic's BeforeValidator, so we pass it through as-is.
_COMPONENT_FIELDS = {"title", "description", "tags"}

# Fields that live inside ToolAnnotations (the hints documented in
# mcp_extensions.yaml under "Annotation Overrides").
_ANNOTATION_FIELDS = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}


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
        for field in _COMPONENT_FIELDS:
            if field in override:
                value = override[field]
                if field == "tags":
                    value = set(value)
                updates[field] = value
        annotation_overrides = {k: v for k, v in override.items() if k in _ANNOTATION_FIELDS}
        if annotation_overrides:
            current = tool.annotations.model_dump() if tool.annotations else {}
            current.update(annotation_overrides)
            updates["annotations"] = ToolAnnotations(**current)
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
