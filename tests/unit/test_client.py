"""Unit tests for GiteaClient."""

import pytest
import respx
from httpx import Response

from gitea_mcp_server.client import GiteaClient
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
            if hasattr(Config, "_instance"):
                delattr(Config, "_instance")
            yield Config.get()

    @pytest.mark.asyncio
    async def test_successful_request(self, config):
        """Test a successful API request."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/api/v1/user").respond(200, json={"name": "testuser"})

            response = await client.request("GET", "/user")
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "testuser"

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
            route = mock.post("/api/v1/repos").respond(201, json={"id": 1, "name": "test-repo"})

            response = await client.request(
                "POST", "/repos", json={"name": "test-repo", "private": False}
            )
            assert response.status_code == 201
            data = response.json()
            assert data["name"] == "test-repo"

    @pytest.mark.asyncio
    async def test_client_lifecycle(self, config):
        """Test client creation and cleanup."""
        client = GiteaClient(config)
        # Client should be None until accessed
        assert client._client is None
        _ = client.client
        assert client._client is not None

        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_multiple_requests_reuse_client(self, config):
        """Test that multiple requests reuse the same client."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/api/v1/user").respond(200, json={"name": "user1"})
            mock.get("/api/v1/repos").respond(200, json={"repos": []})

            await client.request("GET", "/user")
            await client.request("GET", "/repos")

            assert client._client is not None
            # Check that the base_url is correct
            assert str(client._client.base_url) == "https://git.example.com/api/v1/"

    @pytest.mark.asyncio
    async def test_absolute_url(self, config):
        """Test request with absolute URL."""
        client = GiteaClient(config)

        with respx.mock() as mock:
            # respx matches by path, we need to set host too
            mock.get("https://other.example.com/api/test").respond(200, json={"ok": True})

            response = await client.request("GET", "https://other.example.com/api/test")
            assert response.status_code == 200

    def test_initialization(self, config):
        """Test client initialization."""
        client = GiteaClient(config)
        assert client._config is config
        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager(self, config):
        """Test async context manager."""
        async with GiteaClient(config) as client:
            with respx.mock() as mock:
                mock.get("/api/v1/user").respond(200, json={"name": "test"})
                response = await client.request("GET", "/user")
                assert response.status_code == 200
