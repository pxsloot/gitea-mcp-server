"""Integration tests for the MCP server."""

import logging

import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig, extract_tool_names


class TestServerIntegration:
    """Integration tests for the server setup."""

    @pytest.mark.asyncio
    async def test_create_mcp_server(self):
        """Test server creation with mocked config."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="INFO",
            log_format="text",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

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
    async def test_server_instructions_present(self):
        """Test that server instructions are properly set."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            log_format="text",
            tool_filtering_enabled=False,
            enable_lazy_loading=False,
        )
        gitea_client = GiteaClient(config)

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
            # FastMCP stores instructions in the `_instructions` attribute
            # or it's accessible via the server's initialization info
            assert mcp is not None
            # Check that instructions exist and contain key phrases
            instructions = getattr(mcp, "_instructions", None) or getattr(mcp, "instructions", None)
            assert instructions is not None, "Server should have instructions set"
            assert isinstance(instructions, str)
            assert "Gitea MCP Server" in instructions
            assert "Authentication" in instructions
            assert "lazy loading" in instructions.lower() or "search" in instructions.lower()

    @pytest.mark.asyncio
    async def test_server_tools_discovery(self):
        """Test that tools are discovered from OpenAPI spec."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

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
            # FastMCP 3.x: list_tools returns a list of tool objects
            tools = await mcp.list_tools()
            tool_names = [t.name for t in tools]

            assert len(tools) > 0
            assert any("issue" in name for name in tool_names), (
                f"Expected issue tools, got: {tool_names[:10]}"
            )

    @pytest.mark.asyncio
    async def test_tool_call_with_mock_client(self):
        """Test calling a tool with a mocked HTTP client."""
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
            tools = await mcp.list_tools()

            get_tools = [t for t in tools if "get" in str(t).lower()]
            if get_tools:
                tool = get_tools[0]
                assert tool.name
                assert tool.description
                assert hasattr(tool, "inputSchema") or hasattr(tool, "output_schema")


class TestSyntheticToolMetadata:
    """Integration tests for synthetic tool metadata (descriptions, etc.)."""

    def _synthetic_base_names(self):
        return [
            "search",
            "search_tools",
            "call_tool",
            "tool_info",
            "list_resources",
            "read_resource",
            "search_resources",
            "search_docs",
            "read_doc",
        ]

    def _expected_synthetic_names(self, prefix: str) -> list[str]:
        """Build expected synthetic tool names with the given prefix."""
        return [f"{prefix}{base}" if prefix else base for base in self._synthetic_base_names()]

    @pytest.mark.asyncio
    async def test_extension_metadata_transform_applies_yaml_overrides(self, monkeypatch):
        """ExtensionMetadataTransform should apply YAML description overrides to tools."""
        monkeypatch.setattr(
            "gitea_mcp_server.server_setup.spec_loader.load_mcp_extensions",
            lambda: {"tool_names": {"search": {"description": "CUSTOM SEARCH DESCRIPTION"}}},
        )

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
            enable_lazy_loading=True,
        )
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "basePath": "/api/v1",
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
            tools = await mcp.list_tools()
            tool_map = {t.name: t for t in tools}

            search_tool = tool_map.get("gitea_search")
            assert search_tool is not None, "gitea_search should be registered"
            assert search_tool.description == "CUSTOM SEARCH DESCRIPTION", (
                f"Expected YAML override, got {search_tool.description!r}"
            )

            from fastmcp.server.context import Context

            ctx = Context(fastmcp=mcp)
            retrieved = await ctx.fastmcp.get_tool("gitea_search")
            assert retrieved is not None
            assert retrieved.description == "CUSTOM SEARCH DESCRIPTION", (
                f"get_tool should also show YAML override, got {retrieved.description!r}"
            )

    @pytest.mark.asyncio
    async def test_synthetic_tools_have_descriptions(self):
        """All synthetic tools must have non-empty descriptions."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
            enable_lazy_loading=True,
        )
        gitea_client = GiteaClient(config)

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
            tools = await mcp.list_tools()
            tool_map = {t.name: t for t in tools}

            expected = self._expected_synthetic_names(config.tool_prefix or "")
            missing = []
            for name in expected:
                t = tool_map.get(name)
                if t is None:
                    missing.append(f"{name} (not registered)")
                elif not t.description or not t.description.strip():
                    missing.append(f"{name} (empty description: {t.description!r})")

            assert not missing, (
                f"{len(missing)} synthetic tool(s) with missing or empty descriptions:\n  "
                + "\n  ".join(missing)
            )


class TestCustomResources:
    """Integration tests for custom resource reading."""

    @pytest.mark.asyncio
    async def test_read_server_info(self):
        """Regression test: reading gitea://server/info should succeed.

        The get_server_info() function takes no parameters (it closes over
        openapi_spec from the outer scope). This should be registered and
        readable without crashes.
        """
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea Test API", "version": "9.9.9"},
            "basePath": "/api/v1",
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)

            from fastmcp.server.context import Context

            ctx = Context(fastmcp=mcp)
            result = await ctx.read_resource("gitea://server/info")
            assert len(result.contents) > 0
            text = result.contents[0].content
            assert "Server Information" in text
            assert "Gitea Test API" in text
            assert "9.9.9" in text


class TestToolFiltering:
    """Tests for tool permission filtering."""

    def _make_config(self, **overrides):
        """Create a SimpleConfig instance with given overrides."""
        return SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            verify_ssl=False,
            ssl_cert_file=None,
            log_level="ERROR",
            log_format="text",
            tool_filtering_enabled=overrides.get("tool_filtering_enabled", True),
        )

    @pytest.mark.asyncio
    async def test_filtering_removes_admin_tools_for_non_admin_user(self):
        """Test that admin tools are filtered out when user is not admin."""
        config = self._make_config(tool_filtering_enabled=True)
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
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) == 0, (
                f"Expected no admin tools for non-admin user, but found: {admin_tools}"
            )

    @pytest.mark.asyncio
    async def test_filtering_keeps_admin_tools_for_admin_user(self):
        """Test that admin tools are kept when user is admin."""
        config = self._make_config(tool_filtering_enabled=True)
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
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            prefix = config.tool_prefix or ""
            admin_tools = [name for name in tool_names if name.startswith(f"{prefix}admin")]
            assert len(admin_tools) > 0, (
                f"Expected admin tools to be present for admin user, but none found in {tool_names}"
            )

    @pytest.mark.asyncio
    async def test_filtering_disabled_when_config_false(self):
        """Test that admin tools are kept when filtering is disabled."""
        config = self._make_config(tool_filtering_enabled=False)
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
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            prefix = config.tool_prefix or ""
            admin_tools = [name for name in tool_names if name.startswith(f"{prefix}admin")]
            assert len(admin_tools) > 0, (
                f"Expected admin tools when filtering is disabled, but none found in {tool_names}"
            )

    @pytest.mark.asyncio
    async def test_filtering_keeps_all_tools_on_user_fetch_error(self):
        """Test that all tools are kept if fetching user info fails."""
        config = self._make_config(tool_filtering_enabled=True)
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
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            assert len(tool_names) > 0
