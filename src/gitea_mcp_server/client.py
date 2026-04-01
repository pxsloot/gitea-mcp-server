"""HTTP client wrapper for Gitea API with retry and error handling."""

import asyncio
import logging
from typing import Any

import httpx
import ssl
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from gitea_mcp_server.config import Config
from gitea_mcp_server.exceptions import GiteaAPIError

logger = logging.getLogger(__name__)


def _should_retry(exception: Exception) -> bool:
    """Determine if an exception should trigger a retry.

    Retry on:
    - Network errors (connection, timeout, etc.)
    - HTTP status codes: 429 (rate limit), 408 (timeout), 500, 502, 503, 504
    Do NOT retry on client errors (4xx) except 429 and 408.
    """
    # Check if exception is our custom GiteaAPIError
    if isinstance(exception, GiteaAPIError):
        if exception.status_code in {429, 408, 500, 502, 503, 504}:
            return True
        # Check if underlying cause is a network-level HTTPError (non-status)
        cause = exception.__cause__
        if (
            cause
            and isinstance(cause, httpx.HTTPError)
            and not isinstance(cause, httpx.HTTPStatusError)
        ):
            return True
        return False

    # Direct httpx exceptions (should be rare if we always wrap, but handle anyway)
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code in {429, 408, 500, 502, 503, 504}
    if isinstance(exception, httpx.HTTPError):
        # Includes network errors, timeouts, etc.
        return True

    return False


class GiteaClient:
    """HTTP client wrapper for Gitea API with proper lifecycle management."""

    def __init__(self, config: Config):
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

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
                    connect=10.0,
                    read=30.0,
                    write=30.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
                follow_redirects=True,
            )
        return self._client

    @retry(
        retry=retry_if_exception(_should_retry),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request with retry logic.

        Args:
            method: HTTP method
            url: URL (relative to base_url or absolute)
            json: JSON body
            params: Query parameters
            headers: Additional headers
            **kwargs: Additional httpx arguments

        Returns:
            HTTP response

        Raises:
            GiteaAPIError: On API errors after retries exhausted
        """
        try:
            response = await self.client.request(
                method=method,
                url=url,
                json=json,
                params=params,
                headers=headers,
                **kwargs,
            )

            # Raise for HTTP errors (including 429, which retry decorator will handle)
            response.raise_for_status()

            return response

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code} error for {method} {url}"
            try:
                error_data = e.response.json()
                error_detail = error_data.get("message", str(error_data))
                error_msg += f": {error_detail}"
            except Exception:
                # Limit response text to avoid log bloat and potential sensitive data
                preview = e.response.text[:200] if e.response.text else ""
                error_msg += f": {preview}"

            logger.error(
                "API request failed",
                extra={
                    "method": method,
                    "url": url,
                    "status_code": e.response.status_code,
                    # Truncate response preview to reduce sensitive data exposure
                    "response_preview": (
                        e.response.text[:100] + "..."
                        if e.response.text and len(e.response.text) > 100
                        else e.response.text
                    )
                    if e.response.text
                    else None,
                },
                exc_info=True,
            )

            raise GiteaAPIError(error_msg, status_code=e.response.status_code) from e

        except httpx.RequestError as e:
            error_msg = f"Request failed for {method} {url}: {e!s}"
            logger.error(
                "Request error",
                extra={"method": method, "url": url, "error": str(e)},
                exc_info=True,
            )
            raise GiteaAPIError(error_msg) from e

        except Exception as e:
            error_msg = f"Unexpected error during {method} {url}: {e!s}"
            logger.error("Unexpected error", extra={"method": method, "url": url}, exc_info=True)
            raise GiteaAPIError(error_msg) from e

    async def close(self) -> None:
        """Close the HTTP client and cleanup resources."""
        if self._client is not None:
            try:
                await self._client.aclose()
                logger.debug("HTTP client closed")
            except Exception:
                logger.warning("Error closing HTTP client", exc_info=True)
            finally:
                self._client = None

    async def __aenter__(self) -> "GiteaClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
