"""Unified search across tools, docs, and resources.

Provides a single ``search`` tool that queries all three subsystems
(tools, workflow docs, MCP resources) and returns merged results
with a ``type`` discriminator so agents can route each result
to the appropriate access path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from gitea_mcp_server.docs_tools import DocManager

from fastmcp.server.context import Context  # noqa: TC002 - runtime use via get_type_hints
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.constants import SEARCH_MIN_SCORE
from gitea_mcp_server.format import _format_paginated_result
from gitea_mcp_server.mcp_tools import _mcp_list_resources_impl
from gitea_mcp_server.models import UnifiedSearchItem
from gitea_mcp_server.tools.customize import synthetic_annotations
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
    tool_prefix: str = "",
) -> None:
    """Register the unified ``search`` tool.

    Args:
        mcp: The FastMCP server instance
        doc_manager: DocManager with loaded workflow guides
        search_transform: TolerantSearchTransform for tool catalog access
        tool_prefix: Configured namespace prefix (e.g. ``"gitea_"``).
            Used to strip the prefix from tool/resource names before name matching.
    """

    async def search(  # noqa: PLR0913 - min_score is a new config axis
        query: Annotated[str, "Natural language query to search for tools, docs, and resources"],
        format: Annotated[
            str,
            "Output format: markdown (default, human-readable), json (structured data), or raw",
        ] = "markdown",
        page: Annotated[int, "Page number (1-based, default 1)"] = 1,
        limit: Annotated[int, "Maximum results per page (1-100, default 10)"] = 10,
        min_score: Annotated[
            float,
            "Minimum relevance score (0.0-1.0). 0.0 returns everything, "
            "0.1 requires at least 10% as relevant as the top result, "
            "1.0 requires perfect match.",
        ] = SEARCH_MIN_SCORE,
        fetch_all: Annotated[
            bool,
            "When true, return all matching results instead of a single page. "
            "Results are merged into one response (in-memory, no looping needed).",
        ] = False,
        detail: Annotated[
            str,
            'Output detail level.  "full" (default) — complete information, '
            'full object expansion.  "concise" — compact view: nested objects '
            "are collapsed to type labels ($ref:TypeName) at depth > 0.",
        ] = "full",
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
                UnifiedSearchItem(
                    type="tool",
                    name=t["name"],
                    description=t.get("description", ""),
                    tags=t.get("tags", []),
                    access_uri=t["name"],
                )
            )
            all_texts.append(tool_search_texts[i])

        for r in resource_entries:
            all_items.append(
                UnifiedSearchItem(
                    type="resource",
                    name=r.get("name", ""),
                    description=r.get("description", ""),
                    tags=r.get("tags", []),
                    uri=r.get("uri", ""),
                    access_uri=r.get("uri", ""),
                )
            )
            all_texts.append(_extract_resource_text(r))

        for d in doc_entries:
            topic = d["name"]
            all_items.append(
                UnifiedSearchItem(
                    type="doc",
                    name=topic,
                    title=d.get("title", ""),
                    description=d.get("description", ""),
                    tags=d.get("tags", []),
                    access_uri=f"gitea://docs/guide/{topic}",
                )
            )
            all_texts.append(_extract_doc_search_text(d))

        # Get all ranked results (no pre-slicing).
        all_ranked, total_count = _search_and_slice(
            all_items, all_texts, query, 1, len(all_items) or 1,
            min_score=min_score, tool_prefix=tool_prefix,
        )

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

        # Check page range before formatting (only when paginating, not fetch_all).
        if not fetch_all:
            start = (page - 1) * limit
            if start >= total_count:
                hint = f"Page {page} is out of range (total results: {total_count})."
                return ToolResult(
                    content=[TextContent(type="text", text=hint)],
                    structured_content={"result": [], "_hint": hint},
                )

        extras: list[str] = []
        if format == "markdown":
            extras.append(
                "**Cross-linking hints:**\n"
                "- For API tools: `search_tools(query)`\n"
                "- For workflow guides: `search_docs(query)`\n"
                "- For data resources: `search_resources(query)`"
            )

        return _format_paginated_result(
            all_ranked, total_count, format, page, limit, fetch_all,
            markdown_extras=extras or None,
            detail=detail,
        )

    mcp.tool(
        name="search",
        description="Unified search across tools, workflow docs, and data resources. Returns merged results ranked by name-match then BM25 with a type discriminator (tool/doc/resource) so you can route each hit to the right access path.",
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
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
                            "score": {
                                "type": "number",
                                "description": "Normalized relevance score (0.0-1.0). "
                                "1.0 is the top match for this query.",
                            },
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
                        "example": {
                            "type": "tool",
                            "name": "gitea_issue_create_issue",
                            "description": "Create a new issue in a repository",
                            "tags": ["issue"],
                            "score": 1.0,
                            "access_uri": "gitea_issue_create_issue",
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
