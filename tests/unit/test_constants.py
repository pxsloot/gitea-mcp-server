"""Tests for constants, configuration values, and mappings."""

from gitea_mcp_server.constants import (
    AUTO_GENERATED_RESOURCE_SKIP_URIS,
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
    SEARCH_ALWAYS_VISIBLE_TOOLS,
    SEARCH_CATEGORY_ALIASES,
    SEARCH_MAX_RESULTS,
    SEARCH_MIN_TOKEN_LENGTH,
    SEARCH_NAME_BOOST,
    TAG_TO_SCOPE,
    TITLE_TRUNCATE_LIMIT,
    TOOL_INVALIDATION_PATTERNS,
)


class TestTitleAndFormatting:
    """Tests for title and response formatting constants."""

    def test_title_truncate_limit_is_positive(self):
        assert TITLE_TRUNCATE_LIMIT > 0

    def test_response_preview_limit_is_positive(self):
        assert RESPONSE_PREVIEW_LIMIT > 0


class TestHTTPClientConfig:
    """Tests for HTTP client timeout and connection constants."""

    def test_timeouts_are_positive(self):
        assert HTTP_TIMEOUT_CONNECT > 0
        assert HTTP_TIMEOUT_READ > 0
        assert HTTP_TIMEOUT_WRITE > 0
        assert HTTP_TIMEOUT_POOL > 0

    def test_connection_limits_are_positive(self):
        assert HTTP_MAX_KEEPALIVE_CONNECTIONS > 0
        assert HTTP_MAX_CONNECTIONS > 0


class TestRetryConfig:
    """Tests for retry configuration constants."""

    def test_retry_attempts_are_positive(self):
        assert RETRY_MAX_ATTEMPTS > 0

    def test_wait_times_are_positive(self):
        assert RETRY_WAIT_MULTIPLIER > 0
        assert RETRY_WAIT_MIN > 0
        assert RETRY_WAIT_MAX > 0

    def test_wait_min_less_than_max(self):
        assert RETRY_WAIT_MIN < RETRY_WAIT_MAX


class TestCacheConfig:
    """Tests for cache TTL and size constants."""

    def test_cache_ttls_are_non_negative(self):
        assert CACHE_TTL_DEFAULT >= 0
        assert CACHE_TTL_RESOURCE_LIST >= 0
        assert CACHE_TTL_REPOSITORY >= 0
        assert CACHE_TTL_README >= 0
        assert CACHE_TTL_RELEASES >= 0
        assert CACHE_TTL_USERS >= 0

    def test_resource_list_ttl_higher_than_default(self):
        assert CACHE_TTL_RESOURCE_LIST >= CACHE_TTL_DEFAULT

    def test_max_item_size_is_positive(self):
        assert CACHE_MAX_ITEM_SIZE > 0

    def test_label_cache_ttl_is_positive(self):
        assert LABEL_CACHE_TTL > 0


class TestSearchConfig:
    """Tests for BM25 search configuration constants."""

    def test_search_max_results_is_positive(self):
        assert SEARCH_MAX_RESULTS > 0

    def test_always_visible_tools_contains_core_tools(self):
        assert "search" in SEARCH_ALWAYS_VISIBLE_TOOLS
        assert "read_resource" in SEARCH_ALWAYS_VISIBLE_TOOLS
        assert "list_resources" in SEARCH_ALWAYS_VISIBLE_TOOLS

    def test_min_token_length_is_at_least_one(self):
        assert SEARCH_MIN_TOKEN_LENGTH >= 1

    def test_name_boost_is_positive(self):
        assert SEARCH_NAME_BOOST > 0

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


class TestAutoGeneratedSkipURIs:
    """Tests for resource URIs that skip auto-generation."""

    def test_skip_uris_are_templates(self):
        for uri in AUTO_GENERATED_RESOURCE_SKIP_URIS:
            assert "{" in uri, f"URI should be a template: {uri}"

    def test_repo_in_skip_list(self):
        assert "gitea://repos/{owner}/{repo}" in AUTO_GENERATED_RESOURCE_SKIP_URIS

    def test_issues_in_skip_list(self):
        assert "gitea://repos/{owner}/{repo}/issues" in AUTO_GENERATED_RESOURCE_SKIP_URIS


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
