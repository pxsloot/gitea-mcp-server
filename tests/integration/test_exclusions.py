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
