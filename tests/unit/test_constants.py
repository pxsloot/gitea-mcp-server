"""Tests for constants, configuration values, and mappings."""

from gitea_mcp_server.constants import (
    CACHE_MAX_ITEM_SIZE,
    CACHE_TTL_DEFAULT,
    CACHE_TTL_README,
    CACHE_TTL_RELEASES,
    CACHE_TTL_REPOSITORY,
    CACHE_TTL_RESOURCE_LIST,
    CACHE_TTL_USERS,
    HTTP_MAX_CONNECTIONS,
    HTTP_MAX_KEEPALIVE_CONNECTIONS,
    HTTP_METHODS_DESTRUCTIVE,
    HTTP_METHODS_IDEMPOTENT,
    HTTP_METHODS_SAFE,
    HTTP_STATUS_NOT_FOUND,
    HTTP_STATUS_RATE_LIMIT,
    HTTP_STATUS_RETRYABLE,
    HTTP_TIMEOUT_CONNECT,
    HTTP_TIMEOUT_POOL,
    HTTP_TIMEOUT_READ,
    HTTP_TIMEOUT_WRITE,
    LABEL_CACHE_TTL,
    LABEL_GUIDANCE,
    PATTERN_FILES,
    PATTERN_ISSUES_LIST,
    PATTERN_PULLS_LIST,
    PATTERN_REPO,
    RESOURCE_PATTERN_FILES,
    RESOURCE_PATTERN_ISSUES_LIST,
    RESOURCE_PATTERN_PULLS_LIST,
    RESOURCE_PATTERN_REPO,
    RESPONSE_PREVIEW_LIMIT,
    RETRY_MAX_ATTEMPTS,
    RETRY_WAIT_MAX,
    RETRY_WAIT_MIN,
    RETRY_WAIT_MULTIPLIER,
    SEARCH_CATEGORY_ALIASES,
    SEARCH_MAX_RESULTS,
    SEARCH_MIN_TOKEN_LENGTH,
    SEARCH_NAME_BOOST,
    TAG_TO_SCOPE,
    TOOL_INVALIDATION_PATTERNS,
)


class TestFormatting:
    """Tests for response formatting constants."""

    def test_preview_limit_is_reasonable(self):
        assert 1 <= RESPONSE_PREVIEW_LIMIT <= 10000


class TestHTTPClientConfig:
    """Tests for HTTP client timeout and connection constants."""

    def test_timeouts_are_reasonable(self):
        for timeout in (HTTP_TIMEOUT_CONNECT, HTTP_TIMEOUT_READ, HTTP_TIMEOUT_WRITE, HTTP_TIMEOUT_POOL):
            assert 1 <= timeout <= 300

    def test_connection_limits_are_reasonable(self):
        assert 1 <= HTTP_MAX_KEEPALIVE_CONNECTIONS <= 1000
        assert 1 <= HTTP_MAX_CONNECTIONS <= 10000


class TestRetryConfig:
    """Tests for retry configuration constants."""

    def test_retry_attempts_reasonable(self):
        assert 1 <= RETRY_MAX_ATTEMPTS <= 20

    def test_wait_times_reasonable(self):
        assert 0 < RETRY_WAIT_MULTIPLIER <= 10
        assert 0 < RETRY_WAIT_MIN < RETRY_WAIT_MAX


class TestCacheConfig:
    """Tests for cache TTL and size constants."""

    def test_cache_ttls_are_non_negative(self):
        for ttl in (CACHE_TTL_DEFAULT, CACHE_TTL_RESOURCE_LIST, CACHE_TTL_REPOSITORY, CACHE_TTL_README, CACHE_TTL_RELEASES, CACHE_TTL_USERS):
            assert ttl >= 0

    def test_resource_list_ttl_higher_than_default(self):
        assert CACHE_TTL_RESOURCE_LIST >= CACHE_TTL_DEFAULT

    def test_max_item_size_and_label_ttl_are_positive(self):
        assert CACHE_MAX_ITEM_SIZE > 0
        assert LABEL_CACHE_TTL > 0


class TestSearchConfig:
    """Tests for BM25 search configuration constants."""

    def test_search_config_is_sensible(self):
        assert 1 <= SEARCH_MAX_RESULTS <= 1000
        assert 1 <= SEARCH_MIN_TOKEN_LENGTH <= 10
        assert 0 < SEARCH_NAME_BOOST <= 100

    def test_category_aliases_contains_expected_keys(self):
        assert "pull_request" in SEARCH_CATEGORY_ALIASES
        assert "issue" in SEARCH_CATEGORY_ALIASES
        assert "repository" in SEARCH_CATEGORY_ALIASES

    def test_label_guidance_is_non_empty(self):
        assert len(LABEL_GUIDANCE) > 0
        assert "Labels" in LABEL_GUIDANCE


class TestHTTPStatusCodes:
    """Tests for HTTP status code constants."""

    def test_not_found_is_404(self):
        assert HTTP_STATUS_NOT_FOUND == 404

    def test_rate_limit_is_429(self):
        assert HTTP_STATUS_RATE_LIMIT == 429

    def test_retryable_set_contains_rate_limit(self):
        assert HTTP_STATUS_RATE_LIMIT in HTTP_STATUS_RETRYABLE

    def test_retryable_set_contains_server_errors(self):
        for code in (500, 502, 503, 504):
            assert code in HTTP_STATUS_RETRYABLE


class TestHTTPMethodGroups:
    """Tests for HTTP method semantic grouping constants."""

    def test_safe_methods(self):
        assert "GET" in HTTP_METHODS_SAFE
        assert "HEAD" in HTTP_METHODS_SAFE
        assert "OPTIONS" in HTTP_METHODS_SAFE
        assert "POST" not in HTTP_METHODS_SAFE

    def test_destructive_methods(self):
        assert "DELETE" in HTTP_METHODS_DESTRUCTIVE
        assert "GET" not in HTTP_METHODS_DESTRUCTIVE

    def test_idempotent_methods(self):
        for method in ("GET", "PUT", "DELETE", "HEAD", "OPTIONS"):
            assert method in HTTP_METHODS_IDEMPOTENT
        assert "POST" not in HTTP_METHODS_IDEMPOTENT
        assert "PATCH" not in HTTP_METHODS_IDEMPOTENT


class TestPatternConstants:
    """Tests for invalidation pattern name constants."""

    def test_pattern_names_defined(self):
        assert PATTERN_ISSUES_LIST == "issues_list"
        assert PATTERN_PULLS_LIST == "pulls_list"
        assert PATTERN_REPO == "repo"
        assert PATTERN_FILES == "files"


class TestResourcePatterns:
    """Tests for resource URI pattern templates."""

    def test_patterns_have_placeholders(self):
        assert "{owner}" in RESOURCE_PATTERN_ISSUES_LIST
        assert "{owner}" in RESOURCE_PATTERN_PULLS_LIST
        assert "{owner}" in RESOURCE_PATTERN_REPO
        assert "{owner}" in RESOURCE_PATTERN_FILES
        assert "{filepath}" in RESOURCE_PATTERN_FILES


class TestTAGToScope:
    """Tests for Swagger tag to Gitea token scope mapping."""

    def test_known_tags_have_scopes(self):
        for tag in ("admin", "repository", "issue", "organization", "user"):
            assert tag in TAG_TO_SCOPE

    def test_admin_maps_to_sudo(self):
        assert TAG_TO_SCOPE["admin"] == "sudo"

    def test_misc_maps_to_misc(self):
        assert TAG_TO_SCOPE["miscellaneous"] == "misc"


class TestToolInvalidationPatterns:
    """Tests for cache invalidation pattern definitions."""

    def test_patterns_are_tuples(self):
        for pattern in TOOL_INVALIDATION_PATTERNS:
            assert len(pattern) == 3
            path_prefix, match_type, pattern_names = pattern
            assert isinstance(path_prefix, str)
            assert match_type is None or match_type == "exact"
            assert isinstance(pattern_names, list)

    def test_issues_pattern_present(self):
        prefixes = [p[0] for p in TOOL_INVALIDATION_PATTERNS]
        assert "/repos/{owner}/{repo}/issues" in prefixes

    def test_repo_exact_pattern_present(self):
        matches = [(p[0], p[1]) for p in TOOL_INVALIDATION_PATTERNS]
        assert ("/repos/{owner}/{repo}", "exact") in matches
