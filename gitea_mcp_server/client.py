"""HTTP client wrapper for Gitea API with retry and error handling."""

import logging
import ssl
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from gitea_mcp_server.config import Config
from gitea_mcp_server.constants import (
    HTTP_MAX_CONNECTIONS,
    HTTP_MAX_KEEPALIVE_CONNECTIONS,
    HTTP_STATUS_RATE_LIMIT,
    HTTP_STATUS_RETRYABLE,
    HTTP_TIMEOUT_CONNECT,
    HTTP_TIMEOUT_POOL,
    HTTP_TIMEOUT_READ,
    HTTP_TIMEOUT_WRITE,
    RESPONSE_PREVIEW_LIMIT,
    RETRY_MAX_ATTEMPTS,
    RETRY_WAIT_MAX,
    RETRY_WAIT_MIN,
    RETRY_WAIT_MULTIPLIER,
)
from gitea_mcp_server.exceptions import GiteaAPIError
from gitea_mcp_server.pagination import capture_pagination_headers
from gitea_mcp_server.tools.virtual_params import sudo_context

logger = logging.getLogger(__name__)


def _wait_retry(retry_state: RetryCallState) -> float:
    """Custom wait function that respects Retry-After header when available.

    If the exception has a `retry_after` attribute (set by _should_retry)
    that is not None, use that specific wait time. Otherwise fall back to
    exponential backoff.

    Args:
        retry_state: Tenacity retry state object

    Returns:
        Wait time in seconds
    """
    outcome = retry_state.outcome
    if outcome is not None:
        exception = outcome.exception()
        if exception is not None:
            retry_after = getattr(exception, "retry_after", None)
            if retry_after is not None:
                return float(retry_after)
    # Fall back to exponential backoff
    return wait_exponential(
        multiplier=RETRY_WAIT_MULTIPLIER,
        min=RETRY_WAIT_MIN,
        max=RETRY_WAIT_MAX,
    )(retry_state)


def _should_retry(exception: BaseException) -> bool:
    """Determine if an exception should trigger a retry.

    Retry on:
    - Network errors (connection, timeout, etc.)
    - HTTP status codes: 429 (rate limit), 408 (timeout), 500, 502, 503, 504
    Do NOT retry on client errors (4xx) except 429 and 408.
    """
    # Check if exception is our custom GiteaAPIError
    if isinstance(exception, GiteaAPIError):
        if exception.status_code == HTTP_STATUS_RATE_LIMIT:
            # Check for Retry-After header and store it for wait function
            retry_after = exception.headers.get("Retry-After")
            if retry_after:
                try:
                    exception.retry_after = int(retry_after)
                    logger.warning(
                        "Rate limited (429), Retry-After: %s seconds",
                        retry_after,
                    )
                except (ValueError, TypeError):
                    # Invalid Retry-After value, ignore and use exponential
                    logger.warning(
                        "Rate limited (429) with invalid Retry-After header: %s",
                        retry_after,
                    )
            return True
        if exception.status_code in HTTP_STATUS_RETRYABLE:
            return True
        # Retry if caused by httpx.HTTPError but not HTTPStatusError
        if exception.__cause__:
            return isinstance(exception.__cause__, httpx.HTTPError) and not isinstance(
                exception.__cause__, httpx.HTTPStatusError
            )
        return False

    # Direct httpx exceptions (should be rare if we always wrap, but handle anyway)
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code in HTTP_STATUS_RETRYABLE
    return bool(isinstance(exception, httpx.HTTPError))  # Ensure bool return


async def _inject_sudo(request: httpx.Request) -> None:
    """Request hook: inject ``?sudo=<username>`` query parameter when set.

    Reads the ``sudo_context`` :class:`~contextvars.ContextVar` that the
    virtual-param pre-hook sets before each tool call.  When a sudo target
    is active, the query parameter is appended to every request made through
    the shared HTTP client, so the Gitea API executes the call as that user.
    """
    sudo_user = sudo_context.get()
    if sudo_user:
        params = dict(request.url.params)
        params["sudo"] = sudo_user
        request.url = request.url.copy_with(params=params)


class HTTPTransport:
    """HTTP transport layer handling low-level client creation and retry logic."""

    def __init__(self, config: Config):
        """Initialize transport with configuration.

        Args:
            config: Application configuration
        """
        self._config = config
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client (lazy initialization)."""
        if self._client is None:
            # Build verify parameter properly to avoid httpx deprecation warnings
            # ssl_cert_file takes precedence; if set, create SSLContext with that CA bundle
            verify: bool | ssl.SSLContext
            if self._config.ssl_cert_file:
                ctx = ssl.create_default_context(cafile=self._config.ssl_cert_file)
                verify = ctx
            else:
                verify = self._config.verify_ssl

            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers={
                    "Authorization": f"token {self._config.token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                verify=verify,
                timeout=httpx.Timeout(
                    connect=HTTP_TIMEOUT_CONNECT,
                    read=HTTP_TIMEOUT_READ,
                    write=HTTP_TIMEOUT_WRITE,
                    pool=HTTP_TIMEOUT_POOL,
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
                    max_connections=HTTP_MAX_CONNECTIONS,
                ),
                follow_redirects=True,
                event_hooks={
                    "request": [_inject_sudo],
                    "response": [capture_pagination_headers],
                },
            )
        return self._client

    @retry(
        retry=retry_if_exception(_should_retry),
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=_wait_retry,
        reraise=True,
    )
    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Any:
        """Make an HTTP request with retry logic.

        Args:
            method: HTTP method
            url: Absolute URL (must be full URL, not relative)
            **kwargs: Additional httpx arguments (json, params, headers, etc.)

        Returns:
            Parsed JSON response as dict/list, or text content if not JSON

        Raises:
            GiteaAPIError: On API errors after retries exhausted
        """
        try:
            response = await self.client.request(
                method=method,
                url=url,
                **kwargs,
            )

            # Raise for HTTP errors (including 429, which retry decorator will handle)
            response.raise_for_status()

            # Return JSON if content type indicates JSON, otherwise text
            content_type = response.headers.get("Content-Type", "")
            return response.json() if "application/json" in content_type else response.text
        except httpx.HTTPStatusError as e:
            error_msg = f"API request failed: {e!s}"
            try:
                error_data = e.response.json()
                error_detail = error_data.get("message", str(error_data))
                error_msg += f": {error_detail}"
            except Exception:
                logger.exception("Could not parse error response, using text preview")
                # Limit response text to avoid log bloat and potential sensitive data
                preview = e.response.text[:RESPONSE_PREVIEW_LIMIT] if e.response.text else ""
                error_msg += f": {preview}"

            # Add retry-after guidance for rate-limited (429) responses
            if e.response.status_code == HTTP_STATUS_RATE_LIMIT:
                retry_after = e.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        retry_after_secs = int(retry_after)
                        error_msg += f" (retry after {retry_after_secs} seconds)"
                    except (ValueError, TypeError):
                        error_msg += " (rate limited — retry after Retry-After duration)"
                else:
                    error_msg += " (rate limited — retry later)"

            logger.exception(
                "API request failed",
                extra={
                    "method": method,
                    "url": url,
                    "status_code": e.response.status_code,
                    "error": str(e),
                    "response_preview": (
                        e.response.text[:RESPONSE_PREVIEW_LIMIT] + "..."
                        if e.response.text and len(e.response.text) > RESPONSE_PREVIEW_LIMIT
                        else e.response.text
                    ),
                },
            )
            # Capture response headers for rate limiting info
            headers = dict(e.response.headers)
            raise GiteaAPIError(
                error_msg, status_code=e.response.status_code, headers=headers
            ) from e
        except httpx.RequestError as e:
            error_msg = f"Request failed for {method} {url}: {e!s}"
            logger.exception(
                "Request error",
                extra={"method": method, "url": url, "error": str(e)},
            )
            raise GiteaAPIError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error during {method} {url}: {e!s}"
            logger.exception("Unexpected error", extra={"method": method, "url": url})
            raise GiteaAPIError(error_msg) from e

    async def close(self) -> None:
        """Close the HTTP client and cleanup resources."""
        if self._client is not None:
            try:
                await self._client.aclose()
                logger.debug("HTTP client closed")
            except Exception:
                logger.exception("Error closing HTTP client")
            finally:
                self._client = None


class GiteaAPI:
    """API layer handling URL construction and request routing."""

    def __init__(self, transport: HTTPTransport, base_url: str):
        """Initialize API client with transport and base URL.

        Args:
            transport: HTTP transport layer
            base_url: Base URL for the API (will have trailing slashes removed)
        """
        self.transport = transport
        self.base_url = base_url.rstrip("/")

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Make an API request with proper URL construction.

        Args:
            method: HTTP method
            path: API path (relative to base_url, e.g., "/user" or "/repos")
            json: JSON body
            params: Query parameters
            headers: Additional headers
            **kwargs: Additional arguments

        Returns:
            Parsed response from the API

        Raises:
            GiteaAPIError: On API errors
        """
        # Handle absolute URLs (pass through unchanged)
        full_url = path if path.startswith(("http://", "https://")) else f"{self.base_url}{path}"
        return await self.transport.request(
            method, full_url, json=json, params=params, headers=headers, **kwargs
        )


class GiteaClient:
    """HTTP client wrapper for Gitea API with proper lifecycle management."""

    def __init__(self, config: Config):
        self._config = config
        self.transport = HTTPTransport(config)
        self.api = GiteaAPI(self.transport, config.base_url)

    @property
    def config(self) -> Config:
        """Get the application configuration."""
        return self._config

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client (lazy initialization)."""
        return self.transport.client

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Make an HTTP request with retry logic.

        Args:
            method: HTTP method
            url: URL (relative to base_url or absolute)
            json: JSON body
            params: Query parameters
            headers: Additional headers
            **kwargs: Additional httpx arguments

        Returns:
            Parsed JSON response as dict/list, or text content if not JSON

        Raises:
            GiteaAPIError: On API errors after retries exhausted
        """
        full_url = (
            url
            if url.startswith(("http://", "https://"))
            else f"{self._config.base_url.rstrip('/')}{url}"
        )

        return await self.transport.request(
            method, full_url, json=json, params=params, headers=headers, **kwargs
        )

    async def close(self) -> None:
        """Close the HTTP client and cleanup resources."""
        await self.transport.close()

    async def __aenter__(self) -> "GiteaClient":
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: BaseException | None,
    ) -> None:
        """Async context manager exit."""
        await self.close()
