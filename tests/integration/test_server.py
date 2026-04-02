"""Integration tests for the MCP server."""

import logging

import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import extract_tool_names


class TestServerIntegration:
    """Integration tests for the server setup."""

    @pytest.mark.asyncio
    async def test_create_mcp_server(self):
        """Test server creation with mocked config."""
        mock_config = type(
            "MockConfig",
            (),
            {
                "url": "https://git.example.com",
                "base_url": "https://git.example.com/api/v1",
                "token": "test_token",
                "verify_ssl": False,
                "ssl_cert_file": None,
                "log_level": "INFO",
                "log_format": "text",
                "tool_filtering_enabled": False,
            },
        )()
        gitea_client = GiteaClient(mock_config)

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(
                200,
                json={
                    "swagger": "2.0",
                    "info": {"title": "Gitea API", "version": "1.0"},
                    "paths": {},
                    "definitions": {},
                },
            )
            mcp = await create_mcp_server(gitea_client)
            assert mcp is not None
            assert mcp.name == "Gitea MCP Server"

    @pytest.mark.asyncio
    async def test_server_tools_discovery(self):
        """Test that tools are discovered from OpenAPI spec."""
        mock_config = type(
            "MockConfig",
            (),
            {
                "url": "https://git.example.com",
                "base_url": "https://git.example.com/api/v1",
                "token": "test_token",
                "verify_ssl": False,
                "ssl_cert_file": None,
                "log_level": "ERROR",
                "log_format": "text",
                "tool_filtering_enabled": False,
            },
        )()
        gitea_client = GiteaClient(mock_config)

        logging.getLogger("fastmcp").setLevel(logging.WARNING)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "get_repo_issues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/pulls": {
                    "get": {
                        "operationId": "get_repo_pull_requests",
                        "summary": "List pull requests",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()

            if isinstance(tools, dict):
                tool_names = list(tools.keys())
            elif tools and hasattr(tools[0], "name"):
                tool_names = [t.name for t in tools]
            else:
                tool_names = tools

            assert len(tools) > 0
            assert any("issue" in name for name in tool_names), (
                f"Expected issue tools, got: {tool_names[:10]}"
            )

    @pytest.mark.asyncio
    async def test_tool_call_with_mock_client(self):
        """Test calling a tool with a mocked HTTP client."""
        mock_config = type(
            "MockConfig",
            (),
            {
                "url": "https://git.example.com",
                "base_url": "https://git.example.com/api/v1",
                "token": "test_token",
                "verify_ssl": False,
                "ssl_cert_file": None,
                "log_level": "ERROR",
                "log_format": "text",
                "tool_filtering_enabled": False,
            },
        )()
        gitea_client = GiteaClient(mock_config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "get_repo_issues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()

            tool_list = list(tools.values()) if isinstance(tools, dict) else tools

            get_tools = [t for t in tool_list if "get" in str(t).lower()]
            if get_tools:
                if hasattr(get_tools[0], "name"):
                    tool = get_tools[0]
                    assert tool.name
                    assert tool.description
                    assert (
                        hasattr(tool, "inputSchema")
                        or hasattr(tool, "output_schema")
                        or hasattr(tool, "parameters")
                    )
                else:
                    assert get_tools[0]


class TestToolFiltering:
    """Tests for tool permission filtering."""

    def _make_mock_config(self, **overrides):
        """Create a mock config object with required attributes."""
        defaults = {
            "url": "https://git.example.com",
            "base_url": "https://git.example.com/api/v1",
            "token": "test_token",
            "verify_ssl": False,
            "ssl_cert_file": None,
            "log_level": "ERROR",
            "log_format": "text",
            "tool_filtering_enabled": True,
        }
        defaults.update(overrides)
        return type("MockConfig", (), defaults)()

    @pytest.mark.asyncio
    async def test_filtering_removes_admin_tools_for_non_admin_user(self):
        """Test that admin tools are filtered out when user is not admin."""
        config = self._make_mock_config(tool_filtering_enabled=True)
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/admin/settings": {
                    "get": {
                        "operationId": "admin_settings",
                        "summary": "Get admin settings",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "get_repo_issues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("/api/v1/user").respond(200, json={"login": "regularuser", "admin": False})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = extract_tool_names(tools)

            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) == 0, (
                f"Expected no admin tools for non-admin user, but found: {admin_tools}"
            )

    @pytest.mark.asyncio
    async def test_filtering_keeps_admin_tools_for_admin_user(self):
        """Test that admin tools are kept when user is admin."""
        config = self._make_mock_config(tool_filtering_enabled=True)
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/admin/settings": {
                    "get": {
                        "operationId": "admin_settings",
                        "summary": "Get admin settings",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "get_repo_issues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("/api/v1/user").respond(200, json={"login": "adminuser", "admin": True})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = extract_tool_names(tools)

            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) > 0, (
                "Expected admin tools to be present for admin user, but none found"
            )

    @pytest.mark.asyncio
    async def test_filtering_disabled_when_config_false(self):
        """Test that admin tools are kept when filtering is disabled."""
        config = self._make_mock_config(tool_filtering_enabled=False)
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/admin/settings": {
                    "get": {
                        "operationId": "admin_settings",
                        "summary": "Get admin settings",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "get_repo_issues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = extract_tool_names(tools)

            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) > 0, (
                "Expected admin tools when filtering is disabled, but none found"
            )

    @pytest.mark.asyncio
    async def test_filtering_keeps_all_tools_on_user_fetch_error(self):
        """Test that all tools are kept if fetching user info fails."""
        config = self._make_mock_config(tool_filtering_enabled=True)
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/admin/settings": {
                    "get": {
                        "operationId": "get_admin_settings",
                        "summary": "Get admin settings",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "get_repo_issues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("/api/v1/user").respond(500, json={"message": "Error"})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = extract_tool_names(tools)

            assert len(tool_names) > 0
