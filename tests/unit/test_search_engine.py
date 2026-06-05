"""Unit tests for the self-contained BM25 search engine (search.py)."""

from gitea_mcp_server.search import (
    _BM25Index,
    _BM25IndexLen2,
    _expand_word_aliases,
    _texts_hash,
    _tokenize_len2,
    BM25SearchEngine,
)


class TestTokenize:
    """Tests for _tokenize_len2."""

    def test_basic_tokenization(self):
        """Basic lowercase splitting."""
        tokens = _tokenize_len2("Hello World Test")
        assert tokens == ["hello", "world", "test"]

    def test_two_character_tokens_included(self):
        """2-char tokens like 'pr' are included."""
        tokens = _tokenize_len2("pr repo issue")
        assert "pr" in tokens
        assert "repo" in tokens
        assert "issue" in tokens

    def test_single_character_excluded(self):
        """Single-character tokens are excluded."""
        tokens = _tokenize_len2("a b c hello")
        assert tokens == ["hello"]

    def test_non_alphanumeric_split(self):
        """Non-alphanumeric chars split tokens."""
        tokens = _tokenize_len2("gitea_user_get_current foo-bar")
        assert "gitea_user_get_current" not in tokens
        assert "gitea" in tokens
        assert "user" in tokens
        assert "foo" in tokens
        assert "bar" in tokens

    def test_empty_string(self):
        """Empty string returns empty list."""
        assert _tokenize_len2("") == []

    def test_numbers_only(self):
        """Numbers are kept."""
        tokens = _tokenize_len2("issue 42")
        assert "42" in tokens
        assert "issue" in tokens


class TestExpandWordAliases:
    """Tests for _expand_word_aliases."""

    def test_repo_expansion(self):
        """'repo' expands to include 'repository' and 'repos'."""
        result = _expand_word_aliases("find repo")
        assert "repository" in result
        assert "repos" in result

    def test_pr_expansion(self):
        """'pr' expands to include 'pull request'."""
        result = _expand_word_aliases("create pr")
        assert "pull" in result
        assert "request" in result

    def test_no_expansion_needed(self):
        """Strings without aliases are unchanged."""
        result = _expand_word_aliases("hello world")
        assert result == "hello world"

    def test_multiple_aliases(self):
        """Multiple aliases in same string all expand."""
        result = _expand_word_aliases("current user repo pr")
        assert "authenticated" in result
        assert "account" in result
        assert "repository" in result
        assert "pull" in result

    def test_case_insensitive(self):
        """Alias expansion is case-insensitive."""
        result = _expand_word_aliases("REPO")
        assert "repository" in result


class TestTextsHash:
    """Tests for _texts_hash."""

    def test_consistent_hash(self):
        """Same texts produce same hash."""
        texts = ["hello world", "foo bar"]
        h1 = _texts_hash(texts)
        h2 = _texts_hash(texts)
        assert h1 == h2

    def test_different_texts_different_hash(self):
        """Different texts produce different hashes."""
        h1 = _texts_hash(["hello"])
        h2 = _texts_hash(["world"])
        assert h1 != h2

    def test_order_independent(self):
        """Hash is independent of input order."""
        h1 = _texts_hash(["a", "b"])
        h2 = _texts_hash(["b", "a"])
        assert h1 == h2


class TestBM25Index:
    """Direct unit tests for the self-contained _BM25Index."""

    def test_empty_index_returns_empty(self):
        """Query on empty/unbuilt index returns empty list."""
        index = _BM25Index()
        assert index.query("test", 10) == []

    def test_empty_query_returns_empty(self):
        """Empty query string returns empty list."""
        index = _BM25Index()
        index.build(["hello world", "foo bar"])
        assert index.query("", 10) == []

    def test_single_document_found(self):
        """Single document matching query is returned."""
        index = _BM25Index()
        index.build(["hello world"])
        results = index.query("hello", 10)
        assert results == [0]

    def test_relevance_ranking(self):
        """Documents with more matches rank higher."""
        index = _BM25Index()
        index.build([
            "apple banana cherry",
            "apple apple banana",
            "orange grape",
        ])
        results = index.query("apple banana", 10)
        # doc 1 (apple x2 + banana) should rank higher than doc 0 (apple x1 + banana)
        # doc 2 has neither, should be last or excluded
        assert results[0] == 1
        assert 2 not in results

    def test_top_k_limits_results(self):
        """top_k parameter limits number of results."""
        index = _BM25Index()
        index.build(["apple one", "apple two", "apple three", "orange"])
        results = index.query("apple", 2)
        assert len(results) <= 2

    def test_non_matching_query_returns_empty(self):
        """Query with no matches returns empty list."""
        index = _BM25Index()
        index.build(["hello world", "foo bar"])
        results = index.query("zzzzzzz", 10)
        assert results == []


class TestBM25IndexLen2:
    """Tests for _BM25IndexLen2."""

    def test_inherits_query_from_base(self):
        """_BM25IndexLen2 inherits query() from _BM25Index."""
        index = _BM25IndexLen2()
        index.build(["hello world"])
        assert index.query("hello", 10) == [0]

    def test_two_char_tokens_supported(self):
        """2-char tokens like 'pr' are indexed and searchable."""
        index = _BM25IndexLen2()
        index.build(["create pr", "create pull request"])
        results = index.query("pr", 10)
        assert 0 in results


class TestBM25SearchEngine:
    """Tests for BM25SearchEngine (higher-level wrapper)."""

    def test_search_returns_indices(self):
        """search() returns ranked indices."""
        engine = BM25SearchEngine()
        texts = ["apple banana", "banana cherry", "cherry date"]
        results = engine.search(texts, "banana", 10)
        assert 0 in results
        assert 1 in results

    def test_search_with_empty_texts(self):
        """Empty texts list returns empty results."""
        engine = BM25SearchEngine()
        assert engine.search([], "test", 10) == []

    def test_search_caches_and_rebuilds(self):
        """Search rebuilds index when texts change."""
        engine = BM25SearchEngine()
        r1 = engine.search(["hello world"], "hello", 10)
        assert r1 == [0]

        r2 = engine.search(["foo bar"], "hello", 10)
        assert r2 == []
