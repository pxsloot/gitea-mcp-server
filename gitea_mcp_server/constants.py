"""Centralized constants for Gitea MCP Server.

This module collects all magic numbers and hardcoded values to improve
maintainability and make configuration easier.
"""

# ============================================================================
# Response Formatting
# ============================================================================

RESPONSE_PREVIEW_LIMIT = 100
"""Maximum length for error response previews in logs (characters)."""


# ============================================================================
# HTTP Client Configuration (httpx.AsyncClient)
# ============================================================================

HTTP_TIMEOUT_CONNECT = 10.0
"""Timeout for establishing connection (seconds)."""

HTTP_TIMEOUT_READ = 30.0
"""Timeout for reading response data (seconds)."""

HTTP_TIMEOUT_WRITE = 30.0
"""Timeout for sending request data (seconds)."""

HTTP_TIMEOUT_POOL = 5.0
"""Timeout for acquiring connection from pool (seconds)."""

HTTP_MAX_KEEPALIVE_CONNECTIONS = 20
"""Maximum number of idle keepalive connections per host."""

HTTP_MAX_CONNECTIONS = 100
"""Maximum number of concurrent connections."""


# ============================================================================
# Retry Configuration (tenacity)
# ============================================================================

RETRY_MAX_ATTEMPTS = 3
"""Maximum number of retry attempts for failed requests."""

RETRY_WAIT_MULTIPLIER = 1
"""Multiplier for exponential backoff (seconds)."""

RETRY_WAIT_MIN = 2
"""Minimum wait time between retries (seconds)."""

RETRY_WAIT_MAX = 10
"""Maximum wait time between retries (seconds)."""


# ============================================================================
# Cache Configuration (ResponseCachingMiddleware)
# ============================================================================

CACHE_TTL_DEFAULT = 30.0
"""Default cache TTL for resources (seconds)."""

CACHE_TTL_RESOURCE_LIST = 300.0
"""Cache TTL for resource list operations (seconds, 5 minutes)."""

CACHE_TTL_REPOSITORY = 300.0
"""Cache TTL for repository metadata (seconds, 5 minutes)."""

CACHE_TTL_README = 600.0
"""Cache TTL for README files (seconds, 10 minutes)."""

CACHE_TTL_RELEASES = 600.0
"""Cache TTL for release data (seconds, 10 minutes)."""

CACHE_TTL_USERS = 300.0
"""Cache TTL for user/organization profiles (seconds, 5 minutes)."""

CACHE_MAX_ITEM_SIZE = 100_000_000
"""Maximum size of cached items (bytes, 100MB)."""


# ============================================================================
# Label Cache Configuration
# ============================================================================

LABEL_MAX_LENGTH = 100
"""Maximum length for a label name (characters)."""

PAGE_SIZE_MAX = 100
"""Maximum number of items per page for paginated endpoints."""

LABEL_CACHE_TTL = 300
"""Cache TTL for repository label mappings (seconds, 5 minutes)."""


# ============================================================================
# Search Configuration (TolerantSearchTransform)
# ============================================================================

SEARCH_MAX_RESULTS = 10
"""Maximum number of search results to return."""

SEARCH_ALWAYS_VISIBLE_TOOLS = [
    "search",
    "search_tools",
    "call_tool",
    "tool_info",
    "read_resource",
    "list_resources",
    "search_resources",
    "search_docs",
    "read_doc",
]
"""Tool names that are always visible regardless of lazy loading settings."""


# ============================================================================
# HTTP Status Codes
# ============================================================================

HTTP_STATUS_NOT_FOUND = 404
"""HTTP 404 Not Found status code."""

HTTP_STATUS_RATE_LIMIT = 429
"""HTTP 429 Too Many Requests status code (rate limiting)."""

HTTP_STATUS_RETRYABLE = {HTTP_STATUS_RATE_LIMIT, 408, 500, 502, 503, 504}
"""HTTP status codes that should trigger a retry."""


# ============================================================================
# Resource Invalidation Pattern Names
# ============================================================================
# These are the keys used to identify invalidation patterns. They map to URI templates.

PATTERN_ISSUES_LIST = "issues_list"
PATTERN_PULLS_LIST = "pulls_list"
PATTERN_REPO = "repo"
PATTERN_FILES = "files"


# ============================================================================
# Resource URI Patterns (for cache invalidation)
# ============================================================================
# These are template strings with {placeholders} for path parameters.

RESOURCE_PATTERN_ISSUES_LIST = "gitea://repos/{owner}/{repo}/issues"
RESOURCE_PATTERN_PULLS_LIST = "gitea://repos/{owner}/{repo}/pulls"
RESOURCE_PATTERN_REPO = "gitea://repos/{owner}/{repo}"
RESOURCE_PATTERN_FILES = "gitea://repos/{owner}/{repo}/files/{filepath}"


# ============================================================================
# HTTP Method Semantic Groups
# ============================================================================

HTTP_METHODS_SAFE = {"GET", "HEAD", "OPTIONS"}
"""HTTP methods that are safe (read-only, no side effects)."""

HTTP_METHODS_DESTRUCTIVE = {"DELETE"}
"""HTTP methods that destroy resources."""

HTTP_METHODS_IDEMPOTENT = {"GET", "PUT", "DELETE", "HEAD", "OPTIONS"}
"""HTTP methods that are idempotent (can be repeated safely)."""


# ============================================================================
# Resource Registration Skips
# ============================================================================

# URIs that have custom-formatted resources and should be skipped during
# auto-generated resource registration
AUTO_GENERATED_RESOURCE_SKIP_URIS = {
    "gitea://repos/{owner}/{repo}",
    "gitea://repos/{owner}/{repo}/readme",
    "gitea://repos/{owner}/{repo}/issues",
    "gitea://repos/{owner}/{repo}/pulls",
    "gitea://repos/{owner}/{repo}/files/{path*}",
    "gitea://repos/{owner}/{repo}/releases",
    "gitea://users/{username}",
    "gitea://orgs/{orgname}",
}


# ============================================================================
# Documentation Strings (reused)
# ============================================================================

LABEL_GUIDANCE = (
    "\n\n**Labels**: You may provide existing label names (strings) or IDs (integers). "
    "Both are validated against the repository's existing labels. "
    "Call `list_labels(owner, repo)` or read `gitea://repos/{owner}/{repo}/labels` "
    "to see available labels. Unknown names or IDs will produce an error "
    "listing available labels."
)
"""Guidance text added to tools that accept label parameters."""


# ============================================================================
# BM25 Search Configuration
# ============================================================================

SEARCH_MIN_TOKEN_LENGTH = 2
"""Minimum character length for search tokens."""

SEARCH_NAME_BOOST = 3
"""Number of times tool name is included in searchable text to boost relevance."""

SEARCH_MIN_SCORE = 0.1
"""Minimum normalized BM25 score (0.0-1.0) for a document to be considered a match.

A score of 0.0 means any positive overlap counts; 0.1 means at least 10% as
relevant as the top result; 1.0 means only perfect matches.  Agents can
override this per-query via the ``min_score`` parameter on search tools.
"""

SEARCH_CATEGORY_ALIASES: dict[str, str] = {
    "pull_request": "pull request pr",
    "issue": "issue issues bug",
    "repository": "repo repository repos",
    "repo": "repo repository repos",
    "organization": "org organization team",
    "org": "org organization team",
    "user": "user users account",
}
"""Expanded aliases for category tags to improve search matching."""


# ============================================================================
# Tool Scope Mapping (Swagger tag → Gitea token scope)
# ============================================================================

TAG_TO_SCOPE: dict[str, str] = {
    "admin": "sudo",
    "repository": "repository",
    "issue": "issue",
    "organization": "organization",
    "user": "user",
    "notification": "notification",
    "package": "package",
    "activitypub": "activitypub",
    "miscellaneous": "misc",
    "settings": "repository",
}


# ============================================================================
# Cache Invalidation Patterns
# ============================================================================
# Each entry: (path_prefix, match_type, [pattern_names])
# match_type: None (prefix match) or "exact" (exact match)

TOOL_INVALIDATION_PATTERNS: list[tuple[str, str | None, list[str]]] = [
    (
        "/repos/{owner}/{repo}/issues",
        None,
        [PATTERN_ISSUES_LIST],
    ),
    (
        "/repos/{owner}/{repo}/pulls",
        None,
        [PATTERN_PULLS_LIST],
    ),
    ("/repos/{owner}/{repo}", "exact", [PATTERN_REPO]),
    ("/repos/{owner}/{repo}/contents", None, [PATTERN_FILES]),
    ("/repos/{owner}/{repo}/labels", None, [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]),
    ("/repos/{owner}/{repo}/milestones", None, [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]),
    ("/repos/{owner}/{repo}/releases", None, [PATTERN_REPO]),
    ("/repos/{owner}/{repo}/topics", None, [PATTERN_REPO]),
]
