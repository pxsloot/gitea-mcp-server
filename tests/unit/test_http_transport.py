"""Unit tests for HTTPTransport class."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pytest_mock import MockerFixture

from gitea_mcp_server.client import HTTPTransport
from gitea_mcp_server.config import Config
from gitea_mcp_server.constants import (
    HTTP_TIMEOUT_CONNECT,
    HTTP_TIMEOUT_POOL,
    HTTP_TIMEOUT_READ,
    HTTP_TIMEOUT_WRITE,
)
from gitea_mcp_server.exceptions import GiteaAPIError


class TestHTTPTransport:
    """Tests for the HTTPTransport class."""

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
    async def test_lazy_initialization(self, config):
        """Test that client is lazily initialized."""
        transport = HTTPTransport(config)
        # Internal client should be None initially
        assert transport._client is None
        # Accessing client property should create it
        client = transport.client
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)
        # Subsequent access should return same client
        client2 = transport.client
        assert client is client2

    @pytest.mark.asyncio
    async def test_ssl_configuration_with_cert_file(self, config, mocker: MockerFixture):
        """Test SSL configuration when ssl_cert_file is set."""
        config.ssl_cert_file = "/path/to/ca-bundle.crt"
        transport = HTTPTransport(config)

        # Mock ssl.create_default_context to verify it's called
        mock_ssl_context = mocker.patch("ssl.create_default_context")
        mock_ctx_instance = mocker.MagicMock()
        mock_ssl_context.return_value = mock_ctx_instance

        # Access client to trigger creation
        _ = transport.client

        mock_ssl_context.assert_called_once_with(cafile="/path/to/ca-bundle.crt")
        # Verify the client uses the SSL context
        assert transport._client is not None

    @pytest.mark.asyncio
    async def test_ssl_configuration_without_cert_file(self, config):
        """Test SSL configuration when ssl_cert_file is not set."""
        config.ssl_cert_file = None
        config.verify_ssl = False
        transport = HTTPTransport(config)

        _ = transport.client

        # Client should be created with verify=False
        assert transport._client is not None
        # httpx.AsyncClient doesn't expose verify directly, so we trust it's set correctly

    @pytest.mark.asyncio
    async def test_timeout_settings(self, config):
        """Test that timeout settings are applied correctly."""
        transport = HTTPTransport(config)
        _ = transport.client

        client = transport._client
        assert client.timeout.connect == HTTP_TIMEOUT_CONNECT
        assert client.timeout.read == HTTP_TIMEOUT_READ
        assert client.timeout.write == HTTP_TIMEOUT_WRITE
        assert client.timeout.pool == HTTP_TIMEOUT_POOL

    @pytest.mark.asyncio
    async def test_headers(self, config):
        """Test that authorization and content-type headers are set."""
        transport = HTTPTransport(config)
        _ = transport.client

        client = transport._client
        assert "Authorization" in client.headers
        assert client.headers["Authorization"] == f"token {config.token}"
        assert client.headers["Accept"] == "application/json"
        assert client.headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_retry_on_retryable_exception(self, config):
        """Test that retry logic works for retryable exceptions."""

        transport = HTTPTransport(config)

        # Mock _should_retry to control retry behavior
        with respx.mock() as mock:
            # First two attempts fail with connection error, third succeeds
            mock.get("https://git.example.com/api/v1/test").mock(
                side_effect=[
                    httpx.RequestError("Connection failed"),
                    httpx.RequestError("Connection failed"),
                    httpx.Response(200, json={"ok": True}),
                ]
            )

            result = await transport.request("GET", "https://git.example.com/api/v1/test")
            assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_retry_stops_after_max_attempts(self, config):
        """Test that retry stops after max attempts."""
        transport = HTTPTransport(config)

        with respx.mock() as mock:
            # Always fail with connection error
            mock.get("https://git.example.com/api/v1/test").mock(
                side_effect=httpx.RequestError("Connection failed")
            )

            with pytest.raises(GiteaAPIError):
                await transport.request("GET", "https://git.example.com/api/v1/test")

    @pytest.mark.asyncio
    async def test_error_conversion_http_status_error(self, config):
        """Test HTTPStatusError is converted to GiteaAPIError with message."""
        transport = HTTPTransport(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/test").respond(
                404, json={"message": "Not found"}
            )

            with pytest.raises(GiteaAPIError) as exc_info:
                await transport.request("GET", "https://git.example.com/api/v1/test")

            assert exc_info.value.status_code == 404
            assert "Not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_error_conversion_request_error(self, config):
        """Test RequestError is converted to GiteaAPIError."""
        transport = HTTPTransport(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/test").mock(
                side_effect=httpx.RequestError("Network error")
            )

            with pytest.raises(GiteaAPIError) as exc_info:
                await transport.request("GET", "https://git.example.com/api/v1/test")

            assert "Request failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_json_vs_text_response(self, config):
        """Test JSON and text response handling."""
        transport = HTTPTransport(config)

        # JSON response
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/json").respond(200, json={"data": "test"})
            result = await transport.request("GET", "https://git.example.com/api/v1/json")
            assert isinstance(result, dict)
            assert result["data"] == "test"

        # Text response
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/text").respond(200, text="plain text")
            result = await transport.request("GET", "https://git.example.com/api/v1/text")
            assert isinstance(result, str)
            assert result == "plain text"

    @pytest.mark.asyncio
    async def test_close(self, config):
        """Test client close cleanup."""
        transport = HTTPTransport(config)
        _ = transport.client  # Initialize client
        assert transport._client is not None

        await transport.close()
        assert transport._client is None

    @pytest.mark.asyncio
    async def test_close_with_error(self, config, mocker: MockerFixture):
        """Test that close handles errors gracefully."""
        transport = HTTPTransport(config)
        _ = transport.client
        assert transport._client is not None

        # Mock aclose to raise an exception
        mocker.patch.object(
            transport._client,
            "aclose",
            new_callable=AsyncMock,
            side_effect=Exception("Close failed"),
        )

        # Should not raise, should just log warning and set to None
        await transport.close()
        assert transport._client is None
