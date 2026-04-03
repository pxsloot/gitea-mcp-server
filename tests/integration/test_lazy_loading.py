"""Integration tests for lazy loading feature."""

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
        tool_filtering_enabled=False,
        enable_lazy_loading=True,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.ssl_cert_file = ssl_cert_file
        self.log_level = log_level
        self.log_format = log_format
        self.tool_filtering_enabled = tool_filtering_enabled
        self.enable_lazy_loading = enable_lazy_loading

    @property
    def base_url(self) -> str:
        return f"{self.url}/api/v1"


class TestLazyLoading:
    """Tests for lazy loading with search transform."""

    @pytest.mark.asyncio
    async def test_lazy_loading_reduces_tool_count(self):
        """Test that lazy loading reduces the number of tools to synthetic + pinned."""
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
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            # Should have synthetic tools
            assert "search_tools" in tool_names, f"Expected search_tools, got: {tool_names}"
            assert "call_tool" in tool_names, f"Expected call_tool, got: {tool_names}"

            # Should have pinned MCP resource tools
            assert "mcp_list_resources" in tool_names
            assert "mcp_read_resource" in tool_names

            # Total should be small (pinned + synthetic)
            assert len(tool_names) <= 10, (
                f"Lazy loading should return at most ~10 tools (pinned + synthetic), got {len(tool_names)}"
            )

    @pytest.mark.asyncio
    async def test_lazy_loading_with_tool_filtering(self):
        """Test lazy loading works with tool filtering enabled."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=True,  # Enable filtering
            enable_lazy_loading=True,
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
                "/admin/settings": {
                    "get": {
                        "operationId": "admin_settings",
                        "summary": "Get admin settings",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            # User is non-admin
            mock_http.get("/api/v1/user").respond(200, json={"login": "user", "admin": False})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            # Should have synthetic tools
            assert "search_tools" in tool_names
            assert "call_tool" in tool_names
            # Should have pinned MCP resource tools
            assert "mcp_list_resources" in tool_names
            assert "mcp_read_resource" in tool_names
            # Admin tool should not appear
            assert "admin_settings" not in tool_names
            # Total should be small
            assert len(tool_names) <= 10, f"Got {len(tool_names)} tools: {tool_names}"

    @pytest.mark.asyncio
    async def test_search_tools_returns_matching_tools(self):
        """Test that search_tools actually finds tools by keyword."""
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
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "list_repo_issues",
                        "summary": "List issues in a repository",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "create_repo_issue",
                        "summary": "Create a new issue",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
                "/repos/{owner}/{repo}/pulls": {
                    "get": {
                        "operationId": "list_repo_pulls",
                        "summary": "List pull requests",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/user/repos": {
                    "get": {
                        "operationId": "list_user_repos",
                        "summary": "List repositories for the authenticated user",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)

            # NOTE: Not calling list_tools before search to avoid cache priming

            # Search for "repo" - all tools contain "repo"
            search_repo = await mcp.call_tool("search_tools", {"query": "repo"})
            repo_tools = search_repo.structured_content.get("result", [])
            repo_names = [t["name"] for t in repo_tools if isinstance(t, dict)]

            # Should find repo-related tools that contain the token "repo"
            # At minimum, the list operations should appear
            assert "list_repo_issues" in repo_names, f"Expected list_repo_issues in {repo_names}"
            assert "list_repo_pulls" in repo_names, f"Expected list_repo_pulls in {repo_names}"

            # Should NOT return synthetic tools
            assert "search_tools" not in repo_names
            assert "call_tool" not in repo_names

            # Additionally, search for "repos" should find list_user_repos
            search_repos = await mcp.call_tool("search_tools", {"query": "repos"})
            repos_tools = search_repos.structured_content.get("result", [])
            repos_names = [t["name"] for t in repos_tools if isinstance(t, dict)]
            assert "list_user_repos" in repos_names, f"Expected list_user_repos in {repos_names}"

    @pytest.mark.asyncio
    async def test_search_works_after_list_tools_cache_priming(self):
        """Regression test: cache poisoning bug. search_tools should work even after list_tools has been called."""
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
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "get": {
                        "operationId": "list_repo_issues",
                        "summary": "List issues in a repository",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/pulls": {
                    "get": {
                        "operationId": "list_repo_pulls",
                        "summary": "List pull requests",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/user/repos": {
                    "get": {
                        "operationId": "list_user_repos",
                        "summary": "List repositories for the authenticated user",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)

            # First, call list_tools to cache the synthetic catalog
            await mcp.list_tools()

            # Now search for "repo" - should still return real tool matches despite cache
            search_repo = await mcp.call_tool("search_tools", {"query": "repo"})
            repo_tools = search_repo.structured_content.get("result", [])
            repo_names = [t["name"] for t in repo_tools if isinstance(t, dict)]

            # Should find tools containing "repo" (the expected ones from spec)
            assert "list_repo_issues" in repo_names, (
                f"Cache poisoning: expected list_repo_issues in {repo_names}"
            )
            assert "list_repo_pulls" in repo_names, (
                f"Cache poisoning: expected list_repo_pulls in {repo_names}"
            )

            # Should NOT return synthetic tools
            assert "search_tools" not in repo_names
            assert "call_tool" not in repo_names
