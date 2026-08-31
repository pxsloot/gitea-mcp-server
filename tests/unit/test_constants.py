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
)


class TestFormatting:
    """Tests for response formatting constants."""

    def test_preview_limit_is_reasonable(self) -> None:
        assert 1 <= RESPONSE_PREVIEW_LIMIT <= 10000


class TestHTTPClientConfig:
    """Tests for HTTP client timeout and connection constants."""

    def test_timeouts_are_reasonable(self) -> None:
        for timeout in (
            HTTP_TIMEOUT_CONNECT,
            HTTP_TIMEOUT_READ,
            HTTP_TIMEOUT_WRITE,
            HTTP_TIMEOUT_POOL,
        ):
            assert 1 <= timeout <= 300

    def test_connection_limits_are_reasonable(self) -> None:
        assert 1 <= HTTP_MAX_KEEPALIVE_CONNECTIONS <= 1000
        assert 1 <= HTTP_MAX_CONNECTIONS <= 10000


class TestRetryConfig:
    """Tests for retry configuration constants."""

    def test_retry_attempts_reasonable(self) -> None:
        assert 1 <= RETRY_MAX_ATTEMPTS <= 20

    def test_wait_times_reasonable(self) -> None:
        assert 0 < RETRY_WAIT_MULTIPLIER <= 10
        assert 0 < RETRY_WAIT_MIN < RETRY_WAIT_MAX


class TestCacheConfig:
    """Tests for cache TTL and size constants."""

    def test_cache_ttls_are_non_negative(self) -> None:
        for ttl in (
            CACHE_TTL_DEFAULT,
            CACHE_TTL_RESOURCE_LIST,
            CACHE_TTL_REPOSITORY,
            CACHE_TTL_README,
            CACHE_TTL_RELEASES,
            CACHE_TTL_USERS,
        ):
            assert ttl >= 0

    def test_resource_list_ttl_higher_than_default(self) -> None:
        assert CACHE_TTL_RESOURCE_LIST >= CACHE_TTL_DEFAULT

    def test_max_item_size_and_label_ttl_are_positive(self) -> None:
        assert CACHE_MAX_ITEM_SIZE > 0
        assert LABEL_CACHE_TTL > 0


class TestSearchConfig:
    """Tests for BM25 search configuration constants."""

    def test_search_config_is_sensible(self) -> None:
        assert 1 <= SEARCH_MAX_RESULTS <= 1000
        assert 1 <= SEARCH_MIN_TOKEN_LENGTH <= 10
        assert 0 < SEARCH_NAME_BOOST <= 100

    def test_category_aliases_contains_expected_keys(self) -> None:
        assert "pull_request" in SEARCH_CATEGORY_ALIASES
        assert "issue" in SEARCH_CATEGORY_ALIASES
        assert "repository" in SEARCH_CATEGORY_ALIASES


class TestHTTPStatusCodes:
    """Tests for HTTP status code constants."""

    def test_not_found_is_404(self) -> None:
        assert HTTP_STATUS_NOT_FOUND == 404

    def test_rate_limit_is_429(self) -> None:
        assert HTTP_STATUS_RATE_LIMIT == 429

    def test_retryable_set_contains_rate_limit(self) -> None:
        assert HTTP_STATUS_RATE_LIMIT in HTTP_STATUS_RETRYABLE

    def test_retryable_set_contains_server_errors(self) -> None:
        for code in (500, 502, 503, 504):
            assert code in HTTP_STATUS_RETRYABLE


class TestHTTPMethodGroups:
    """Tests for HTTP method semantic grouping constants."""

    def test_safe_methods(self) -> None:
        assert "GET" in HTTP_METHODS_SAFE
        assert "HEAD" in HTTP_METHODS_SAFE
        assert "OPTIONS" in HTTP_METHODS_SAFE
        assert "POST" not in HTTP_METHODS_SAFE

    def test_destructive_methods(self) -> None:
        assert "DELETE" in HTTP_METHODS_DESTRUCTIVE
        assert "GET" not in HTTP_METHODS_DESTRUCTIVE

    def test_idempotent_methods(self) -> None:
        for method in ("GET", "PUT", "DELETE", "HEAD", "OPTIONS"):
            assert method in HTTP_METHODS_IDEMPOTENT
        assert "POST" not in HTTP_METHODS_IDEMPOTENT
        assert "PATCH" not in HTTP_METHODS_IDEMPOTENT


class TestTAGToScope:
    """Tests for Swagger tag to Gitea token scope mapping."""

    def test_known_tags_have_scopes(self) -> None:
        for tag in ("admin", "repository", "issue", "organization", "user"):
            assert tag in TAG_TO_SCOPE

    def test_admin_maps_to_sudo(self) -> None:
        assert TAG_TO_SCOPE["admin"] == "sudo"

    def test_misc_maps_to_misc(self) -> None:
        assert TAG_TO_SCOPE["miscellaneous"] == "misc"
