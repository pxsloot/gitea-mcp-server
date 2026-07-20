"""Doc tools and resources for workflow guides.

Provides search_docs and read_doc tools, plus gitea://docs/guide/{topic} resources.
Guides are markdown files in gitea_mcp_server/docs/guides/.
"""

from __future__ import annotations

import logging
from importlib.resources import files as pkg_files
from typing import TYPE_CHECKING, Any

import yaml
from fastmcp.exceptions import ResourceError
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.constants import SEARCH_MIN_SCORE
from gitea_mcp_server.format import _format_as_markdown, apply_format
from gitea_mcp_server.models import DocEntry
from gitea_mcp_server.pagination import PAGINATION_KEYS, add_pagination_metadata, apply_pagination
from gitea_mcp_server.search import BM25SearchEngine
from gitea_mcp_server.tools.customize import synthetic_annotations

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

SEARCH_MAX_RESULTS = 10
"""Maximum number of guide search results."""

_DESC_TRUNCATE = 80
"""Max description length before truncation in manifest."""

_FRONTMATTER_SPLIT_LIMIT = 2
"""Maximum number of splits when parsing frontmatter (---/content/--- -> 3 expected parts)."""

_VALID_FORMATS = frozenset({"markdown", "raw", "json"})
"""Accepted format parameter values for doc tools."""

_SEARCH_DOCS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "result": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "example": {
                    "name": "token-scopes",
                    "title": "Token Scopes",
                    "description": "How Gitea/Forgejo API tokens work, the scope model, and repository access restrictions",
                    "tags": ["auth", "security", "tokens"],
                },
            },
            "description": "Matching guide definitions ranked by relevance",
        },
    },
}


class DocGuide:
    """Represents a single workflow guide."""

    def __init__(  # noqa: PLR0913 -- data class with 6 fields
        self,
        name: str,
        title: str,
        description: str,
        tags: list[str],
        full_content: str,
        markdown_body: str,
    ) -> None:
        self.name = name
        self.title = title
        self.description = description
        self.tags = list(tags)
        self.full_content = full_content
        self.markdown_body = markdown_body

    def search_text(self) -> str:
        """Text for BM25 search indexing."""
        parts = [self.name] * 3
        parts.append(self.title)
        parts.append(self.description)
        parts.extend(self.tags)
        parts.append(self.markdown_body)
        return " ".join(parts)


class DocManager:
    """Manages workflow guide discovery and retrieval.

    Loads guide markdown files from the package's docs/guides/ directory
    at initialization and provides search and read capabilities.
    """

    def __init__(self) -> None:
        self._guides: list[DocGuide] = []
        self._search_texts: list[str] = []
        self._search_engine = BM25SearchEngine()
        self._load()

    def _load(self) -> None:
        """Load all guide files from the package's docs/guides/ directory."""
        try:
            guides_dir = pkg_files("gitea_mcp_server").joinpath("docs/guides")
            if not guides_dir.is_dir():
                logger.warning("Guides directory not found: %s", guides_dir)
                return
            entries = sorted(guides_dir.iterdir(), key=lambda e: e.name)
            for entry in entries:
                if not entry.name.endswith(".md") or not entry.is_file():
                    continue
                raw = entry.read_text(encoding="utf-8")
                guide = self._parse_guide(entry.name[:-3], raw)
                if guide:
                    self._guides.append(guide)
            self._search_texts = [g.search_text() for g in self._guides]
            logger.info("Loaded %d workflow guides", len(self._guides))
        except Exception:
            logger.exception("Failed to load workflow guides")

    @staticmethod
    def _parse_guide(name: str, raw: str) -> DocGuide | None:
        """Parse a guide file with optional YAML frontmatter."""
        if raw.startswith("---"):
            parts = raw.split("---", _FRONTMATTER_SPLIT_LIMIT)
            if len(parts) >= _FRONTMATTER_SPLIT_LIMIT + 1:
                frontmatter = parts[1].strip()
                markdown_body = parts[2].strip()
                try:
                    meta = yaml.safe_load(frontmatter) or {}
                except yaml.YAMLError:
                    logger.warning("Invalid frontmatter in guide '%s'", name)
                    meta = {}
                title = meta.get("title", name.replace("-", " ").title())
                description = meta.get("description", "")
                tags = meta.get("tags", [])
                if isinstance(tags, str):
                    tags = [tags]
                return DocGuide(
                    name=name,
                    title=str(title),
                    description=str(description),
                    tags=list(tags),
                    full_content=raw,
                    markdown_body=markdown_body,
                )
        logger.debug("Guide '%s' has no frontmatter, using defaults", name)
        return DocGuide(
            name=name,
            title=name.replace("-", " ").title(),
            description="",
            tags=[],
            full_content=raw,
            markdown_body=raw,
        )

    @property
    def guides(self) -> list[DocGuide]:
        return list(self._guides)

    def get(self, topic: str) -> DocGuide | None:
        """Get a guide by topic name (case-insensitive)."""
        topic_lower = topic.lower()
        for guide in self._guides:
            if guide.name.lower() == topic_lower:
                return guide
        return None

    def search(
        self,
        query: str,
        max_results: int = SEARCH_MAX_RESULTS,
        min_score: float = 0.0,
    ) -> list[DocEntry]:
        """Search guides by natural language query.

        Returns ranked list of DocEntry objects.

        Args:
            query: Natural language query.
            max_results: Maximum number of results to return.
            min_score: Minimum normalized BM25 score (0.0-1.0).  A result must
                score at least this fraction of the top result to be returned.
        """
        if not self._search_texts:
            return []
        if not query.strip():
            return [
                DocEntry(name=g.name, title=g.title, description=g.description, tags=g.tags)
                for g in self._guides[:max_results]
            ]
        indices = self._search_engine.search(
            self._search_texts, query, max_results, min_score=min_score
        )
        return [
            {
                "name": self._guides[i].name,
                "title": self._guides[i].title,
                "description": self._guides[i].description,
                "tags": self._guides[i].tags,
            }
            for i in indices
        ]

    def get_manifest_markdown(self) -> str:
        """Build a Markdown manifest of available guides."""
        if not self._guides:
            return ""
        lines = [
            "## Workflow Guides",
            "",
            "These guides explain Forgejo workflows and concepts beyond individual API tools:",
            "",
            "| Guide | Description |",
            "|-------|-------------|",
        ]
        for g in self._guides:
            desc = (
                (g.description[: _DESC_TRUNCATE - 3] + "...")
                if len(g.description) > _DESC_TRUNCATE
                else g.description
            )
            lines.append(f"| `{g.name}` | {desc} |")
        lines.extend(
            [
                "",
                "Use `search_docs(query)` to find guides by topic, or `read_doc(topic)` to read one.",
                "Guides are also available as resources at `gitea://docs/guide/{topic}`.",
                "",
            ]
        )
        return "\n".join(lines)


def register_doc_tools(
    mcp: FastMCP,
    doc_manager: DocManager,
) -> None:
    """Register doc tools and resources with the FastMCP server.

    Args:
        mcp: The FastMCP server instance
        doc_manager: Initialized DocManager with loaded guides
    """

    @mcp.tool(
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
        output_schema=_SEARCH_DOCS_OUTPUT_SCHEMA,
    )
    async def search_docs(
        query: str,
        format: str = "markdown",
        page: int = 1,
        limit: int = 10,
        min_score: float = SEARCH_MIN_SCORE,
    ) -> ToolResult:
        """Search workflow guides by natural language query.

        Finds guides explaining Forgejo workflows, concepts, and settings.
        Matches against guide name, title, description, and tags using BM25 ranking.

        Use this when you need to understand how Gitea/Forgejo features work
        beyond individual API calls -- e.g., permission models, token scopes,
        branch protection rules, label system, pull request workflows.

        ## Parameters

        - ``query``: Natural language query (e.g., "how do tokens work", "protect branches", "label scopes")
        - ``format``: Output format -- ``markdown`` (default, human-readable table), ``json`` (structured data), or ``raw``.
        - ``min_score``: Minimum relevance score (0.0-1.0). 0.0 returns everything,
          0.1 requires at least 10% as relevant as the top result, 1.0 requires perfect match.

        ## Return Value

        Ranked list of matching guides, each with:
        - ``name``: Topic name (use with ``read_doc`` or resource URI)
        - ``title``: Human-readable title
        - ``description``: Brief description
        - ``tags``: Topic categorisation tags

        To read a full guide, use ``read_doc(topic)`` or read the resource
        ``gitea://docs/guide/{topic}``.

        Args:
            query: Natural language query to search for guides
            format: Output format: markdown (default), json, or raw
            page: Page number (1-based, default 1)
            limit: Maximum results per page (1-100, default 10)
            min_score: Minimum relevance score (0.0-1.0)

        Returns:
            Ranked list of matching guide metadata
        """
        all_results = doc_manager.search(
            query,
            max_results=len(doc_manager.guides) if doc_manager.guides else 0,
            min_score=min_score,
        )
        total_count = len(all_results)

        # Slice
        start = (page - 1) * limit
        end = start + limit
        page_items = all_results[start:end]

        if total_count == 0:
            content = (
                f"No workflow guides found for '{query}'.\n\n"
                "**Cross-linking hints:**\n"
                "- For API tools: `search_tools(query)`\n"
                "- For data resources: `search_resources(query)`"
            )
            return ToolResult(
                content=[TextContent(type="text", text=content)],
                structured_content={"result": [], "_hint": content},
            )

        if not page_items:
            content = f"Page {page} is out of range (total results: {total_count})."
            return ToolResult(
                content=[TextContent(type="text", text=content)],
                structured_content={"result": [], "_hint": content},
            )

        extras: list[str] = []
        if format == "markdown":
            extras.append(
                "**Cross-linking hints:**\n"
                "- Guides are also available as resources at `gitea://docs/guide/{topic}`\n"
                "- For API tools: `search_tools(query)`\n"
                "- For data resources: `search_resources(query)`"
            )
            pagination_table = _format_as_markdown(
                {k: v for k, v in add_pagination_metadata(
                    {"result": page_items}, page, limit, total_count
                ).items() if k in PAGINATION_KEYS},
                None,
            )
            extras.append(pagination_table)

        return apply_pagination(
            apply_format(page_items, format, markdown_extras=extras or None),
            page, limit, total_count,
        )

    @mcp.tool(
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The full guide content in Markdown",
                },
            },
        },
    )
    async def read_doc(
        topic: str,
        format: str = "markdown",
    ) -> ToolResult:
        """Read a workflow guide by topic name.

        Returns the full guide content explaining a Forgejo workflow or concept.
        Topic names are case-insensitive and correspond to the ``name`` field
        from ``search_docs`` results.

        Use this after ``search_docs`` to read the full guide on a specific topic.

        ## Parameters

        - ``topic``: Topic name (e.g., "token-scopes", "branch-protection", "labels").
          Case-insensitive. Find available topics with ``search_docs``.
        - ``format``: Output format -- ``markdown`` (default, full content with
          YAML frontmatter), ``raw`` (same as markdown - full content included).

        ## Return Value

        The full guide content in Markdown format.

        ## Error Handling

        Raises ``ValueError`` if the topic is not found, listing available guides.

        Args:
            topic: The guide topic name (case-insensitive)
            format: Output format: markdown (default) or raw

        Returns:
            The full guide content

        Raises:
            ValueError: If the topic is not found
        """
        guide = doc_manager.get(topic)
        if guide is None:
            available = ", ".join(g.name for g in doc_manager.guides)
            msg = (
                f"Guide '{topic}' not found. "
                f"Available guides: {available}. "
                "Use search_docs() to find guides by topic, or read_doc(topic) to read one."
            )
            raise ValueError(msg)

        return apply_format(
            guide.full_content,
            format,
            markdown_formatter=lambda d: d,
        )

    # Compute dynamic tags and description from all loaded guides
    # so resource discovery aligns with guide frontmatter content
    all_tags: set[str] = {"docs", "guide", "workflow"}
    topic_list: list[str] = []
    for g in doc_manager.guides:
        all_tags.update(g.tags)
        all_tags.add(g.name)
        topic_list.append(g.name)
    topic_str = ", ".join(sorted(topic_list))
    description = (
        "Read a workflow guide by topic name. "
        f"Topics: {topic_str}. "
        "Use search_docs() to find guides by topic, or read_doc(topic) to read one."
    )

    # Register the resource template for all guides
    @mcp.resource(
        uri="gitea://docs/guide/{topic}",
        name="Workflow Guide",
        description=description,
        mime_type="text/markdown",
        tags=all_tags,
    )
    async def doc_resource(topic: str) -> str:
        """Get a workflow guide by topic name."""
        guide = doc_manager.get(topic)
        if guide is None:
            raise ResourceError(
                {
                    "code": "GUIDE_NOT_FOUND",
                    "message": f"Guide '{topic}' not found",
                    "detail": f"Available guides: {', '.join(g.name for g in doc_manager.guides)}",
                    "resource_type": "guide",
                    "resource_id": topic,
                }
            )
        return guide.full_content

    logger.info(
        "Registered doc tools (search_docs, read_doc) and resource template for %d guides",
        len(doc_manager.guides),
    )


__all__ = [
    "DocGuide",
    "DocManager",
    "register_doc_tools",
]
