"""Integration tests for the exclusion transform (Zone 4 pattern).

Exercises the full path: YAML config file → env var → server.py → list_tools().
"""

from pathlib import Path

import pytest
import respx

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig, extract_tool_names


SWAGGER_SPEC = {
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
        "/admin/users": {
            "get": {
                "operationId": "admin_get_users",
                "summary": "List admin users",
                "tags": ["admin"],
                "responses": {"200": {"description": "Success"}},
            }
        },
        "/admin/settings": {
            "get": {
                "operationId": "admin_get_settings",
                "summary": "Get admin settings",
                "tags": ["admin"],
                "responses": {"200": {"description": "Success"}},
            }
        },
    },
    "definitions": {},
}


class TestExclusionIntegration:
    """Integration tests for the exclusion transform."""

    @pytest.mark.asyncio
    async def test_exclude_admin_tools(self, tmp_path: Path):
        """Exclude patterns hide matching tools from the server's listing."""
        cfg = tmp_path / "exclude.yaml"
        cfg.write_text("exclude:\n  - gitea_admin_*")
        config = SimpleConfig(exclude_config_path=str(cfg))
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            assert "gitea_get_repo_issues" in tool_names
            assert not any("admin" in t for t in tool_names), (
                f"Expected no admin tools but found: {[t for t in tool_names if 'admin' in t]}"
            )

    @pytest.mark.asyncio
    async def test_include_overrides_exclude(self, tmp_path: Path):
        """Include patterns override exclude, restoring a subset of excluded tools."""
        cfg = tmp_path / "override.yaml"
        cfg.write_text(
            "exclude:\n  - gitea_admin_*\ninclude:\n  - gitea_admin_get_users"
        )
        config = SimpleConfig(exclude_config_path=str(cfg))
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            assert "gitea_admin_get_users" in tool_names, (
                f"Expected gitea_admin_get_users to be present but got: {tool_names}"
            )
            assert "gitea_admin_get_settings" not in tool_names, (
                f"Expected gitea_admin_get_settings to be excluded but got: {tool_names}"
            )

    @pytest.mark.asyncio
    async def test_no_config_file_no_filtering(self):
        """Without a config path, all tools should be present."""
        config = SimpleConfig(exclude_config_path=None)
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            assert "gitea_get_repo_issues" in tool_names
            assert "gitea_admin_get_users" in tool_names

    @pytest.mark.asyncio
    async def test_exclude_star_with_include_whitelist(self, tmp_path: Path):
        """Exclude everything, then whitelist specific tools via include.

        ``*`` must be quoted in YAML to avoid alias parsing.
        """
        cfg = tmp_path / "whitelist.yaml"
        cfg.write_text(
            "exclude:\n  - \"*\"\ninclude:\n  - gitea_admin_get_users"
        )
        config = SimpleConfig(exclude_config_path=str(cfg))
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            # Synthetic discovery tools (search_docs, read_doc, ...) are
            # registered separately from the OpenAPI spec and are never subject
            # to spec-level exclusion, so they remain.  The spec-level filter
            # must keep the whitelisted admin tool and drop everything else.
            assert "gitea_admin_get_users" in tool_names, (
                f"Expected gitea_admin_get_users to be present but got: {tool_names}"
            )
            assert "gitea_get_repo_issues" not in tool_names, (
                f"Expected gitea_get_repo_issues to be excluded but got: {tool_names}"
            )

    @pytest.mark.asyncio
    async def test_exclude_by_tag(self, tmp_path: Path):
        """Exclude tools by tag prefix through the full server path."""
        cfg = tmp_path / "tag_exclude.yaml"
        cfg.write_text("exclude:\n  - tag:admin")
        config = SimpleConfig(exclude_config_path=str(cfg))
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            assert "gitea_get_repo_issues" in tool_names
            assert not any("admin" in t for t in tool_names), (
                f"Expected no admin tools when excluding tag:admin, got: {tool_names}"
            )

    @pytest.mark.asyncio
    async def test_excluded_tools_also_exclude_resources(self, tmp_path: Path):
        """When a tool is excluded by config, its corresponding auto-generated
        resource should also be excluded at registration time."""
        # Use paths with {path_params} that are NOT in
        # AUTO_GENERATED_RESOURCE_SKIP_URIS so they produce auto resources.
        spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/repos/{owner}/{repo}/branches": {
                    "get": {
                        "operationId": "repo_list_branches",
                        "summary": "List repository branches",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/admin/users/{username}": {
                    "get": {
                        "operationId": "admin_get_user",
                        "summary": "Get admin user",
                        "tags": ["admin"],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        cfg = tmp_path / "exclude.yaml"
        cfg.write_text("exclude:\n  - gitea_admin_*")
        config = SimpleConfig(exclude_config_path=str(cfg))
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=spec)
            mcp = await create_mcp_server(gitea_client)

            # Excluded admin tools should not appear in the tool listing
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)
            assert not any("admin" in t for t in tool_names), (
                f"Expected no admin tools but found: {[t for t in tool_names if 'admin' in t]}"
            )

            # The corresponding auto-generated resources (registered as resource
            # TEMPLATES, not concrete resources) should also be absent.
            # The admin_get_user operation would produce gitea://admin/users/{username}
            # but it's excluded by config, so the resource template should not appear.
            templates = await mcp.list_resource_templates()
            template_uris = {str(t.uri_template) for t in (templates or [])}
            assert "gitea://admin/users/{username}" not in template_uris, (
                f"Expected no admin resource template for excluded tool, "
                f"got templates: {template_uris}"
            )

            # Non-admin auto-generated resource templates should still be present.
            # repo_list_branches produces gitea://repos/{owner}/{repo}/branches
            # which is NOT filtered (it has no admin tag).
            assert "gitea://repos/{owner}/{repo}/branches" in template_uris, (
                f"Expected non-excluded auto resource template, got: {template_uris}"
            )

    @pytest.mark.asyncio
    async def test_exclude_with_unprefixed_pattern(self, tmp_path: Path):
        """Unprefixed patterns match the unprefixed operationId of tools.

        Since tools are already prefixed at transform time, an unprefixed
        pattern like ``admin_*`` does NOT match ``gitea_admin_get_users``.
        Users should use ``gitea_admin_*`` for prefixed names.
        """
        cfg = tmp_path / "unprefixed.yaml"
        cfg.write_text("exclude:\n  - admin_*")
        config = SimpleConfig(exclude_config_path=str(cfg))
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=SWAGGER_SPEC)
            mcp = await create_mcp_server(gitea_client)
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)

            assert "gitea_admin_get_users" in tool_names, (
                f"Expected gitea_admin_get_users to be present (admin_* won't match "
                f"prefixed names), got: {tool_names}"
            )

    @pytest.mark.asyncio
    async def test_scoped_tools_also_exclude_resources(self):
        """When a tool is scope-filtered (token lacks required scope), its
        auto-generated resource template should also be excluded."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/repos/{owner}/{repo}/branches": {
                    "get": {
                        "operationId": "repo_list_branches",
                        "summary": "List repository branches",
                        "tags": ["repository"],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/admin/users/{username}": {
                    "get": {
                        "operationId": "admin_get_user",
                        "summary": "Get admin user",
                        "tags": ["admin"],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        token = "scope-test-no-sudo"
        config = SimpleConfig(
            tool_filtering_enabled=True,
            token=token,
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=spec)
            mock.get("https://git.example.com/api/v1/user").respond(
                200, json={"login": "testuser"}
            )
            # Token has read:repository but NOT sudo — admin tools should be
            # scope-filtered, which must also hide their auto resources.
            mock.get("https://git.example.com/api/v1/users/testuser/tokens").respond(
                200, json=[
                    {
                        "id": 1,
                        "name": "limited",
                        "token_last_eight": token[-8:],
                        "scopes": ["read:repository"],
                    }
                ],
            )
            mcp = await create_mcp_server(gitea_client)

            # Admin tool (needs sudo) should not appear
            tools = await mcp.list_tools()
            tool_names = extract_tool_names(tools)
            assert not any("admin" in t for t in tool_names), (
                f"Expected no admin tools but found: "
                f"{[t for t in tool_names if 'admin' in t]}"
            )

            # The admin auto-generated resource template should also be absent
            templates = await mcp.list_resource_templates()
            template_uris = {str(t.uri_template) for t in (templates or [])}
            assert "gitea://admin/users/{username}" not in template_uris, (
                f"Expected no admin resource template for scope-filtered tool, "
                f"got templates: {template_uris}"
            )

            # Non-admin auto resource template should still be present
            assert "gitea://repos/{owner}/{repo}/branches" in template_uris, (
                f"Expected non-filtered auto resource template, got: {template_uris}"
            )

    @pytest.mark.asyncio
    async def test_scoped_custom_resources_filtered(self):
        """Custom resources whose required_scope is not satisfied by the token's
        available scopes should be skipped at registration time, while those
        with no required scope or a matching scope remain visible."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "Gitea API", "version": "1.0"},
            "paths": {
                "/repos/{owner}/{repo}/branches": {
                    "get": {
                        "operationId": "repo_list_branches",
                        "summary": "List repository branches",
                        "tags": ["repository"],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
            "definitions": {},
        }

        token = "custom-scope-issue"
        config = SimpleConfig(
            tool_filtering_enabled=True,
            token=token,
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get("https://git.example.com/swagger.v1.json").respond(200, json=spec)
            mock.get("https://git.example.com/api/v1/user").respond(
                200, json={"login": "testuser"}
            )
            # Token has read:issue only — custom resources requiring
            # read:user or read:repository should be skipped.
            mock.get("https://git.example.com/api/v1/users/testuser/tokens").respond(
                200, json=[
                    {
                        "id": 1,
                        "name": "iss-only",
                        "token_last_eight": token[-8:],
                        "scopes": ["read:issue"],
                    }
                ],
            )
            mcp = await create_mcp_server(gitea_client)

            templates = await mcp.list_resource_templates()
            template_uris = {str(t.uri_template) for t in (templates or [])}

            # gitea://repos/{owner}/{repo}/labels requires read:issue → present
            assert "gitea://repos/{owner}/{repo}/labels" in template_uris, (
                f"Expected labels resource (needs read:issue) to be present, "
                f"got: {template_uris}"
            )

            # gitea://users/{username} requires read:user → absent
            assert "gitea://users/{username}" not in template_uris, (
                f"Expected user resource (needs read:user) to be filtered, "
                f"got: {template_uris}"
            )

            # gitea://repos/{owner}/{repo} requires read:repository → absent
            assert "gitea://repos/{owner}/{repo}" not in template_uris, (
                f"Expected repo resource (needs read:repository) to be filtered, "
                f"got: {template_uris}"
            )

            # gitea://version has no required scope → present (concrete resource)
            resources = await mcp.list_resources()
            resource_uris = {str(r.uri) for r in (resources or [])}
            assert "gitea://version" in resource_uris, (
                f"Expected version resource (no scope) to be present, "
                f"got resources: {resource_uris}"
            )
