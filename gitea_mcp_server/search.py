"""Generic BM25 search engine (infra layer).

Provides tokenization, alias expansion, and a reusable BM25 search engine.
Used by both tool search (tools/search.py) and resource search (mcp_tools.py).
"""

import hashlib
import re

from fastmcp.server.transforms.search.bm25 import _BM25Index as _BaseBM25Index

from gitea_mcp_server.constants import SEARCH_MIN_TOKEN_LENGTH


def _tokenize_len2(text: str) -> list[str]:
    """Tokenize with support for 2-character tokens like 'pr'."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= SEARCH_MIN_TOKEN_LENGTH]


def _expand_word_aliases(text: str) -> str:
    """Expand common abbreviations and fragments for better search matching."""
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


def _texts_hash(texts: list[str]) -> str:
    """SHA256 hash of sorted texts for staleness detection."""
    key = "|".join(sorted(texts))
    return hashlib.sha256(key.encode()).hexdigest()


class BM25SearchEngine:
    """Generic BM25 search engine for document-based search.

    Builds and queries a BM25 index from a list of searchable text strings.
    Returns ranked indices for a query. Caches the index until texts change.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._index: _BM25IndexLen2 = _BM25IndexLen2(k1, b)
        self._last_texts_hash: str = ""

    def search(self, texts: list[str], query: str, max_results: int = 10) -> list[int]:
        """Search texts by BM25 relevance ranking.

        Args:
            texts: Searchable text strings for each document.
            query: Natural language query.
            max_results: Maximum number of results.

        Returns:
            Ranked list of indices into the original texts list.
        """
        current_hash = _texts_hash(texts)
        if current_hash != self._last_texts_hash:
            new_index = _BM25IndexLen2(self._k1, self._b)
            new_index.build(texts)
            self._index, self._last_texts_hash = new_index, current_hash

        expanded_query = _expand_word_aliases(query)
        return list(self._index.query(expanded_query, max_results))


__all__ = [
    "BM25SearchEngine",
    "_BM25IndexLen2",
    "_expand_word_aliases",
    "_texts_hash",
    "_tokenize_len2",
]
