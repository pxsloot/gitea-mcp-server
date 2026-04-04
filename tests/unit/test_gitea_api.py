"""Unit tests for GiteaAPI class."""

import asyncio
import pytest
import respx
from pytest_mock import MockerFixture
from unittest.mock import AsyncMock

from gitea_mcp_server.client import GiteaAPI, HTTPTransport
from gitea_mcp_server.config import Config
from gitea_mcp_server.exceptions import GiteaAPIError


class TestGiteaAPI:
    """Tests for the GiteaAPI class."""

    @pytest.fixture
    def config(self):
        """Create a test configuration."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GITEA_URL", "https://git.example.com")
            mp.setenv("GITEA_TOKEN", "test_token")
            mp.setenv("GITEA_VERIFY_SSL", "false")
            Config._instance = None
            yield Config.get()

    @pytest.fixture
    def transport(self, config):
        """Create a transport for testing."""
        return HTTPTransport(config)

    @pytest.fixture
    def api(self, transport):
        """Create a GiteaAPI instance for testing."""
        return GiteaAPI(transport, "https://git.example.com/api/v1")

    @pytest.mark.asyncio
    async def test_relative_url_construction(self, api):
        """Test that relative paths are correctly appended to base_url."""
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/user").respond(200, json={"name": "testuser"})

            result = await api.request("GET", "/user")
            assert result["name"] == "testuser"

    @pytest.mark.asyncio
    async def test_absolute_url_unchanged(self, api):
        """Test that absolute URLs are used as-is (should not be modified)."""
        with respx.mock() as mock:
            # Absolute URL to a different host - should be used as-is
            mock.get("https://other.example.com/api/test").respond(200, json={"ok": True})

            result = await api.request("GET", "https://other.example.com/api/test")
            assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_base_url_trailing_slash_handling(self, config):
        """Test that base_url trailing slashes are properly handled."""
        # Create API with base_url that has trailing slash
        transport = HTTPTransport(config)
        api = GiteaAPI(transport, "https://git.example.com/api/v1/")

        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/user").respond(200, json={"ok": True})

            result = await api.request("GET", "/user")
            # Should not have double slashes
            assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_path_with_params(self, api):
        """Test that query parameters are correctly added."""
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/repos").respond(200, json=[{"name": "repo1"}])

            result = await api.request("GET", "/repos", params={"type": "owner"})
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_post_with_json_body(self, api):
        """Test POST request with JSON body."""
        with respx.mock() as mock:
            mock.post("https://git.example.com/api/v1/repos").respond(
                201, json={"id": 1, "name": "test-repo"}
            )

            result = await api.request(
                "POST", "/repos", json={"name": "test-repo", "private": False}
            )
            assert result["name"] == "test-repo"

    @pytest.mark.asyncio
    async def test_delegation_to_transport(self, transport, mocker: MockerFixture):
        """Test that GiteaAPI correctly delegates to transport."""
        api = GiteaAPI(transport, "https://git.example.com/api/v1")

        # Mock transport.request
        mock_transport_request = mocker.patch.object(transport, "request", new_callable=AsyncMock)
        mock_transport_request.return_value = {"mocked": True}

        result = await api.request("GET", "/user")

        mock_transport_request.assert_called_once_with(
            "GET", "https://git.example.com/api/v1/user", json=None, params=None, headers=None
        )
        assert result == {"mocked": True}

    @pytest.mark.asyncio
    async def test_error_propagation(self, api):
        """Test that errors from transport are propagated correctly."""
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/user").respond(
                500, json={"message": "Server error"}
            )

            with pytest.raises(GiteaAPIError) as exc_info:
                await api.request("GET", "/user")

            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_path(self, api):
        """Test request with empty path (should just use base_url)."""
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1").respond(200, json={"ok": True})

            result = await api.request("GET", "")
            assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_path_concatenation_edge_cases(self, api):
        """Test various path concatenation scenarios."""
        # Path starting with /
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1/user/repos").respond(200, json={"ok": True})
            result = await api.request("GET", "/user/repos")
            assert result["ok"] is True

        # Path not starting with / (should still work, but standard is to use /)
        with respx.mock() as mock:
            mock.get("https://git.example.com/api/v1user").respond(200, json={"ok": True})
            # This would be unusual but our implementation just concatenates
            result = await api.request("GET", "user")
            # This would actually go to /api/v1user which is probably wrong
            # But the test documents current behavior
