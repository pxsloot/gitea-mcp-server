"""Unit tests for GiteaClient."""

from unittest.mock import Mock

import httpx
import pytest
import respx

from gitea_mcp_server.client import (
    GiteaClient,
    _inject_sudo,
    _should_retry,
    _wait_retry,
)
from gitea_mcp_server.config import Config
from gitea_mcp_server.exceptions import GiteaAPIError


class TestGiteaClient:
    """Tests for the GiteaClient class."""

    @pytest.fixture
    def config(self):
        """Create a test configuration."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GITEA_URL", "https://git.example.com")
            mp.setenv("GITEA_TOKEN", "test_token")
            mp.setenv("GITEA_VERIFY_SSL", "false")
            Config._instance = None
            yield Config.get()

    @pytest.mark.asyncio
    async def test_successful_request(self, config):
        """Test a successful API request."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/api/v1/user").respond(200, json={"name": "testuser"})

            result = await client.request("GET", "/user")
            # Should return parsed JSON (dict)
            assert isinstance(result, dict)
            assert result["name"] == "testuser"

    @pytest.mark.asyncio
    async def test_404_error(self, config):
        """Test 404 error handling."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/api/v1/user/repos").respond(404, json={"message": "Not found"})

            with pytest.raises(GiteaAPIError) as exc_info:
                await client.request("GET", "/user/repos")
            assert exc_info.value.status_code == 404
            assert "Not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_500_error(self, config):
        """Test 500 error handling."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/api/v1/user").respond(500, json={"message": "Internal Server Error"})

            with pytest.raises(GiteaAPIError) as exc_info:
                await client.request("GET", "/user")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_connection_error(self, config):
        """Test connection error (should retry then fail)."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            # Simulate connection error
            mock.get("/api/v1/user").mock(side_effect=Exception("Connection failed"))

            with pytest.raises(GiteaAPIError):
                await client.request("GET", "/user")

    @pytest.mark.asyncio
    async def test_successful_post_with_json(self, config):
        """Test successful POST with JSON body."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.post("/api/v1/repos").respond(201, json={"id": 1, "name": "test-repo"})

            result = await client.request(
                "POST", "/repos", json={"name": "test-repo", "private": False}
            )
            # Should return parsed JSON (dict)
            assert isinstance(result, dict)
            assert result["name"] == "test-repo"

    @pytest.mark.asyncio
    async def test_client_lifecycle(self, config):
        """Test client creation and cleanup."""
        client = GiteaClient(config)
        transport_client = client.transport.client
        assert transport_client is not None

        await client.close()
        # After close, accessing client creates a new one
        new_client = client.client
        assert new_client is not None

    @pytest.mark.asyncio
    async def test_multiple_requests_reuse_client(self, config):
        """Test that multiple requests reuse the same transport."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/api/v1/user").respond(200, json={"name": "user1"})
            mock.get("/api/v1/repos").respond(200, json={"repos": []})

            await client.request("GET", "/user")
            await client.request("GET", "/repos")

            transport_client = client.transport.client
            assert transport_client is not None
            assert str(transport_client.base_url) == "https://git.example.com/api/v1/"

    @pytest.mark.asyncio
    async def test_absolute_url(self, config):
        """Test request with absolute URL."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            # respx matches by path, we need to set host too
            mock.get("https://other.example.com/api/test").respond(200, json={"ok": True})

            result = await client.request("GET", "https://other.example.com/api/test")
            # Should return parsed JSON (dict)
            assert isinstance(result, dict)
            assert result["ok"] is True

    def test_initialization(self, config):
        """Test client initialization."""
        client = GiteaClient(config)
        assert client._config is config
        assert client.transport is not None

    @pytest.mark.asyncio
    async def test_context_manager(self, config):
        """Test async context manager."""
        async with GiteaClient(config) as client:
            with respx.mock() as mock:
                mock.get("/api/v1/user").respond(200, json={"name": "test"})
                result = await client.request("GET", "/user")
                # Should return parsed JSON (dict)
                assert isinstance(result, dict)
                assert result["name"] == "test"

    @pytest.mark.asyncio
    async def test_429_with_retry_after_header(self, config):
        """Test 429 rate limit respects Retry-After header."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            # First two requests return 429 with Retry-After: 1 second
            # Third request succeeds
            mock.get("/api/v1/user").respond(
                429,
                json={"message": "Rate limit exceeded"},
                headers={"Retry-After": "1"},
            )
            mock.get("/api/v1/user").respond(
                429,
                json={"message": "Rate limit exceeded"},
                headers={"Retry-After": "1"},
            )
            mock.get("/api/v1/user").respond(200, json={"name": "testuser"})

            result = await client.request("GET", "/user")

            # Should eventually succeed
            assert result["name"] == "testuser"

    @pytest.mark.asyncio
    async def test_429_without_retry_after_uses_exponential(self, config):
        """Test 429 without Retry-After falls back to exponential backoff."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            # Fail twice then succeed
            mock.get("/api/v1/user").respond(429, json={"message": "Rate limit"})
            mock.get("/api/v1/user").respond(429, json={"message": "Rate limit"})
            mock.get("/api/v1/user").respond(200, json={"name": "testuser"})

            result = await client.request("GET", "/user")
            assert result["name"] == "testuser"

    @pytest.mark.asyncio
    async def test_429_retry_exhaustion(self, config):
        """Test 429 responses exhaust retry limit and raise error with retry-after guidance."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            # Always return 429 with Retry-After
            mock.get("/api/v1/user").respond(
                429,
                json={"message": "Rate limit exceeded"},
                headers={"Retry-After": "1"},
            )

            with pytest.raises(GiteaAPIError) as exc_info:
                await client.request("GET", "/user")

            assert exc_info.value.status_code == 429
            assert "retry after 1 seconds" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_429_retry_exhaustion_no_retry_after(self, config):
        """Test 429 exhaustion without Retry-After header says 'retry later'."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            # Always return 429 without Retry-After header
            mock.get("/api/v1/user").respond(
                429,
                json={"message": "Rate limit exceeded"},
            )

            with pytest.raises(GiteaAPIError) as exc_info:
                await client.request("GET", "/user")

            assert exc_info.value.status_code == 429
            assert "retry later" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_429_retry_exhaustion_invalid_retry_after(self, config):
        """Test 429 exhaustion with invalid Retry-After header."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/api/v1/user").respond(
                429,
                json={"message": "Rate limit exceeded"},
                headers={"Retry-After": "not-a-number"},
            )

            with pytest.raises(GiteaAPIError) as exc_info:
                await client.request("GET", "/user")

            assert exc_info.value.status_code == 429
            assert "retry after Retry-After duration" in str(exc_info.value)

    def test_should_retry_with_retry_after_header(self):
        """Test _should_retry extracts Retry-After from 429."""
        # Create a GiteaAPIError with headers containing Retry-After
        error = GiteaAPIError(
            "Rate limited",
            status_code=429,
            headers={"Retry-After": "5", "X-RateLimit-Limit": "100"},
        )
        assert _should_retry(error) is True
        assert hasattr(error, "retry_after")
        assert error.retry_after == 5

    def test_should_retry_429_without_retry_after(self):
        """Test _should_retry returns True for 429 without Retry-After."""
        error = GiteaAPIError("Rate limited", status_code=429)
        assert _should_retry(error) is True
        # retry_after should be None (attribute exists but not set)
        assert error.retry_after is None

    def test_should_retry_other_status_codes(self):
        """Test _should_retry for other retryable status codes."""
        for status in [408, 500, 502, 503, 504]:
            error = GiteaAPIError("Server error", status_code=status)
            assert _should_retry(error) is True

    def test_should_not_retry_4xx_except_429_408(self):
        """Test _should_retry does not retry on other 4xx errors."""
        for status in [400, 401, 403, 404, 405]:
            error = GiteaAPIError("Client error", status_code=status)
            assert _should_retry(error) is False

    def test_wait_retry_uses_retry_after_when_present(self):
        """Test _wait_retry uses retry_after attribute when available."""
        # Create a mock retry state with an exception that has retry_after
        mock_exception = GiteaAPIError("Rate limited", status_code=429)
        mock_exception.retry_after = 3

        mock_retry_state = Mock()
        mock_retry_state.outcome = Mock()
        mock_retry_state.outcome.exception.return_value = mock_exception

        wait_time = _wait_retry(mock_retry_state)
        assert wait_time == 3

    def test_wait_retry_falls_back_to_exponential_when_no_retry_after(self):
        """Test _wait_retry uses exponential backoff when retry_after not set."""
        mock_exception = GiteaAPIError("Server error", status_code=500)
        # No retry_after attribute

        mock_retry_state = Mock()
        mock_retry_state.outcome = Mock()
        mock_retry_state.outcome.exception.return_value = mock_exception
        mock_retry_state.attempt_number = 2  # Second attempt

        wait_time = _wait_retry(mock_retry_state)
        assert isinstance(wait_time, (int, float))
        assert wait_time >= 0

    def test_should_retry_invalid_retry_after_header(self):
        """Test _should_retry handles invalid Retry-After header gracefully."""
        error = GiteaAPIError(
            "Rate limited",
            status_code=429,
            headers={"Retry-After": "not-a-number"},
        )
        assert _should_retry(error) is True
        # retry_after should remain None when int() conversion fails
        # (the ValueError is caught, the attribute is never updated from its default)
        assert error.retry_after is None

    def test_should_retry_httpx_http_status_error(self):
        """Test _should_retry handles direct httpx.HTTPStatusError."""
        response = httpx.Response(500, request=httpx.Request("GET", "https://example.com/api"))
        error = httpx.HTTPStatusError("Server error", request=response.request, response=response)
        assert _should_retry(error) is True

        # Non-retryable status code
        response2 = httpx.Response(404, request=httpx.Request("GET", "https://example.com/api"))
        error2 = httpx.HTTPStatusError("Not found", request=response2.request, response=response2)
        assert _should_retry(error2) is False

    def test_should_retry_generic_httpx_error(self):
        """Test _should_retry handles generic httpx.HTTPError (not HTTPStatusError)."""
        error = httpx.TimeoutException("Connection timed out")
        assert _should_retry(error) is True

    def test_should_retry_non_httpx_error(self):
        """Test _should_retry returns False for non-httpx exceptions."""
        error = ValueError("Some other error")
        assert _should_retry(error) is False

    def test_should_retry_gitea_error_with_cause_httpx_error(self):
        """Test _should_retry with GiteaAPIError caused by httpx.HTTPError."""
        cause = httpx.TimeoutException("Timed out")
        error = GiteaAPIError("Request failed", status_code=500)
        error.__cause__ = cause
        assert _should_retry(error) is True

    def test_should_retry_gitea_error_with_cause_http_status_error(self):
        """Test _should_retry with GiteaAPIError caused by httpx.HTTPStatusError."""
        response = httpx.Response(404, request=httpx.Request("GET", "https://example.com/api"))
        cause = httpx.HTTPStatusError("Not found", request=response.request, response=response)
        error = GiteaAPIError("Not found", status_code=404)
        error.__cause__ = cause
        # httpx.HTTPStatusError is also httpx.HTTPError, but the check says:
        # isinstance(exception.__cause__, httpx.HTTPError) AND NOT isinstance(exception.__cause__, httpx.HTTPStatusError)
        assert _should_retry(error) is False

    def test_should_retry_gitea_error_without_cause(self):
        """Test _should_retry with GiteaAPIError having no __cause__."""
        error = GiteaAPIError("Client error", status_code=400)
        assert _should_retry(error) is False


# ---------------------------------------------------------------------------
# _inject_sudo request hook
# ---------------------------------------------------------------------------


class TestInjectSudo:
    """Tests for the _inject_sudo httpx request hook."""

    def test_injects_sudo_query_param(self):
        """Adds ?sudo=<username> when sudo_context is set."""
        from gitea_mcp_server.tools.virtual_params import sudo_context

        sudo_context.set("alice")
        request = httpx.Request("GET", "https://git.example.com/api/v1/user")
        assert "sudo" not in dict(request.url.params)

        import asyncio
        asyncio.run(_inject_sudo(request))

        params = dict(request.url.params)
        assert params["sudo"] == "alice"
        sudo_context.set(None)

    def test_preserves_existing_query_params(self):
        """Appends sudo= to existing query params, preserving them."""
        from gitea_mcp_server.tools.virtual_params import sudo_context

        sudo_context.set("bob")
        request = httpx.Request(
            "GET",
            "https://git.example.com/api/v1/repos/owner/repo/issues?page=1&limit=10",
        )
        assert dict(request.url.params) == {"page": "1", "limit": "10"}

        import asyncio
        asyncio.run(_inject_sudo(request))

        params = dict(request.url.params)
        assert params["sudo"] == "bob"
        assert params["page"] == "1"
        assert params["limit"] == "10"
        sudo_context.set(None)

    def test_no_op_when_sudo_not_set(self):
        """Does not modify URL when sudo_context is None."""
        from gitea_mcp_server.tools.virtual_params import sudo_context

        sudo_context.set(None)
        request = httpx.Request("GET", "https://git.example.com/api/v1/user")

        import asyncio
        asyncio.run(_inject_sudo(request))

        assert "sudo" not in dict(request.url.params)
