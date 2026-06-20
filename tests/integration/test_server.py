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

            retrieved = await mcp.get_tool("gitea_search")
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

            result = await mcp.read_resource("gitea://server/info")
            assert len(result.contents) > 0
            text = result.contents[0].content
            assert "Server Information" in text
            assert "Gitea Test API" in text
            assert "9.9.9" in text

    @pytest.mark.asyncio
    async def test_read_version(self):
        """Read gitea://version returns server version."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/version").respond(200, json={"version": "1.99.0"})
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://version")
            assert "1.99.0" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_user(self):
        """Read gitea://users/{username} returns formatted user."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/users/alice").respond(
                200, json={
                    "login": "alice", "full_name": "Alice",
                    "html_url": "https://git.example.com/alice",
                    "public_repos": 5, "followers_count": 10, "following_count": 3,
                    "created_at": "2023-01-01T00:00:00Z",
                    "bio": "Developer", "location": "Earth", "website": "",
                }
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://users/alice")
            assert "alice" in result.contents[0].content
            assert "Alice" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_repository(self):
        """Read gitea://repos/{owner}/{repo} returns formatted repo."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/repos/owner/repo").respond(
                200, json={
                    "full_name": "owner/repo", "description": "A test repo",
                    "default_branch": "main", "html_url": "https://git.example.com/owner/repo",
                    "owner": {"login": "owner", "id": 1},
                    "stargazers_count": 5, "forks_count": 2, "open_issues_count": 1,
                    "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-06-01T00:00:00Z",
                }
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo")
            assert "owner/repo" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_releases(self):
        """Read gitea://repos/{owner}/{repo}/releases returns formatted releases."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/repos/owner/repo/releases").respond(
                200,
                json=[
                    {"tag_name": "v1.0", "name": "First", "draft": False, "prerelease": False,
                     "created_at": "2024-01-01T00:00:00Z", "published_at": "2024-01-02T00:00:00Z",
                     "body": "Initial release", "author": {"login": "dev", "id": 1, "html_url": "https://git.example.com/dev"}},
                ],
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo/releases")
            assert "v1.0" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_readme(self):
        """Read gitea://repos/{owner}/{repo}/readme returns README content."""
        import base64

        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}
        content = "# Hello"
        encoded = base64.b64encode(content.encode()).decode()

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/repos/owner/repo/contents/README.md").respond(
                200, json={"content": encoded, "encoding": "base64"}
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo/readme")
            assert "Hello" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_issues(self):
        """Read gitea://repos/{owner}/{repo}/issues returns formatted issues."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get(
                "https://git.example.com/api/v1/repos/owner/repo/issues",
                params={"state": "open"},
            ).respond(
                200,
                json=[
                    {"number": 1, "title": "Bug", "state": "open", "user": {"login": "dev"},
                     "created_at": "2024-01-01T00:00:00Z", "comments": 0, "labels": [],
                     "html_url": "https://example.com/issue/1"},
                ],
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://repos/owner/repo/issues?state=open")
            assert "Bug" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_token_scopes(self):
        """Read gitea://token/scopes returns token scopes."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test-token-prefix_last8",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/user").respond(200, json={"login": "dev2"})
            mock.get("https://git.example.com/api/v1/users/dev2/tokens").respond(
                200,
                json=[
                    {"id": 1, "name": "test", "token_last_eight": "ix_last8", "scopes": ["read:repo", "write:issue"]},
                ],
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://token/scopes")
            assert "read:repo" in result.contents[0].content
            assert "write:issue" in result.contents[0].content

    @pytest.mark.asyncio
    async def test_read_token_scopes_no_match(self):
        """Read gitea://token/scopes returns null when token doesn't match."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="unknown-token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/user").respond(200, json={"login": "dev2"})
            mock.get("https://git.example.com/api/v1/users/dev2/tokens").respond(
                200,
                json=[
                    {"id": 1, "name": "t1", "token_last_eight": "ffffffff", "scopes": ["sudo"]},
                ],
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://token/scopes")
            import json
            data = json.loads(result.contents[0].content)
            assert data["scopes"] is None

    @pytest.mark.asyncio
    async def test_read_organization(self):
        """Read gitea://orgs/{orgname} returns formatted org."""
        config = SimpleConfig(
            url="https://git.example.com",
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)
        swagger_spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}, "definitions": {}}

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=swagger_spec)
            mock.get("https://git.example.com/api/v1/orgs/myorg").respond(
                200, json={
                    "login": "myorg", "full_name": "My Org",
                    "html_url": "https://git.example.com/myorg",
                    "type": "Organization",
                    "public_repos": 10, "followers_count": 0, "following_count": 0,
                    "created_at": "2022-01-01T00:00:00Z",
                    "bio": "", "location": "", "website": "",
                }
            )
            mcp = await create_mcp_server(gitea_client)
            result = await mcp.read_resource("gitea://orgs/myorg")
            assert "myorg" in result.contents[0].content


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


class TestServerEdgeCases:
    """Tests for server edge cases and error paths."""

    @pytest.mark.asyncio
    async def test_load_instructions_fallback(self):
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

    async def test_apply_permission_filter_exception_handled(self):
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
    async def test_permission_filter_exception_logged(self):
        """Exception in fetch_token_scopes is caught by _apply_permission_filter."""
        from unittest.mock import AsyncMock, MagicMock

        from gitea_mcp_server.server import _apply_permission_filter

        mcp = MagicMock()
        gitea_client = AsyncMock()
        gitea_client.request = AsyncMock(side_effect=Exception("API failure"))
        gitea_client.config.token = "test-token"
        config = MagicMock()
        config.tool_filtering_enabled = True
        config.token = "test-token"

        await _apply_permission_filter(mcp, gitea_client, config)
        # Should not raise - exception is caught and logged

    @pytest.mark.asyncio
    async def test_permission_filter_disabled_returns_early(self):
        """_apply_permission_filter returns early when filtering is disabled."""
        from unittest.mock import MagicMock

        from gitea_mcp_server.server import _apply_permission_filter

        mcp = MagicMock()
        config = MagicMock()
        config.tool_filtering_enabled = False

        await _apply_permission_filter(mcp, None, config)
        # No exception - early return

    @pytest.mark.asyncio
    async def test_spec_loading_error_propagates(self):
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
    async def test_build_server_instructions_without_manifest(self):
        """_build_server_instructions works when manifest is empty."""
        from gitea_mcp_server.docs_tools import DocManager
        from gitea_mcp_server.server import _build_server_instructions, load_instructions

        dm = DocManager.__new__(DocManager)
        dm._guides = []
        result = _build_server_instructions(dm)
        assert "Gitea MCP Server" in result

    @pytest.mark.asyncio
    async def test_setup_tool_exclusions_noop_when_no_config(self):
        """_setup_tool_exclusions is a no-op when no exclude config is set."""
        from unittest.mock import MagicMock, patch

        from gitea_mcp_server.server import _setup_tool_exclusions

        mcp = MagicMock()
        config = SimpleConfig(exclude_config_path=None)

        with patch(
            "gitea_mcp_server.server.load_exclusion_config",
            return_value={"exclude": [], "include": []},
        ) as mock_load:
            _setup_tool_exclusions(mcp, config)
            mock_load.assert_called_once()
            mcp.add_transform.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_tool_exclusions_with_config(self):
        """_setup_tool_exclusions adds transform when exclusion config exists."""
        from unittest.mock import MagicMock, patch

        from gitea_mcp_server.server import _setup_tool_exclusions

        mcp = MagicMock()
        config = SimpleConfig(exclude_config_path="/fake/path.yaml")

        with patch(
            "gitea_mcp_server.server.load_exclusion_config",
            return_value={"exclude": ["admin_*"], "include": []},
        ):
            _setup_tool_exclusions(mcp, config)
            mcp.add_transform.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_tool_discovery_with_lazy_loading(self):
        """_setup_tool_discovery adds search + namespace transforms when lazy loading enabled."""
        from unittest.mock import MagicMock, patch

        from gitea_mcp_server.docs_tools import DocManager
        from gitea_mcp_server.server import _setup_tool_discovery

        mcp = MagicMock()
        config = SimpleConfig(enable_lazy_loading=True)
        dm = DocManager.__new__(DocManager)
        dm._guides = []
        dm._search_texts = []

        with patch(
            "gitea_mcp_server.server.register_synthetic_tools"
        ) as mock_register:
            _setup_tool_discovery(mcp, config, dm)
            # add_transform called for search + namespace
            assert mcp.add_transform.call_count == 2
            mock_register.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_tool_discovery_without_lazy_loading(self):
        """_setup_tool_discovery skips search transform when lazy loading disabled."""
        from unittest.mock import MagicMock, patch

        from gitea_mcp_server.docs_tools import DocManager
        from gitea_mcp_server.server import _setup_tool_discovery

        mcp = MagicMock()
        config = SimpleConfig(enable_lazy_loading=False)
        dm = DocManager.__new__(DocManager)
        dm._guides = []

        with patch(
            "gitea_mcp_server.server.register_synthetic_tools"
        ) as mock_register:
            _setup_tool_discovery(mcp, config, dm)
            mock_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_main_async_config_error(self):
        """main_async handles config initialization errors gracefully."""
        from unittest.mock import patch

        with patch("gitea_mcp_server.server.Config.get", side_effect=Exception("Config init failed")):
            with pytest.raises(SystemExit) as exc:
                from gitea_mcp_server.server import main_async
                await main_async()
            assert exc.value.code == 1

    @pytest.mark.asyncio
    async def test_mcp_disable_hides_tools_from_listing(self):
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

        # Before disable — both tools visible
        tools_before = await server.list_tools()
        names_before = {t.name for t in tools_before}
        assert "public_tool" in names_before
        assert "secret_tool" in names_before

        # Disable secret_tool
        server.disable(keys={"tool:secret_tool@"})

        # After disable — secret_tool hidden
        tools_after = await server.list_tools()
        names_after = {t.name for t in tools_after}
        assert "public_tool" in names_after
        assert "secret_tool" not in names_after

    @pytest.mark.asyncio
    async def test_mcp_disable_raises_not_found_on_call(self):
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
    async def test_mcp_disable_hides_resources(self):
        """mcp.disable() with resource keys hides resources from listing."""
        from fastmcp import FastMCP

        server = FastMCP("Test")

        @server.resource("data://public")
        def public_resource() -> str:
            return "public"

        @server.resource("data://secret")
        def secret_resource() -> str:
            return "secret"

        # Before disable — both resources visible
        resources_before = await server.list_resources()
        uris_before = {str(r.uri) for r in resources_before}
        assert "data://public" in uris_before
        assert "data://secret" in uris_before

        # Disable secret resource
        server.disable(keys={"resource:data://secret@"})

        # After disable — secret resource hidden
        resources_after = await server.list_resources()
        uris_after = {str(r.uri) for r in resources_after}
        assert "data://public" in uris_after
        assert "data://secret" not in uris_after

    @pytest.mark.asyncio
    async def test_mcp_disable_hides_resource_templates(self):
        """mcp.disable() with template keys hides templates from listing."""
        from fastmcp import FastMCP
        from fastmcp.resources import ResourceTemplate

        server = FastMCP("Test")

        @server.resource("data://{item}")
        def dynamic_resource(item: str) -> str:
            return f"data for {item}"

        # Before disable — template visible
        templates_before = await server.list_resource_templates()
        uris_before = {t.uri_template for t in templates_before}
        assert "data://{item}" in uris_before

        # Disable template
        server.disable(keys={"template:data://{item}@"})

        # After disable — template hidden
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

        def _run_on_current_loop(coro, *args, **kwargs):
            nonlocal task
            task = asyncio.ensure_future(coro)
            return task

        with patch("gitea_mcp_server.server.main_async") as mock_main_async:
            with patch.object(asyncio, "run", _run_on_current_loop):
                from gitea_mcp_server.server import main
                main()
                if task is not None:
                    await task
                mock_main_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_async_create_server_exception_exits(self):
        """main_async exits with code 1 when create_mcp_server fails."""
        from unittest.mock import patch, AsyncMock, MagicMock

        with patch("gitea_mcp_server.server.Config.get") as mock_config:
            mock_config.return_value = MagicMock(log_level="INFO", log_format="text")
            with patch("gitea_mcp_server.server.create_mcp_server", side_effect=Exception("boom")):
                with patch("gitea_mcp_server.server.GiteaClient") as mock_client:
                    mock_client.return_value.close = AsyncMock()
                    with pytest.raises(SystemExit) as exc:
                        from gitea_mcp_server.server import main_async
                        await main_async()
                    assert exc.value.code == 1
                    mock_client.return_value.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_main_async_stdio_transport(self):
        """main_async with stdio transport calls run_stdio_async."""
        from unittest.mock import patch, AsyncMock, MagicMock

        mock_mcp = AsyncMock()
        mock_mcp.run_stdio_async = AsyncMock()

        with patch("gitea_mcp_server.server.Config.get") as mock_config:
            cfg = MagicMock(log_level="INFO", log_format="text", transport_type="stdio")
            mock_config.return_value = cfg
            with patch("gitea_mcp_server.server.create_mcp_server", return_value=mock_mcp):
                with patch("gitea_mcp_server.server.GiteaClient"):
                    from gitea_mcp_server.server import main_async
                    await main_async()
                    mock_mcp.run_stdio_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_async_keyboard_interrupt_handled(self):
        """main_async handles KeyboardInterrupt gracefully."""
        from unittest.mock import patch, AsyncMock, MagicMock

        mock_mcp = AsyncMock()
        mock_mcp.run_stdio_async = AsyncMock(side_effect=KeyboardInterrupt)

        with patch("gitea_mcp_server.server.Config.get") as mock_config:
            cfg = MagicMock(log_level="INFO", log_format="text", transport_type="stdio")
            mock_config.return_value = cfg
            with patch("gitea_mcp_server.server.create_mcp_server", return_value=mock_mcp):
                with patch("gitea_mcp_server.server.GiteaClient"):
                    from gitea_mcp_server.server import main_async
                    await main_async()  # Should not raise

    @pytest.mark.asyncio
    async def test_create_mcp_server_generic_exception_wrapped(self):
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
        with patch("gitea_mcp_server.server.load_and_convert_spec", side_effect=ValueError("bad spec")):
            with pytest.raises(SpecError, match="Failed to load or convert OpenAPI spec"):
                await create_mcp_server(gitea_client)
