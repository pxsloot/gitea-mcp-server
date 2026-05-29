"""Tolerant BM25 search for tool discovery.

Extracts BM25 indexing and search logic from tool_annotator.py into a
focused utility class with no transform concerns.
"""

import re
from collections.abc import Sequence

from fastmcp.server.transforms.search.bm25 import _BM25Index as _BaseBM25Index
from fastmcp.server.transforms.search.bm25 import _catalog_hash
from fastmcp.tools.base import Tool

from gitea_mcp_server.constants import (
    SEARCH_CATEGORY_ALIASES,
    SEARCH_MIN_TOKEN_LENGTH,
    SEARCH_NAME_BOOST,
)


def _tokenize_len2(text: str) -> list[str]:
    """Tokenize with support for 2-character tokens like 'pr'."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= SEARCH_MIN_TOKEN_LENGTH]


def _expand_word_aliases(text: str) -> str:
    """Expand common abbreviations and fragments for better search matching.

    BM25 uses whitespace tokenization, so singular/plural variations like
    "repo"/"repos" don't match unless both forms are present.
    """
    alias_expansions = [
        ("repo", "repo repository repos"),
        ("pr", "pr pull request"),
        ("current", "current authenticated"),
        ("user", "user users account"),
    ]
    text_lower = text.lower()
    parts = [text]
    for word, expansion in alias_expansions:
        if word in text_lower:
            parts.append(expansion)
    return " ".join(parts)


def _extract_searchable_text_enhanced(tool: Tool) -> str:
    """Enhanced searchable text extraction for better tool discoverability.

    Includes:
    - Tool name (boosted)
    - Description
    - Parameter names and descriptions
    - Tags with expanded aliases
    - Title
    """
    parts = [tool.name] * SEARCH_NAME_BOOST

    if tool.annotations and tool.annotations.title:
        parts.append(tool.annotations.title)

    if tool.description:
        parts.append(tool.description)

    schema = tool.parameters
    if schema:
        properties = schema.get("properties", {})
        for param_name, param_info in properties.items():
            parts.append(param_name)
            if isinstance(param_info, dict):
                desc = param_info.get("description", "")
                if desc:
                    parts.append(desc)

    if tool.tags:
        for tag in tool.tags:
            parts.append(tag)
            if tag in SEARCH_CATEGORY_ALIASES:
                parts.append(SEARCH_CATEGORY_ALIASES[tag])

    return " ".join(parts)


class _BM25IndexLen2(_BaseBM25Index):
    """BM25 index that supports 2-character tokens."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        super().__init__(k1, b)

    def build(self, documents: list[str]) -> None:
        self._doc_tokens = [_tokenize_len2(doc) for doc in documents]
        self._doc_lengths = [len(tokens) for tokens in self._doc_tokens]
        self._n = len(documents)
        self._avg_dl = sum(self._doc_lengths) / self._n if self._n else 0.0

        self._df: dict[str, int] = {}
        self._tf = []
        for tokens in self._doc_tokens:
            tf: dict[str, int] = {}
            seen: set[str] = set()
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
                if token not in seen:
                    self._df[token] = self._df.get(token, 0) + 1
                    seen.add(token)
            self._tf.append(tf)


class TolerantBM25Search:
    """BM25 search for tools with tolerant tokenization and alias expansion.

    Pure search logic with no transform concerns. Builds and queries
    a BM25 index from tool metadata.
    """

    def __init__(self) -> None:
        self._last_hash: str = ""
        self._index: _BM25IndexLen2 = _BM25IndexLen2()
        self._indexed_tools: Sequence[Tool] = ()

    def search(self, tools: Sequence[Tool], query: str, max_results: int = 10) -> Sequence[Tool]:
        """Search tools by BM25 relevance ranking.

        Args:
            tools: The tool catalog to search.
            query: Natural language search query.
            max_results: Maximum number of results to return.

        Returns:
            Ranked sequence of matching tools.
        """
        current_hash = _catalog_hash(tools)
        if current_hash != self._last_hash:
            documents = [_extract_searchable_text_enhanced(t) for t in tools]
            new_index = _BM25IndexLen2(self._index.k1, self._index.b)
            new_index.build(documents)
            self._index, self._indexed_tools, self._last_hash = (
                new_index,
                tools,
                current_hash,
            )

        expanded_query = _expand_word_aliases(query)
        indices = self._index.query(expanded_query, max_results)
        return [self._indexed_tools[i] for i in indices]


__all__ = [
    "TolerantBM25Search",
    "_BM25IndexLen2",
    "_expand_word_aliases",
    "_extract_searchable_text_enhanced",
    "_tokenize_len2",
]
