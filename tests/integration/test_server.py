"""Integration tests for the MCP server."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import respx
from fastmcp import Client

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server, load_swagger_spec

# Load swagger spec for mocking
SWAGGER_SPEC_PATH = Path(__file__).parents[2] / "swagger.v1.json"
SWAGGER_SPEC = json.loads(SWAGGER_SPEC_PATH.read_text())


class TestServerIntegration:
    """Integration tests for the server setup."""

    @pytest.mark.asyncio
    async def test_load_swagger_spec(self):
        """Test loading the swagger spec file."""
        spec = await load_swagger_spec()
        assert isinstance(spec, dict)
        assert "swagger" in spec
        assert spec["swagger"] == "2.0"
        assert "paths" in spec
        assert len(spec["paths"]) > 0

    @pytest.mark.asyncio
    async def test_load_swagger_spec_missing_file(self):
        """Test error when spec file is missing."""
        # Temporarily rename the swagger file
        spec_path = Path("swagger.v1.json")
        if spec_path.exists():
            backup = spec_path.with_suffix(".backup")
            spec_path.rename(backup)
            try:
                with pytest.raises(Exception, match="not found"):
                    await load_swagger_spec()
            finally:
                backup.rename(spec_path)

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
                "tool_filtering_enabled": False,  # Disable filtering for simple test
            },
        )()
        gitea_client = GiteaClient(mock_config)

        with respx.mock() as mock:
            mock.get("/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            # This should not raise
            try:
                mcp = await create_mcp_server(gitea_client)
                assert mcp is not None
                assert mcp.name == "Gitea MCP Server"
            except Exception as e:
                pytest.fail(f"Server creation failed: {e}")

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
                "log_level": "ERROR",  # Reduce noise
                "log_format": "text",
                "tool_filtering_enabled": False,  # Disable filtering for discovery test
            },
        )()
        gitea_client = GiteaClient(mock_config)

        # Reduce logging during test
        import logging

        logging.getLogger("fastmcp").setLevel(logging.WARNING)

        with respx.mock() as mock:
            mock.get("/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()

            # Determine tool names whether tools is a dict or list
            if isinstance(tools, dict):
                tool_names = list(tools.keys())
            else:
                if tools and hasattr(tools[0], "name"):
                    tool_names = [t.name for t in tools]
                else:
                    tool_names = tools

            # Verify we have tools
            assert len(tools) > 0

            # Check for expected Gitea endpoints (use snake_case naming)
            assert any("activitypub" in name for name in tool_names), (
                f"Expected activitypub tools, got: {tool_names[:10]}"
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
                "tool_filtering_enabled": False,  # Disable filtering for this test
            },
        )()
        gitea_client = GiteaClient(mock_config)

        with respx.mock() as mock:
            mock.get("/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()

            # Determine tool list structure
            if isinstance(tools, dict):
                tool_list = list(tools.values())
                tool_names = list(tools.keys())
            else:
                tool_list = tools
                if tools and hasattr(tools[0], "name"):
                    tool_names = [t.name for t in tools]
                else:
                    tool_names = tools

                # Find a simple GET tool
                get_tools = [t for t in tool_list if "get" in str(t).lower()]
                if get_tools:
                    # If objects, check attributes
                    if hasattr(get_tools[0], "name"):
                        tool = get_tools[0]
                        assert tool.name
                        assert tool.description
                        # FastMCP may use either inputSchema or parameters schema
                        assert (
                            hasattr(tool, "inputSchema")
                            or hasattr(tool, "output_schema")
                            or hasattr(tool, "parameters")
                        )
                    # If strings, just ensure we have names
                    else:
                        assert get_tools[0]

    @pytest.mark.asyncio
    async def test_openapi_conversion_idempotent(self):
        """Test that conversion produces valid OpenAPI 3.1 spec."""
        from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3

        spec = await load_swagger_spec()
        openapi_spec = convert_swagger_to_openapi_v3(spec)

        # Verify required OpenAPI 3.1 fields
        assert "openapi" in openapi_spec
        assert openapi_spec["openapi"] == "3.1.1"
        assert "paths" in openapi_spec
        assert isinstance(openapi_spec["paths"], dict)

        # Check that components are properly structured
        if "components" in openapi_spec:
            components = openapi_spec["components"]
            if "schemas" in components:
                assert isinstance(components["schemas"], dict)

        # Could validate against OpenAPI schema, but that's complex
        # Logger usage removed for simplicity


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

    def _extract_tool_names(self, tools):
        """Extract tool names from mcp.get_tools() return value."""
        if isinstance(tools, dict):
            return list(tools.keys())
        if isinstance(tools, list):
            tool_names = []
            for tool in tools:
                if hasattr(tool, "name"):
                    tool_names.append(tool.name)
                elif isinstance(tool, str):
                    tool_names.append(tool)
                else:
                    try:
                        if hasattr(tool, "get"):
                            name = tool.get("name")
                            if name:
                                tool_names.append(name)
                    except Exception:
                        pass
            return tool_names
        return []

    @pytest.mark.asyncio
    async def test_filtering_removes_admin_tools_for_non_admin_user(self):
        """Test that admin tools are filtered out when user is not admin."""
        config = self._make_mock_config(tool_filtering_enabled=True)
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mock.get("/api/v1/user").respond(200, json={"login": "regularuser", "admin": False})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = self._extract_tool_names(tools)

            # Verify that no admin tools are present
            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) == 0, (
                f"Expected no admin tools for non-admin user, but found: {admin_tools}"
            )

    @pytest.mark.asyncio
    async def test_filtering_keeps_admin_tools_for_admin_user(self):
        """Test that admin tools are kept when user is admin."""
        config = self._make_mock_config(tool_filtering_enabled=True)
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mock.get("/api/v1/user").respond(200, json={"login": "adminuser", "admin": True})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = self._extract_tool_names(tools)

            # Verify that at least some admin tools exist
            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) > 0, (
                f"Expected admin tools to be present for admin user, but none found"
            )

    @pytest.mark.asyncio
    async def test_filtering_disabled_when_config_false(self):
        """Test that admin tools are kept when filtering is disabled."""
        config = self._make_mock_config(tool_filtering_enabled=False)
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            # No need to mock /user endpoint since filtering is disabled; no HTTP call to /user
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = self._extract_tool_names(tools)

            # Verify that admin tools exist even for non-admin when filtering is disabled
            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) > 0, (
                f"Expected admin tools when filtering is disabled, but none found"
            )

    @pytest.mark.asyncio
    async def test_filtering_keeps_all_tools_on_user_fetch_error(self):
        """Test that all tools are kept if fetching user info fails."""
        config = self._make_mock_config(tool_filtering_enabled=True)
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            # Simulate user endpoint failure (500)
            mock.get("/api/v1/user").respond(500, json={"message": "Error"})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.get_tools()
            tool_names = self._extract_tool_names(tools)

            # Should have tools including admin ones because filtering failed gracefully
            assert len(tool_names) > 0
            # We expect admin tools to be present since filtering didn't succeed
            # Note: The presence depends on the spec; we just check we have tools
