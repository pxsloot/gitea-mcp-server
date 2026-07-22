"""Integration tests for the MCP server with resources."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig


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
                "gitea_mcp_server.server_setup.resource_setup.register_auto_generated_resources"
            ) as mock_auto:
                with patch(
                    "gitea_mcp_server.server_setup.resource_setup.register_custom_resources"
                ) as mock_custom:
                    mcp = await create_mcp_server(gitea_client)

                    # Verify both registration functions were called
                    mock_auto.assert_called_once()
                    mock_custom.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_generated_resources_use_gitea_uri_scheme(self):
        """Test that auto-generated resources use the gitea:// scheme.

        All auto-generated resource URIs start with gitea:// regardless
        of the API path (e.g. /repos/{owner}/{repo} → gitea://repos/...).
        No double-gitea URIs are produced.
        """
        from gitea_mcp_server import resources as resources_pkg

        # Create a minimal FastMCP mock that tracks registered resources
        mcp = MagicMock()
        mcp.resource = MagicMock()

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
                },
            }
        }

        resources_pkg.register_auto_generated_resources(
            mcp, mock_client, spec, skip_uris=set()
        )

        # Check that the URI is gitea:// -- no double-gitea
        mcp.resource.assert_called()
        uris = [call[0][0] for call in mcp.resource.call_args_list]
        assert "gitea://repos/{owner}/{repo}" in uris, (
            f"Expected gitea://repos/... in {uris}"
        )
        # Ensure no double-gitea URIs remain
        assert not any("gitea://gitea/" in uri for uri in uris), (
            f"Unexpected double-gitea URIs: {uris}"
        )

    @pytest.mark.asyncio
    async def test_custom_resources_register_expected_count(self):
        """Test that register_custom_resources registers the expected number of resources."""
        from gitea_mcp_server import resources as resources_pkg

        mcp = MagicMock()
        mcp.resource = MagicMock()
        mock_client = AsyncMock()

        resources_pkg.register_custom_resources(mcp, mock_client)
        assert mcp.resource.call_count == 12

    @pytest.mark.asyncio
    async def test_custom_resources_include_expected_uris(self):
        """Test that custom resources are registered with proper URIs."""
        from gitea_mcp_server import resources as resources_pkg

        mcp = MagicMock()
        mcp.resource = MagicMock()
        mock_client = AsyncMock()

        resources_pkg.register_custom_resources(mcp, mock_client)
        call_uris = [call[0][0] for call in mcp.resource.call_args_list]
        assert "gitea://repos/{owner}/{repo}" in call_uris
        assert "gitea://repos/{owner}/{repo}/readme" in call_uris
        assert "gitea://repos/{owner}/{repo}/issues" in call_uris
        assert "gitea://users/{username}" in call_uris
