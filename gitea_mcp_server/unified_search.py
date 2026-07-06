"""Unified search across tools, docs, and resources.

Provides a single ``search`` tool that queries all three subsystems
(tools, workflow docs, MCP resources) and returns merged results
with a ``type`` discriminator so agents can route each result
to the appropriate access path.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from gitea_mcp_server.models import UnifiedSearchItem

from fastmcp.server.context import Context  # noqa: TC002 — runtime use via get_type_hints
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations
from mcp.types import TextContent

from gitea_mcp_server.docs_tools import DocManager  # noqa: TC001 — runtime use via get_type_hints
from gitea_mcp_server.format import _format_as_markdown
from gitea_mcp_server.mcp_tools import _mcp_list_resources_impl
from gitea_mcp_server.pagination import PAGINATION_KEYS, add_pagination_metadata
from gitea_mcp_server.tools.search import (
    TolerantSearchTransform,
    _compact_search_serializer,
    _extract_resource_text,
    _extract_searchable_text_enhanced,
    _search_and_slice,
)

logger = logging.getLogger(__name__)


def _extract_doc_search_text(doc: Mapping[str, Any]) -> str:
    """Build searchable text from a doc entry."""
    parts = [doc["name"]] * 3
    if doc.get("title"):
        parts.append(doc["title"])
    if doc.get("description"):
        parts.append(doc["description"])
    for tag in doc.get("tags", []):
        parts.append(tag)
    return " ".join(parts)


def register_unified_search(
    mcp: Any,
    doc_manager: DocManager,
    search_transform: TolerantSearchTransform,
) -> None:
    """Register the unified ``search`` tool.

    Args:
        mcp: The FastMCP server instance
        doc_manager: DocManager with loaded workflow guides
        search_transform: TolerantSearchTransform for tool catalog access
    """

    async def search(
        query: Annotated[str, "Natural language query to search for tools, docs, and resources"],
        format: Annotated[
            str,
            "Output format: markdown (default, human-readable), json (structured data), or raw",
        ] = "markdown",
        page: Annotated[int, "Page number (1-based, default 1)"] = 1,
        limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        ctx: Context | None = None,
    ) -> ToolResult:
        if ctx is None:
            msg = "Context is required"
            raise ValueError(msg)

        # Gather results from all three subsystems
        raw_tools: Sequence[Tool] = await search_transform.get_tool_catalog(ctx)
        tool_entries = _compact_search_serializer(raw_tools)

        raw_resources = await _mcp_list_resources_impl(ctx)
        resource_entries = raw_resources.get("resources", [])

        doc_entries = doc_manager.search(
            query, max_results=len(doc_manager.guides) if doc_manager.guides else 0
        )

        # Build unified corpus with type discriminator
        all_items: list[UnifiedSearchItem] = []
        all_texts: list[str] = []

        # Use _extract_searchable_text_enhanced on raw Tool objects for richer signal
        # (parameter names, descriptions, SEARCH_CATEGORY_ALIASES expansion) but keep
        # _compact_search_serializer dicts for lighter result items.
        tool_search_texts = [_extract_searchable_text_enhanced(t) for t in raw_tools]

        for i, t in enumerate(tool_entries):
            all_items.append(
                {
                    "type": "tool",
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "tags": t.get("tags", []),
                    "access_uri": t["name"],
                }
            )
            all_texts.append(tool_search_texts[i])

        for r in resource_entries:
            all_items.append(
                {
                    "type": "resource",
                    "name": r.get("name", ""),
                    "description": r.get("description", ""),
                    "tags": r.get("tags", []),
                    "uri": r.get("uri", ""),
                    "access_uri": r.get("uri", ""),
                }
            )
            all_texts.append(_extract_resource_text(r))

        for d in doc_entries:
            topic = d["name"]
            all_items.append(
                {
                    "type": "doc",
                    "name": topic,
                    "title": d.get("title", ""),
                    "description": d.get("description", ""),
                    "tags": d.get("tags", []),
                    "access_uri": f"gitea://docs/guide/{topic}",
                }
            )
            all_texts.append(_extract_doc_search_text(d))

        page_items, total_count = _search_and_slice(all_items, all_texts, query, page, limit)

        if total_count == 0:
            hint = (
                f"No results found for '{query}'.\n\n"
                "**Cross-linking hints:**\n"
                "- For API tools: `search_tools(query)`\n"
                "- For workflow guides: `search_docs(query)`\n"
                "- For data resources: `search_resources(query)`"
            )
            return ToolResult(
                content=[TextContent(type="text", text=hint)],
                structured_content={"result": [], "_hint": hint},
            )

        if not page_items:
            hint = f"Page {page} is out of range (total results: {total_count})."
            return ToolResult(
                content=[TextContent(type="text", text=hint)],
                structured_content={"result": [], "_hint": hint},
            )

        structured = {"result": page_items}
        if format == "raw":
            enhanced = add_pagination_metadata(structured, page, limit, total_count)
            return ToolResult(structured_content=enhanced)

        serialized = (
            json.dumps(page_items, indent=2)
            if format == "json"
            else _format_as_markdown(page_items, None)
        )

        if format == "markdown":
            pagination_info = {
                k: v
                for k, v in add_pagination_metadata(structured, page, limit, total_count).items()
                if k in PAGINATION_KEYS
            }
            serialized += "\n\n---\n"
            serialized += _format_as_markdown(pagination_info, None)

        enhanced = add_pagination_metadata(structured, page, limit, total_count)
        return ToolResult(
            content=[TextContent(type="text", text=serialized)],
            structured_content=enhanced,
        )

    mcp.tool(
        name="search",
        description="Unified search across tools, workflow docs, and data resources. Returns merged BM25-ranked results with a type discriminator (tool/doc/resource) so you can route each hit to the right access path.",
        tags={"synthetic"},
        annotations=ToolAnnotations(openWorldHint=False),
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "description": "One of: tool, doc, resource",
                            },
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "access_uri": {
                                "type": "string",
                                "description": "How to access this item",
                            },
                            "uri": {
                                "type": "string",
                                "description": "Resource URI (resource results only)",
                            },
                            "title": {
                                "type": "string",
                                "description": "Doc title (doc results only)",
                            },
                        },
                    },
                    "description": "Merged results across tools, docs, and resources",
                },
            },
        },
    )(search)

    logger.info("Registered unified search tool")


__all__ = [
    "register_unified_search",
]
