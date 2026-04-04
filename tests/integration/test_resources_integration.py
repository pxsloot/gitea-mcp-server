"""Integration tests for the MCP server with resources."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import extract_tool_names


class SimpleConfig:
    """Simple config stub for tests, mirrors essential Config behavior."""

    def __init__(
        self,
        url="https://git.example.com",
        token="test_token",
        *,
        verify_ssl=False,
        ssl_cert_file=None,
        log_level="ERROR",
        log_format="text",
        tool_filtering_enabled=True,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.ssl_cert_file = ssl_cert_file
        self.log_level = log_level
        self.log_format = log_format
        self.tool_filtering_enabled = tool_filtering_enabled

    @property
    def base_url(self) -> str:
        """Get the API base URL."""
        return f"{self.url}/api/v1"


class TestResourcesIntegration:
    """Integration tests for resources registration."""

    @pytest.mark.asyncio
    async def test_resources_registered_on_server_creation(self):
        """Test that resources are automatically registered when server starts."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {
                        "summary": "Get repository",
                        "operationId": "getRepo",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)

            # Patch the resource registration to track calls
            with patch(
                "gitea_mcp_server.server_setup.resource_registry.register_auto_generated_resources"
            ) as mock_auto:
                with patch(
                    "gitea_mcp_server.server_setup.resource_registry.register_custom_resources"
                ) as mock_custom:
                    mcp = await create_mcp_server(gitea_client)

                    # Verify both registration functions were called
                    mock_auto.assert_called_once()
                    mock_custom.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_generated_resources_use_gitea_uri_scheme(self):
        """Test that auto-generated resources use the gitea:// URI scheme."""
        from gitea_mcp_server import resources
        from gitea_mcp_server.resource_registry import ResourceRegistry

        registry = ResourceRegistry()

        # Create a mock client
        mock_client = AsyncMock()
        mock_config = MagicMock()
        mock_client._config = mock_config

        spec = {
            "paths": {
                "/repos/{owner}/{repo}": {
                    "get": {
                        "summary": "Get repo details",
                        "operationId": "getRepoDetails",
                        "parameters": [
                            {
                                "name": "owner",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "repo",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            }
        }

        resources.register_auto_generated_resources(registry, mock_client, spec, skip_uris=set())

        # Check that resource was registered with gitea:// URI
        reg_resources = registry.list_resources()
        assert len(reg_resources) == 1
        assert reg_resources[0].uri == "gitea://repos/{owner}/{repo}"

    @pytest.mark.asyncio
    async def test_custom_resources_override_with_markdown(self):
        """Test that custom resources are registered with proper URIs."""
        from gitea_mcp_server import resources
        from gitea_mcp_server.resource_registry import ResourceRegistry

        registry = ResourceRegistry()
        mock_client = AsyncMock()

        resources.register_custom_resources(registry, mock_client)

        # Should register multiple resources
        reg_resources = registry.list_resources()
        assert len(reg_resources) == 12

        # Check for some expected URIs
        uris = {r.uri for r in reg_resources}
        assert "gitea://repos/{owner}/{repo}" in uris
        assert "gitea://repos/{owner}/{repo}/readme" in uris
        assert "gitea://repos/{owner}/{repo}/issues" in uris
        assert "gitea://users/{username}" in uris
