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

    def test_min_score_zero_returns_all_matches(self):
        """min_score=0.0 returns every document with any overlap."""
        index = _BM25Index()
        index.build(["apple banana", "apple cherry", "orange grape"])
        results = index.query("apple", 10, min_score=0.0)
        # docs 0 and 1 both contain "apple"
        assert 0 in results
        assert 1 in results
        # doc 2 has no match for "apple"
        assert 2 not in results

    def test_min_score_filters_weak_matches(self):
        """High min_score filters out lower-ranked documents."""
        index = _BM25Index()
        # doc 1 has "apple" twice — stronger match
        index.build(["apple banana", "apple apple cherry"])
        results = index.query("apple", 10, min_score=0.0)
        assert len(results) == 2  # both docs match at min_score=0

        # doc 0 has fewer occurrences: its normalized score will be < 1.0
        # At min_score=1.0 only the top doc passes
        top_only = index.query("apple", 10, min_score=1.0)
        assert top_only == [1]  # only the strongest match

    def test_min_score_one_returns_only_top(self):
        """min_score=1.0 returns only the top-ranked document."""
        index = _BM25Index()
        index.build(["apple banana", "apple apple", "orange grape"])
        results = index.query("apple", 10, min_score=1.0)
        assert len(results) == 1
        assert results[0] == 1  # doc 1 has highest TF for "apple"

    def test_min_score_with_top_k(self):
        """min_score and top_k interact correctly."""
        index = _BM25Index()
        index.build(["apple", "apple apple", "apple apple apple", "orange"])
        # top_k=2 should return at most 2, but min_score=1.0 only keeps top 1
        results = index.query("apple", top_k=2, min_score=1.0)
        assert len(results) == 1

    def test_query_with_scores_returns_normalized_scores(self):
        """query_with_scores returns (index, score) with top score == 1.0."""
        index = _BM25Index()
        index.build(["apple banana", "apple apple cherry"])
        ranked = index.query_with_scores("apple", 10, min_score=0.0)
        assert [i for i, _ in ranked] == [1, 0]
        scores = [s for _, s in ranked]
        assert scores[0] == 1.0  # top match normalized to 1.0
        assert 0.0 < scores[1] < 1.0  # weaker match below 1.0

    def test_query_with_scores_out_of_range_raises(self):
        """min_score outside [0.0, 1.0] raises ValueError."""
        index = _BM25Index()
        index.build(["apple banana"])
        import pytest

        with pytest.raises(ValueError, match="min_score must be in"):
            index.query_with_scores("apple", 10, min_score=1.5)
        with pytest.raises(ValueError, match="min_score must be in"):
            index.query_with_scores("apple", 10, min_score=-0.1)

    def test_query_bounds_check_raises(self):
        """The list[int] query() also enforces the min_score bounds."""
        import pytest

        index = _BM25Index()
        index.build(["apple banana"])
        with pytest.raises(ValueError, match="min_score must be in"):
            index.query("apple", 10, min_score=2.0)


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

    def test_search_with_min_score_zero(self):
        """min_score=0.0 returns all matches."""
        engine = BM25SearchEngine()
        texts = ["apple banana", "apple cherry", "orange grape"]
        results = engine.search(texts, "apple", 10, min_score=0.0)
        assert 0 in results
        assert 1 in results
        assert 2 not in results

    def test_search_with_min_score_one(self):
        """min_score=1.0 returns only top match."""
        engine = BM25SearchEngine()
        texts = ["apple banana", "apple apple", "orange"]
        results = engine.search(texts, "apple", 10, min_score=1.0)
        assert results == [1]

    def test_search_with_high_min_score_filters(self):
        """High min_score filters weak matches."""
        engine = BM25SearchEngine()
        texts = ["apple banana", "apple apple apple", "apple cherry"]
        results_default = engine.search(texts, "apple", 10)  # uses default min_score=0.0
        assert len(results_default) >= 2

        results_high = engine.search(texts, "apple", 10, min_score=1.0)
        assert len(results_high) < len(results_default)

    def test_search_with_scores_returns_scores(self):
        """search_with_scores returns (index, score) pairs."""
        engine = BM25SearchEngine()
        texts = ["apple banana", "apple apple cherry"]
        ranked = engine.search_with_scores(texts, "apple", 10, min_score=0.0)
        assert [i for i, _ in ranked] == [1, 0]
        assert [s for _, s in ranked][0] == 1.0

    def test_search_bounds_check_raises(self):
        """min_score outside [0.0, 1.0] raises ValueError."""
        import pytest

        engine = BM25SearchEngine()
        with pytest.raises(ValueError, match="min_score must be in"):
            engine.search(["apple banana"], "apple", 10, min_score=1.2)
        with pytest.raises(ValueError, match="min_score must be in"):
            engine.search(["apple banana"], "apple", 10, min_score=-1.0)
