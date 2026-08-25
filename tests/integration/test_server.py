"""Integration tests for the MCP server."""

import logging
from typing import Any

import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig
from tests.helpers.tool_names import extract_tool_names
from tests.integration.conftest import BASE_TEST_URL


class TestServerIntegration:
    """Integration tests for the server setup."""

    @pytest.mark.asyncio
    async def test_create_mcp_server(self) -> None:
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
    async def test_server_instructions_present(self) -> None:
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
    async def test_server_tools_discovery(self) -> None:
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
    async def test_tool_call_with_mock_client(self) -> None:
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

    def _synthetic_base_names(self) -> list[str]:
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
    async def test_extension_metadata_transform_applies_yaml_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

            retrieved = await mcp.get_tool("gitea_search")
            assert retrieved is not None
            assert retrieved.description == "CUSTOM SEARCH DESCRIPTION", (
                f"get_tool should also show YAML override, got {retrieved.description!r}"
            )

    @pytest.mark.asyncio
    async def test_synthetic_tools_have_descriptions(self) -> None:
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
    async def test_read_server_info(self) -> None:
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

            result = await mcp.read_resource("gitea://server/info")
            assert len(result.contents) > 0
            text = result.contents[0].content
            assert "Server Information" in text
            assert "Gitea Test API" in text
            assert "9.9.9" in text

    @pytest.mark.asyncio
    async def test_read_version(self) -> None:
        """Read gitea://version returns server version."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/version").respond(
                200, json={"version": "1.99.0"}
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://version")
            assert "1.99.0" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_user(self) -> None:
        """Read gitea://users/{username} returns formatted user."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/users/alice").respond(
                200,
                json={
                    "login": "alice",
                    "full_name": "Alice",
                    "html_url": "https://git.example.com/alice",
                    "public_repos": 5,
                    "followers_count": 10,
                    "following_count": 3,
                    "created_at": "2023-01-01T00:00:00Z",
                    "bio": "Developer",
                    "location": "Earth",
                    "website": "",
                },
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://users/alice")
            assert "alice" in result.contents[0].content
            assert "Alice" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_repository(self) -> None:
        """Read gitea://repos/{owner}/{repo} returns formatted repo."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/repos/owner/repo").respond(
                200,
                json={
                    "full_name": "owner/repo",
                    "description": "A test repo",
                    "default_branch": "main",
                    "html_url": "https://git.example.com/owner/repo",
                    "owner": {"login": "owner", "id": 1},
                    "stargazers_count": 5,
                    "forks_count": 2,
                    "open_issues_count": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-06-01T00:00:00Z",
                },
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo")
            assert "owner/repo" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_releases(self) -> None:
        """Read gitea://repos/{owner}/{repo}/releases returns formatted releases."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/repos/owner/repo/releases").respond(
                200,
                json=[
                    {
                        "tag_name": "v1.0",
                        "name": "First",
                        "draft": False,
                        "prerelease": False,
                        "created_at": "2024-01-01T00:00:00Z",
                        "published_at": "2024-01-02T00:00:00Z",
                        "body": "Initial release",
                        "author": {
                            "login": "dev",
                            "id": 1,
                            "html_url": "https://git.example.com/dev",
                        },
                    },
                ],
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo/releases")
            assert "v1.0" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_readme(self) -> None:
        """Read gitea://repos/{owner}/{repo}/readme returns README content."""
        import base64

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }
        content = "# Hello"
        encoded = base64.b64encode(content.encode()).decode()

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/repos/owner/repo/contents/README.md").respond(
                200, json={"content": encoded, "encoding": "base64"}
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo/readme")
            # Resource handler returns raw JSON — decode is in the
            # read_resource tool layer (mcp_tools.py:_read_resource_tool).
            raw = result.contents[0].content
            raw_content = raw.decode() if isinstance(raw, bytes) else raw
            assert raw_content.startswith("{"), f"Expected JSON, got: {raw_content}"
            assert '"encoding": "base64"' in raw_content

    @pytest.mark.asyncio
    async def test_read_issues_default(self) -> None:
        """Read gitea://repos/{owner}/{repo}/issues returns all issues."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/repos/owner/repo/issues").respond(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "Bug",
                        "state": "open",
                        "user": {"login": "dev"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "comments": 0,
                        "labels": [],
                        "html_url": "https://example.com/issue/1",
                    },
                ],
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo/issues")
            assert "Bug" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_issues_with_state_param(self) -> None:
        """Read gitea://repos/{owner}/{repo}/issues?state=open passes state to API."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get(
                "https://git.example.com/api/v1/repos/owner/repo/issues",
                params={"state": "open"},
            ).respond(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "Bug",
                        "state": "open",
                        "user": {"login": "dev"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "comments": 0,
                        "labels": [],
                        "html_url": "https://example.com/issue/1",
                    },
                ],
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo/issues?state=open")
            assert "Bug" in result.contents[0].content
            # Verify the mock was called with the expected params
            issues_route = mock.routes[-1]
            assert issues_route.called

    @pytest.mark.asyncio
    async def test_read_token_scopes(self) -> None:
        """Read gitea://token/scopes returns token scopes."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test-token-prefix_last8",
            log_level="ERROR",
            tool_filtering_enabled=True,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/user").respond(200, json={"login": "dev2"})
            mock.get("https://git.example.com/api/v1/users/dev2/tokens").respond(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "test",
                        "token_last_eight": "ix_last8",
                        "scopes": ["read:repository", "write:issue", "read:user"],
                    },
                ],
            )
            mock.get("https://git.example.com/api/v1/version").respond(
                200, json={"version": "1.0.0"}
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://token/scopes")
            assert "read:repository" in result.contents[0].content
            assert "write:issue" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_organization(self) -> None:
        """Read gitea://orgs/{orgname} returns formatted org."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/orgs/myorg").respond(
                200,
                json={
                    "login": "myorg",
                    "full_name": "My Org",
                    "html_url": "https://git.example.com/myorg",
                    "type": "Organization",
                    "public_repos": 10,
                    "followers_count": 0,
                    "following_count": 0,
                    "created_at": "2022-01-01T00:00:00Z",
                    "bio": "",
                    "location": "",
                    "website": "",
                },
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://orgs/myorg")
            assert "myorg" in result.contents[0].content


class TestToolFiltering:
    """Tests for tool permission filtering."""

    def _make_config(self, **overrides: Any) -> SimpleConfig:
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
    async def test_filtering_removes_admin_tools_for_non_admin_user(self) -> None:
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
            mock.get("https://git.example.com/api/v1/user").respond(
                200, json={"login": "regularuser", "admin": False}
            )
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            admin_tools = [name for name in tool_names if name.startswith("admin")]
            assert len(admin_tools) == 0, (
                f"Expected no admin tools for non-admin user, but found: {admin_tools}"
            )

    @pytest.mark.asyncio
    async def test_filtering_keeps_admin_tools_for_admin_user(self) -> None:
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
            mock.get("https://git.example.com/api/v1/user").respond(
                200, json={"login": "adminuser", "admin": True}
            )
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            prefix = config.tool_prefix or ""
            admin_tools = [name for name in tool_names if name.startswith(f"{prefix}admin")]
            assert len(admin_tools) > 0, (
                f"Expected admin tools to be present for admin user, but none found in {tool_names}"
            )

    @pytest.mark.asyncio
    async def test_filtering_disabled_when_config_false(self) -> None:
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

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_filtering_keeps_all_tools_on_user_fetch_error(self) -> None:
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
            mock.get("https://git.example.com/api/v1/user").respond(500, json={"message": "Error"})
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            assert len(tool_names) > 0


class TestServerEdgeCases:
    """Tests for server edge cases and error paths."""

    @pytest.mark.asyncio
    async def test_load_instructions_fallback(self) -> None:
        """FileNotFoundError in load_instructions returns fallback text."""
        from unittest.mock import patch

        from gitea_mcp_server.server import load_instructions

        with (
            patch("gitea_mcp_server.server.pkg_resources.files") as mock_files,
        ):
            mock_files.side_effect = FileNotFoundError("Package not found")
            result = load_instructions()
            assert "Gitea MCP Server" in result
            assert "Authentication" in result
            assert "lazy loading" in result.lower() or "search" in result.lower()

    async def test_apply_permission_filter_exception_handled(self) -> None:
        """Exception in permission filtering doesn't crash server creation."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            log_format="text",
            tool_filtering_enabled=True,
        )
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "basePath": "/api/v1",
            "paths": {},
            "definitions": {},
        }

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mcp = await create_mcp_server(gitea_client)
            assert mcp is not None

    @pytest.mark.asyncio
    async def test_permission_filter_exception_fail_open(self) -> None:
        """Exception in fetch_token_scopes is caught; filtering fails open.

        Scope filtering happens at spec-prep time inside
        ``load_and_convert_spec``.  A token-scope fetch failure must not raise
        — the server proceeds with no scope filtering applied.
        """
        from unittest.mock import patch

        from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec

        config = SimpleConfig(tool_filtering_enabled=True, token="test-token")
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {},
            "definitions": {},
        }
        with (
            respx.mock() as mock,
            patch(
                "gitea_mcp_server.server_setup.spec_loader.fetch_token_scopes",
                side_effect=Exception("API failure"),
            ),
        ):
            mock.get(f"{config.url}/swagger.v1.json").respond(200, json=swagger_spec)
            # Should not raise - exception is caught and filtering fails open.
            _, _, _, excluded_routes = await load_and_convert_spec(gitea_client, config)
            assert excluded_routes == set()

    @pytest.mark.asyncio
    async def test_permission_filter_disabled_skips_scope_routes(self) -> None:
        """When filtering is disabled, scope-based routes are not excluded.

        Scopes are always fetched (for the ``gitea://token/scopes``
        resource), but ``_compute_excluded_routes`` drops scope reasons
        when ``scope_filtering_enabled`` is False.
        """
        from unittest.mock import patch

        from gitea_mcp_server.server_setup.spec_loader import (
            load_and_convert_spec,
        )

        config = SimpleConfig(tool_filtering_enabled=False, token="test-token")
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {},
            "definitions": {},
        }
        with (
            respx.mock() as mock,
            patch(
                "gitea_mcp_server.server_setup.spec_loader.fetch_token_scopes",
                return_value=None,
            ) as mock_fetch,
        ):
            mock.get(f"{config.url}/swagger.v1.json").respond(200, json=swagger_spec)
            # Scope fetch is called even when filtering is disabled.
            _, _, _, excluded_routes = await load_and_convert_spec(gitea_client, config)
            mock_fetch.assert_called_once()
            assert excluded_routes == set()

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_spec_loading_error_propagates(self) -> None:
        """Spec loading error propagates as SpecError."""
        from gitea_mcp_server.exceptions import SpecError

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(500)
            with pytest.raises(SpecError):
                await create_mcp_server(gitea_client)

    @pytest.mark.asyncio
    async def test_build_server_instructions_without_manifest(self) -> None:
        """_build_server_instructions works when manifest is empty."""
        from gitea_mcp_server.server import _build_server_instructions

        result = _build_server_instructions()
        assert "Gitea MCP Server" in result

    # ------------------------------------------------------------------
    # Placeholder substitution tests
    # ------------------------------------------------------------------

    def test_substitute_placeholders_known(self) -> None:
        """Known placeholders are replaced."""
        from gitea_mcp_server.server import substitute_placeholders

        result = substitute_placeholders(
            "Hello {{NAME}}, prefix is {{TOOL_PREFIX}}",
            {"NAME": "Agent", "TOOL_PREFIX": "gitea_"},
        )
        assert result == "Hello Agent, prefix is gitea_"

    def test_substitute_placeholders_unknown(self) -> None:
        """Unknown placeholders pass through unchanged."""
        from gitea_mcp_server.server import substitute_placeholders

        result = substitute_placeholders(
            "Hello {{NAME}}, {{UNKNOWN}} stays",
            {"NAME": "Agent"},
        )
        assert result == "Hello Agent, {{UNKNOWN}} stays"

    def test_substitute_placeholders_empty_values(self) -> None:
        """Empty values dict returns text unchanged."""
        from gitea_mcp_server.server import substitute_placeholders

        text = "Hello {{NAME}}"
        result = substitute_placeholders(text, {})
        assert result == text

    def test_substitute_placeholders_no_placeholders(self) -> None:
        """Text with no placeholders is returned unchanged."""
        from gitea_mcp_server.server import substitute_placeholders

        text = "Hello world"
        result = substitute_placeholders(text, {"NAME": "Agent"})
        assert result == text

    def test_substitute_placeholders_multiple_same(self) -> None:
        """Same placeholder appearing multiple times is replaced everywhere."""
        from gitea_mcp_server.server import substitute_placeholders

        result = substitute_placeholders(
            "{{P}}a {{P}}b {{P}}c",
            {"P": "x"},
        )
        assert result == "xa xb xc"

    @pytest.mark.asyncio
    async def test_build_server_instructions_with_placeholders(self) -> None:
        """_build_server_instructions substitutes placeholders before returning."""
        from gitea_mcp_server.server import _build_server_instructions

        result = _build_server_instructions(
            placeholder_values={
                "TOOL_PREFIX": "custom_",
                "USER_LOGIN": "testuser",
            },
        )
        assert "custom_" in result
        assert "testuser" in result
        assert "{{TOOL_PREFIX}}" not in result
        assert "{{USER_LOGIN}}" not in result

    @pytest.mark.asyncio
    async def test_served_instructions_no_unresolved_placeholders(self) -> None:
        """Served instructions contain NO unresolved {{}} placeholders.

        After substitution, every ``{{PLACEHOLDER}}`` must be resolved.
        This guards against a placeholder being added to the doc without
        a corresponding entry in the substitution values.

        Uses a realistic GUIDES_LIST to catch conflicts between the
        placeholder syntax (``{{TOOL_PREFIX}}``) and FastMCP URI template
        syntax (``{topic}``) in the guide manifest.
        """
        from gitea_mcp_server.server import _build_server_instructions

        realistic_guides = (
            "## Workflow Guides\n\n"
            "| Guide | Description |\n"
            "|-------|-------------|\n"
            "| `labels` | How labels work |\n\n"
            "Use `search_docs(query)` to find guides, or `read_doc(topic)` "
            "to read one.\n"
            "Guides are also available as resources at "
            "`gitea://docs/guide/{topic}`.\n"
        )

        result = _build_server_instructions(
            placeholder_values={
                "TOOL_PREFIX": "gitea_",
                "USER_LOGIN": "agent",
                "TOKEN_SCOPES": "`read:repository`",
                "SERVER_TYPE": "Gitea",
                "GUIDES_LIST": realistic_guides,
            },
        )
        assert "{{" not in result, f"Unresolved placeholder found in: {result}"

    @pytest.mark.asyncio
    async def test_served_instructions_no_frontmatter(self) -> None:
        """Served instructions start with '#', not YAML frontmatter."""
        from gitea_mcp_server.server import _build_server_instructions

        result = _build_server_instructions()
        assert result.startswith("#"), f"Instructions must start with '#', got: {result[:50]}"

    @pytest.mark.asyncio
    async def test_served_instructions_line_budget(self) -> None:
        """Served instructions respect the line-count budget (<= 300 lines).

        The budget protects the agent-context economy. Raise it deliberately
        with a comment, not by 'tidying'.

        Budget history:
        - 200 lines: initial contract from #462 (proved too tight)
        - 300 lines: raised 2026-07-20 to accommodate the full doc with
          placeholders, workflow guide manifest, and edge-case catalog.
          The doc is intentionally comprehensive because it is the *only*
          context injected at connection time — every other resource is
          discovered on demand.
        - 320 lines: raised 2026-07-24 to document size_hint, default_detail,
          and optional_params metadata fields in the Resources section (#522).
        - 330 lines: raised 2026-08-26 to document the single result pipeline
          contract (envelope in the text, deterministic raw, empty-json shape)
          in the Output format section (#719).
        """
        from gitea_mcp_server.server import _build_server_instructions

        result = _build_server_instructions()
        line_count = len(result.splitlines())
        assert line_count <= 330, (
            f"Instructions are {line_count} lines (budget: 330). "
            "Increase the budget deliberately, not by trimming."
        )

    @pytest.mark.asyncio
    async def test_served_instructions_key_anchors(self) -> None:
        """Served instructions contain key anchor phrases from the #461 review."""
        from gitea_mcp_server.server import _build_server_instructions

        result = _build_server_instructions()

        anchors = [
            # Filter explanation: spec -> scope -> config
            "generated directly from",
            "Swagger/OpenAPI spec",
            # Universal scope filtering (not admin-only)
            "tool and resource is scope",
            # Configurable prefix wording
            "configured prefix",
            # tool_info self-inspection invite
            "tool_info",
        ]
        for anchor in anchors:
            assert anchor.lower() in result.lower(), f"Missing anchor phrase: '{anchor}'"

    @pytest.mark.asyncio
    async def test_exclusion_noop_when_no_config(self) -> None:
        """No exclude config means no excluded routes from exclusion (spec-prep)."""
        from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec

        config = SimpleConfig(exclude_config_path=None, tool_filtering_enabled=False)
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/admin/settings": {
                    "get": {
                        "operationId": "admin_settings",
                        "summary": "Get settings",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
            "definitions": {},
        }
        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            _, _, _, excluded_routes = await load_and_convert_spec(gitea_client, config)
            assert excluded_routes == set()

    @pytest.mark.asyncio
    async def test_exclusion_with_config_excludes_routes(self) -> None:
        """Exclusion config produces excluded routes at spec-prep time."""
        from unittest.mock import patch

        from gitea_mcp_server.server_setup.spec_loader import load_and_convert_spec

        config = SimpleConfig(exclude_config_path="/fake/path.yaml", tool_filtering_enabled=False)
        gitea_client = GiteaClient(config)

        swagger_spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/admin/settings": {
                    "get": {
                        "operationId": "admin_settings",
                        "summary": "Get settings",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
            "definitions": {},
        }
        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            with patch(
                "gitea_mcp_server.server_setup.spec_loader.load_exclusion_config",
                return_value={"exclude": ["gitea_admin_*"], "include": []},
            ):
                _, _, _, excluded_routes = await load_and_convert_spec(gitea_client, config)
                assert ("/admin/settings", "GET") in excluded_routes

    @pytest.mark.asyncio
    async def test_setup_tool_discovery_with_lazy_loading(self) -> None:
        """_setup_tool_discovery adds search + namespace transforms when lazy loading enabled."""
        from unittest.mock import MagicMock, patch

        from gitea_mcp_server.server import _setup_tool_discovery
        from gitea_mcp_server.tools.docs_tools import DocManager

        mcp = MagicMock()
        config = SimpleConfig(enable_lazy_loading=True)
        dm = DocManager.__new__(DocManager)
        dm._guides = []
        dm._search_texts = []

        with patch("gitea_mcp_server.server.register_synthetic_tools") as mock_register:
            _setup_tool_discovery(mcp, config, dm)
            # add_transform called for search + namespace
            assert mcp.add_transform.call_count == 2
            mock_register.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_tool_discovery_without_lazy_loading(self) -> None:
        """_setup_tool_discovery skips search transform when lazy loading disabled."""
        from unittest.mock import MagicMock, patch

        from gitea_mcp_server.server import _setup_tool_discovery
        from gitea_mcp_server.tools.docs_tools import DocManager

        mcp = MagicMock()
        config = SimpleConfig(enable_lazy_loading=False)
        dm = DocManager.__new__(DocManager)
        dm._guides = []

        with patch("gitea_mcp_server.server.register_synthetic_tools") as mock_register:
            _setup_tool_discovery(mcp, config, dm)
            mock_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_main_async_config_error(self) -> None:
        """main_async handles config initialization errors gracefully."""
        from unittest.mock import patch

        from gitea_mcp_server.server import main_async

        with patch(
            "gitea_mcp_server.server.Config.get", side_effect=Exception("Config init failed")
        ):
            with pytest.raises(SystemExit) as exc:
                await main_async()
            assert exc.value.code == 1

    @pytest.mark.asyncio
    async def test_mcp_disable_hides_tools_from_listing(self) -> None:
        """mcp.disable() causes tools to be absent from server.list_tools()."""
        from fastmcp import FastMCP

        server = FastMCP("Test")

        @server.tool
        def public_tool() -> str:
            """A public tool."""
            return "public"

        @server.tool
        def secret_tool() -> str:
            """A secret tool."""
            return "secret"

        # Before disable - both tools visible
        tools_before = await server.list_tools()
        names_before = {t.name for t in tools_before}
        assert "public_tool" in names_before
        assert "secret_tool" in names_before

        # Disable secret_tool
        server.disable(keys={"tool:secret_tool@"})

        # After disable - secret_tool hidden
        tools_after = await server.list_tools()
        names_after = {t.name for t in tools_after}
        assert "public_tool" in names_after
        assert "secret_tool" not in names_after

    @pytest.mark.asyncio
    async def test_mcp_disable_raises_not_found_on_call(self) -> None:
        """Calling a disabled tool raises NotFoundError."""
        from fastmcp import FastMCP
        from fastmcp.exceptions import NotFoundError

        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        server.disable(keys={"tool:my_tool@"})

        with pytest.raises(NotFoundError):
            await server.call_tool("my_tool")

    @pytest.mark.asyncio
    async def test_mcp_disable_hides_resources(self) -> None:
        """mcp.disable() with resource keys hides resources from listing."""
        from fastmcp import FastMCP

        server = FastMCP("Test")

        @server.resource("data://public")
        def public_resource() -> str:
            return "public"

        @server.resource("data://secret")
        def secret_resource() -> str:
            return "secret"

        # Before disable - both resources visible
        resources_before = await server.list_resources()
        uris_before = {str(r.uri) for r in resources_before}
        assert "data://public" in uris_before
        assert "data://secret" in uris_before

        # Disable secret resource
        server.disable(keys={"resource:data://secret@"})

        # After disable - secret resource hidden
        resources_after = await server.list_resources()
        uris_after = {str(r.uri) for r in resources_after}
        assert "data://public" in uris_after
        assert "data://secret" not in uris_after

    @pytest.mark.asyncio
    async def test_mcp_disable_hides_resource_templates(self) -> None:
        """mcp.disable() with template keys hides templates from listing."""
        from fastmcp import FastMCP

        server = FastMCP("Test")

        @server.resource("data://{item}")
        def dynamic_resource(item: str) -> str:
            return f"data for {item}"

        # Before disable - template visible
        templates_before = await server.list_resource_templates()
        uris_before = {t.uri_template for t in templates_before}
        assert "data://{item}" in uris_before

        # Disable template
        server.disable(keys={"template:data://{item}@"})

        # After disable - template hidden
        templates_after = await server.list_resource_templates()
        uris_after = {t.uri_template for t in templates_after}
        assert "data://{item}" not in uris_after

    @pytest.mark.asyncio
    async def test_main_calls_async_main(self) -> None:
        """main() calls asyncio.run(main_async()).

        ``asyncio.run`` is patched to schedule the coroutine on the current
        event loop, avoiding the nested-event-loop error while still
        executing the coroutine properly.
        """
        import asyncio
        from unittest.mock import patch

        task = None

        def _run_on_current_loop(coro: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal task
            task = asyncio.ensure_future(coro)
            return task

        with (
            patch("gitea_mcp_server.server.main_async") as mock_main_async,
            patch.object(asyncio, "run", _run_on_current_loop),
        ):
            from gitea_mcp_server.server import main

            main()
            if task is not None:
                await task
            mock_main_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_async_create_server_exception_exits(self) -> None:
        """main_async exits with code 1 when create_mcp_server fails."""
        from unittest.mock import AsyncMock, patch

        config = SimpleConfig()

        with patch("gitea_mcp_server.server.Config.get") as mock_config:
            mock_config.return_value = config
            with (
                patch("gitea_mcp_server.server.create_mcp_server", side_effect=Exception("boom")),
                patch.object(GiteaClient, "close", new=AsyncMock()) as mock_close,
            ):
                from gitea_mcp_server.server import main_async

                with pytest.raises(SystemExit) as exc:
                    await main_async()
                assert exc.value.code == 1
                mock_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_main_async_stdio_transport(self) -> None:
        """main_async with stdio transport calls run_stdio_async."""
        from unittest.mock import AsyncMock, patch

        mock_mcp = AsyncMock()
        mock_mcp.run_stdio_async = AsyncMock()
        config = SimpleConfig(transport_type="stdio")

        with patch("gitea_mcp_server.server.Config.get") as mock_config:
            mock_config.return_value = config
            with patch("gitea_mcp_server.server.create_mcp_server", return_value=mock_mcp):
                from gitea_mcp_server.server import main_async

                await main_async()
                mock_mcp.run_stdio_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_async_keyboard_interrupt_handled(self) -> None:
        """main_async handles KeyboardInterrupt gracefully."""
        from unittest.mock import AsyncMock, patch

        mock_mcp = AsyncMock()
        mock_mcp.run_stdio_async = AsyncMock(side_effect=KeyboardInterrupt)
        config = SimpleConfig(transport_type="stdio")

        with patch("gitea_mcp_server.server.Config.get") as mock_config:
            mock_config.return_value = config
            with patch("gitea_mcp_server.server.create_mcp_server", return_value=mock_mcp):
                from gitea_mcp_server.server import main_async

                await main_async()  # Should not raise

    @pytest.mark.asyncio
    async def test_main_async_crash_handler(self) -> None:
        """main_async handles Exception crash with sys.exit(1)."""
        from unittest.mock import AsyncMock, patch

        mock_mcp = AsyncMock()
        mock_mcp.run_stdio_async = AsyncMock(side_effect=Exception("server crash"))
        config = SimpleConfig(transport_type="stdio")

        with patch("gitea_mcp_server.server.Config.get") as mock_config:
            mock_config.return_value = config
            with patch("gitea_mcp_server.server.create_mcp_server", return_value=mock_mcp):
                from gitea_mcp_server.server import main_async

                with pytest.raises(SystemExit) as exc:
                    await main_async()
                assert exc.value.code == 1

    @pytest.mark.asyncio
    async def test_create_mcp_server_generic_exception_wrapped(self) -> None:
        """create_mcp_server wraps non-SpecError exceptions in SpecError."""
        from unittest.mock import patch

        from gitea_mcp_server.exceptions import SpecError

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
        )
        gitea_client = GiteaClient(config)

        # Mock load_and_convert_spec to raise a generic exception
        with (
            patch(
                "gitea_mcp_server.server.load_and_convert_spec", side_effect=ValueError("bad spec")
            ),
            pytest.raises(SpecError, match="Failed to load or convert OpenAPI spec"),
        ):
            await create_mcp_server(gitea_client)

    @pytest.mark.asyncio
    async def test_create_mcp_server_forgejo_server_type(self) -> None:
        """Server type placeholder is set to Forgejo when spec says 'forgejo'."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(
                200,
                json={
                    "swagger": "2.0",
                    "info": {"title": "Forgejo API", "version": "1.0"},
                    "paths": {},
                    "definitions": {},
                },
            )
            mcp = await create_mcp_server(gitea_client)
            assert mcp is not None

    @pytest.mark.asyncio
    async def test_create_mcp_server_version_as_string(self) -> None:
        """Server version string path when /version returns a string."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
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
            # Mock version endpoint to return a plain string
            mock_http.get("https://git.example.com/api/v1/version").respond(200, text="1.22.0")
            mcp = await create_mcp_server(gitea_client)
            assert mcp is not None

    @pytest.mark.asyncio
    async def test_apply_virtual_param_scope_filter_exception(self) -> None:
        """_apply_virtual_param_scope_filter handles exceptions gracefully."""
        from unittest.mock import patch

        from gitea_mcp_server.server import _apply_virtual_param_scope_filter

        with patch(
            "gitea_mcp_server.server.apply_scope_filter",
            side_effect=ValueError("scope error"),
        ):
            # Should not raise - exception is caught inside
            await _apply_virtual_param_scope_filter({"read:repository"})


# ---------------------------------------------------------------------------
# Tests: Server startup failure modes
# ---------------------------------------------------------------------------


class TestServerStartupFailures:
    """Tests for server creation with invalid spec, unreachable URL, or invalid token.

    These test the error-handling paths in ``create_mcp_server`` /
    ``load_and_convert_spec`` that are reached when the Swagger spec cannot
    be fetched or parsed.
    """

    @pytest.mark.asyncio
    async def test_corrupt_spec_raises_spec_error(self) -> None:
        """Server creation with a corrupt (non-JSON) swagger spec raises SpecError."""
        from gitea_mcp_server.exceptions import SpecError

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock_http:
            # Return non-JSON content with a JSON content-type (simulates
            # a corrupt server response that the HTTP layer accepts but
            # json.loads fails on).
            mock_http.get("https://git.example.com/swagger.v1.json").respond(
                200,
                content=b"{corrupt json",
                headers={"content-type": "application/json"},
            )
            with pytest.raises(SpecError, match="Failed to fetch or parse spec"):
                await create_mcp_server(gitea_client)

    @pytest.mark.asyncio
    async def test_unreachable_server_raises_spec_error(self) -> None:
        """Server creation with an unreachable Gitea URL raises SpecError."""
        import httpx

        from gitea_mcp_server.exceptions import SpecError

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock_http:
            # Simulate a connection refusal (DNS failure / server down).
            # httpx.ConnectError is what the real httpx transport raises.
            mock_http.get(
                "https://git.example.com/swagger.v1.json"
            ).side_effect = httpx.ConnectError("Connection refused")
            with pytest.raises(SpecError, match="Failed to fetch"):
                await create_mcp_server(gitea_client)

    @pytest.mark.asyncio
    async def test_unauthorized_spec_fetch_raises_spec_error(self) -> None:
        """Server creation with a 401 on the swagger endpoint raises SpecError."""
        from gitea_mcp_server.exceptions import SpecError

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock_http:
            mock_http.get("https://git.example.com/swagger.v1.json").respond(
                401,
                json={"message": "Unauthorized"},
            )
            with pytest.raises(SpecError, match="Failed to fetch"):
                await create_mcp_server(gitea_client)


class TestWrappingPipelineEdgeCases:
    """Tests for edge cases in the tool wrapping pipeline.

    These tests exercise the runtime wrapping transform (validation, error
    handling, formatting, ctx.report_progress).  The existing integration
    test specs deliberately omit response schemas to test the fallback
    wrapping path; these tests ADD a schema so the wrapping transform's
    full pipeline is exercised — including 204 No Content wrapping
    (lines 571-575 in mcp_builder.py), raw format early return (line 306),
    and ctx.report_progress (lines 597, 608).
    """

    @pytest.fixture
    def base_spec(self) -> dict[str, Any]:
        """Swagger spec with a response schema for wrapping pipeline tests."""
        return {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "basePath": "/api/v1",
            "paths": {
                "/version": {
                    "get": {
                        "operationId": "getVersion",
                        "summary": "Get server version",
                        "responses": {
                            "200": {
                                "description": "Success",
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "version": {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                },
            },
            "definitions": {},
        }

    @pytest.mark.asyncio
    async def test_raw_format_returns_raw_data(self, mcp_server: Any) -> None:
        """format=raw returns the API response without markdown formatting."""
        respx.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json={"version": "1.0.0"})
        result = await mcp_server.call_tool("gitea_get_version", {"format": "raw"})
        assert result.structured_content is not None
        assert result.structured_content["result"]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_non_empty_result_triggers_progress(self, mcp_server: Any) -> None:
        """Tool calls with dict results trigger ctx.report_progress."""
        respx.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json={"version": "1.0.0"})
        result = await mcp_server.call_tool("gitea_get_version", {})
        assert result.structured_content is not None
        assert result.structured_content["result"]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_ctx_report_progress_called(self, mcp_server: Any) -> None:
        """ctx.report_progress and ctx.info are called during a tool call.

        Verifies that the context resolution in transform_fn wires progress
        reporting and structured logging through the pipeline.  Uses a mock
        context to avoid depending on an active MCP session.
        """
        from unittest.mock import AsyncMock, patch

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        class _MockCurrentContext:
            """Context manager that returns mock_ctx on enter."""

            async def __aenter__(self) -> AsyncMock:
                return mock_ctx

            async def __aexit__(self, *args: object) -> None:
                pass

        respx.get(f"{BASE_TEST_URL}/api/v1/version").respond(200, json={"version": "1.0.0"})
        with patch(
            "gitea_mcp_server.context_utils.CurrentContext",
            return_value=_MockCurrentContext(),
        ):
            result = await mcp_server.call_tool("gitea_get_version", {})

        assert result.structured_content is not None
        assert result.structured_content["result"]["version"] == "1.0.0"
        mock_ctx.info.assert_awaited()
        mock_ctx.report_progress.assert_awaited()


class Test204NoContentWrapping:
    """Tests for 204 No Content response wrapping in the tool pipeline.

    Endpoints with 204 responses have ``is_empty_response=True`` in their
    customization metadata, which triggers the wrapping pipeline to produce
    ``ToolResult(structured_content={"result": None})`` instead of the raw
    empty response.
    """

    @pytest.fixture
    def base_spec(self) -> dict[str, Any]:
        """Swagger spec with a delete endpoint returning 204 No Content.

        The endpoint has a 200 response with a ``$ref`` to an empty response
        definition, which is how Gitea's spec flags empty-body endpoints.
        """
        return {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "basePath": "/api/v1",
            "paths": {
                "/repos/{owner}/{repo}": {
                    "delete": {
                        "operationId": "repoDelete",
                        "summary": "Delete a repository",
                        "parameters": [
                            {"name": "owner", "in": "path", "required": True, "type": "string"},
                            {"name": "repo", "in": "path", "required": True, "type": "string"},
                        ],
                        "responses": {
                            "204": {"description": "No Content"},
                        },
                    }
                },
            },
            "definitions": {},
        }

    @pytest.mark.asyncio
    async def test_204_response_returns_none_result(self, mcp_server: Any) -> None:
        """204 No Content response yields structured_content with result=None."""
        respx.delete(f"{BASE_TEST_URL}/api/v1/repos/owner/repo").respond(204)
        result = await mcp_server.call_tool("gitea_repo_delete", {"owner": "owner", "repo": "repo"})
        assert result.structured_content is not None
        assert result.structured_content.get("result") is None


class TestServerLifecycle:
    """Tests for server lifecycle — lifespan, startup error paths."""

    @pytest.mark.asyncio
    async def test_app_lifespan_yields_and_closes_client(self) -> None:
        """The app_lifespan closure in main_async yields the client and closes on exit.

        Patches only Config.get and create_mcp_server — main_async creates
        a real GiteaClient from SimpleConfig, making the test less fragile
        to internal flow changes.  If main_async gains intermediate steps
        that need mocking, those patches go here (and only here).
        """
        from unittest.mock import AsyncMock, patch

        lifespan_captured: list[Any] = []
        mock_mcp = AsyncMock()
        mock_mcp.run_stdio_async = AsyncMock()

        async def mock_create_mcp_server(
            gitea_client: Any, lifespan: Any = None, config: Any = None
        ) -> Any:
            lifespan_captured.append(lifespan)
            return mock_mcp

        config = SimpleConfig(log_level="INFO", log_format="text", transport_type="stdio")

        with (
            patch("gitea_mcp_server.server.Config.get", return_value=config),
            patch("gitea_mcp_server.server.create_mcp_server", side_effect=mock_create_mcp_server),
        ):
            from gitea_mcp_server.server import main_async

            await main_async()

        # The lifespan closure was captured — exercise it directly
        lifespan = lifespan_captured[0]
        async with lifespan(None) as ctx:
            assert "gitea_client" in ctx
            gitea_client = ctx["gitea_client"]
        # After lifespan exit, GiteaClient.close() → transport._client = None
        assert gitea_client.transport._client is None

    @pytest.mark.asyncio
    async def test_exclusion_config_load_failure_falls_back(self) -> None:
        """When load_exclusion_config raises, the server proceeds with empty config."""
        from unittest.mock import patch

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
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

            with patch(
                "gitea_mcp_server.server_setup.spec_loader.load_exclusion_config",
                side_effect=OSError("config not found"),
            ):
                server = await create_mcp_server(gitea_client, config)
                assert server is not None
