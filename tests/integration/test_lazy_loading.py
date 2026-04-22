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
        tool_prefix="gitea_",
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.ssl_cert_file = ssl_cert_file
        self.log_level = log_level
        self.log_format = log_format
        self.tool_filtering_enabled = tool_filtering_enabled
        self.enable_lazy_loading = enable_lazy_loading
        self.tool_prefix = tool_prefix

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
                        "operationId": "issueListIssues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/pulls": {
                    "get": {
                        "operationId": "repoListPullRequests",
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

            prefix = config.tool_prefix or ""

            # Should have synthetic tools
            assert f"{prefix}search_tools" in tool_names, f"Expected {prefix}search_tools, got: {tool_names}"
            assert f"{prefix}call_tool" in tool_names, f"Expected {prefix}call_tool, got: {tool_names}"

            # Should have pinned MCP resource tools
            assert f"{prefix}mcp_list_resources" in tool_names
            assert f"{prefix}mcp_read_resource" in tool_names

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
                        "operationId": "issueListIssues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/admin/orgs": {
                    "get": {
                        "operationId": "adminGetAllOrgs",
                        "summary": "List all organizations",
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

            prefix = config.tool_prefix or ""

            # Should have synthetic tools
            assert f"{prefix}search_tools" in tool_names
            assert f"{prefix}call_tool" in tool_names
            # Should have pinned MCP resource tools
            assert f"{prefix}mcp_list_resources" in tool_names
            assert f"{prefix}mcp_read_resource" in tool_names
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
                        "operationId": "issueListIssues",
                        "summary": "List issues in a repository",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "issueCreateIssue",
                        "summary": "Create a new issue",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
                "/repos/{owner}/{repo}/pulls": {
                    "get": {
                        "operationId": "repoListPullRequests",
                        "summary": "List pull requests",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/user/repos": {
                    "get": {
                        "operationId": "userCurrentListRepos",
                        "summary": "List the repos that the authenticated user owns",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)

            prefix = config.tool_prefix or ""

            # NOTE: Not calling list_tools before search to avoid cache priming

            # Search for "repo" - all tools contain "repo"
            search_repo = await mcp.call_tool(f"{prefix}search_tools", {"query": "repo"})
            repo_tools = search_repo.structured_content.get("result", [])
            repo_names = [t["name"] for t in repo_tools if isinstance(t, dict)]

            # Should find repo-related tools that contain the token "repo"
            # At minimum, the list operations should appear
            assert f"{prefix}user_current_list_repos" in repo_names, f"Expected {prefix}user_current_list_repos in {repo_names}"
            assert f"{prefix}repo_list_pull_requests" in repo_names, f"Expected {prefix}repo_list_pull_requests in {repo_names}"

            # Should NOT return synthetic tools
            assert f"{prefix}search_tools" not in repo_names
            assert f"{prefix}call_tool" not in repo_names

            # Additionally, search for "repos" should find list_user_repos
            search_repos = await mcp.call_tool(f"{prefix}search_tools", {"query": "repos"})
            repos_tools = search_repos.structured_content.get("result", [])
            repos_names = [t["name"] for t in repos_tools if isinstance(t, dict)]
            assert f"{prefix}user_current_list_repos" in repos_names, f"Expected {prefix}user_current_list_repos in {repos_names}"

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
                        "operationId": "issueListIssues",
                        "summary": "List issues in a repository",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/repos/{owner}/{repo}/pulls": {
                    "get": {
                        "operationId": "repoListPullRequests",
                        "summary": "List pull requests",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/user/repos": {
                    "get": {
                        "operationId": "userCurrentListRepos",
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

            prefix = config.tool_prefix or ""

            # First, call list_tools to cache the synthetic catalog
            await mcp.list_tools()

            # Now search for "repo" - should still return real tool matches despite cache
            search_repo = await mcp.call_tool(f"{prefix}search_tools", {"query": "repo"})
            repo_tools = search_repo.structured_content.get("result", [])
            repo_names = [t["name"] for t in repo_tools if isinstance(t, dict)]

            # Should find tools containing "repo" (the expected ones from spec)
            assert f"{prefix}repo_list_pull_requests" in repo_names, (
                f"Cache poisoning: expected {prefix}repo_list_pull_requests in {repo_names}"
            )

            # Should NOT return synthetic tools
            assert f"{prefix}search_tools" not in repo_names
            assert f"{prefix}call_tool" not in repo_names

    @pytest.mark.asyncio
    async def test_search_discovers_pull_request_tools_with_various_queries(self):
        """Test that pull request tools are discoverable with various query patterns."""
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
                "/repos/{owner}/{repo}/pulls": {
                    "get": {
                        "operationId": "repoListPullRequests",
                        "summary": "List a repository's pull requests",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "repoCreatePullRequest",
                        "summary": "Create a pull request",
                        "description": "Create a new pull request from a branch.",
                        "responses": {"201": {"description": "Success"}},
                    },
                },
                "/repos/{owner}/{repo}/pulls/{index}": {
                    "get": {
                        "operationId": "repoGetPullRequest",
                        "summary": "Get a pull request",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)

            prefix = config.tool_prefix or ""

            # Test 1: Query "pr" should find pull request tools
            search_pr = await mcp.call_tool(f"{prefix}search_tools", {"query": "pr"})
            pr_tools = search_pr.structured_content.get("result", [])
            pr_names = [t["name"] for t in pr_tools if isinstance(t, dict)]

            assert f"{prefix}repo_create_pull_request" in pr_names, (
                f"Query 'pr' should find {prefix}repo_create_pull_request, got: {pr_names}"
            )
            assert f"{prefix}repo_list_pull_requests" in pr_names, (
                f"Query 'pr' should find {prefix}repo_list_pull_requests, got: {pr_names}"
            )

            # Test 2: Query "pull request" should find pull request tools
            search_pull = await mcp.call_tool(f"{prefix}search_tools", {"query": "pull request"})
            pull_tools = search_pull.structured_content.get("result", [])
            pull_names = [t["name"] for t in pull_tools if isinstance(t, dict)]

            assert f"{prefix}repo_create_pull_request" in pull_names, (
                f"Query 'pull request' should find {prefix}repo_create_pull_request, got: {pull_names}"
            )

            # Test 3: Query "create pr" should find repo_create_pull_request
            search_create_pr = await mcp.call_tool(f"{prefix}search_tools", {"query": "create pr"})
            create_pr_tools = search_create_pr.structured_content.get("result", [])
            create_pr_names = [t["name"] for t in create_pr_tools if isinstance(t, dict)]

            assert f"{prefix}repo_create_pull_request" in create_pr_names, (
                f"Query 'create pr' should find {prefix}repo_create_pull_request, got: {create_pr_names}"
            )

            # Test 4: Query "pull request create" should find the tool
            search_pr_create = await mcp.call_tool(f"{prefix}search_tools", {"query": "pull request create"})
            pr_create_tools = search_pr_create.structured_content.get("result", [])
            pr_create_names = [t["name"] for t in pr_create_tools if isinstance(t, dict)]

            assert f"{prefix}repo_create_pull_request" in pr_create_names, (
                f"Query 'pull request create' should find {prefix}repo_create_pull_request, got: {pr_create_names}"
            )

    @pytest.mark.asyncio
    async def test_search_discovers_issue_tools_with_various_queries(self):
        """Test that issue tools are discoverable with various query patterns."""
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
                        "operationId": "issueListIssues",
                        "summary": "List repository issues",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "issueCreateIssue",
                        "summary": "Create an issue",
                        "description": "Create a new issue in a repository.",
                        "responses": {"201": {"description": "Success"}},
                    },
                },
            },
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)

            prefix = config.tool_prefix or ""

            # Test 1: Query "issue" should find issue tools
            search_issue = await mcp.call_tool(f"{prefix}search_tools", {"query": "issue"})
            issue_tools = search_issue.structured_content.get("result", [])
            issue_names = [t["name"] for t in issue_tools if isinstance(t, dict)]

            assert f"{prefix}issue_create_issue" in issue_names, (
                f"Query 'issue' should find {prefix}issue_create_issue, got: {issue_names}"
            )
            assert f"{prefix}issue_list_issues" in issue_names, (
                f"Query 'issue' should find {prefix}issue_list_issues, got: {issue_names}"
            )

            # Test 2: Query "create issue" should find create_issue
            search_create = await mcp.call_tool(f"{prefix}search_tools", {"query": "create issue"})
            create_tools = search_create.structured_content.get("result", [])
            create_names = [t["name"] for t in create_tools if isinstance(t, dict)]

            assert f"{prefix}issue_create_issue" in create_names, (
                f"Query 'create issue' should find {prefix}issue_create_issue, got: {create_names}"
            )
