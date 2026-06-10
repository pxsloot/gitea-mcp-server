"""Unit tests for ExclusionTransform — tool/resource exclusion via config.

Tests verify that the transform correctly filters tools, resources, and
resource templates based on exclude/include patterns from a config file.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastmcp.resources import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.base import Tool

from gitea_mcp_server.tools.exclusion import ExclusionTransform, load_exclusion_config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tool_admin_create() -> Tool:
    return Tool(
        name="admin_create_user",
        description="Create a user (admin)",
        parameters={"properties": {}},
        tags={"admin"},
    )


@pytest.fixture
def tool_admin_list() -> Tool:
    return Tool(
        name="admin_list_users",
        description="List users (admin)",
        parameters={"properties": {}},
        tags={"admin"},
    )


@pytest.fixture
def tool_repo_get() -> Tool:
    return Tool(
        name="repo_get",
        description="Get a repository",
        parameters={"properties": {}},
        tags={"repository"},
    )


@pytest.fixture
def tool_repo_delete() -> Tool:
    return Tool(
        name="repo_delete",
        description="Delete a repository",
        parameters={"properties": {}},
        tags={"repository"},
    )


@pytest.fixture
def tool_issue_list() -> Tool:
    return Tool(
        name="issue_list_issues",
        description="List issues",
        parameters={"properties": {}},
        tags={"issue", "repository"},
    )


@pytest.fixture
def sample_tools(tool_admin_create, tool_admin_list, tool_repo_get, tool_repo_delete, tool_issue_list):
    return [tool_admin_create, tool_admin_list, tool_repo_get, tool_repo_delete, tool_issue_list]


@pytest.fixture
def sample_resources() -> list[Resource]:
    return [
        Resource(uri="gitea://repos/owner/repo", name="repo_get", tags={"repository"}),
        Resource(uri="gitea://admin/users", name="admin_list_users", tags={"admin"}),
    ]


@pytest.fixture
def sample_templates() -> list[ResourceTemplate]:
    return [
        ResourceTemplate(
            uri_template="gitea://repos/{owner}/{repo}",
            name="repo_template",
            parameters={"owner": str, "repo": str},
            tags={"repository"},
        ),
        ResourceTemplate(
            uri_template="gitea://admin/users/{user}",
            name="admin_user_template",
            parameters={"user": str},
            tags={"admin"},
        ),
    ]


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------

class TestLoadExclusionConfig:
    """Tests for load_exclusion_config — the YAML config file loader."""

    def test_none_path_returns_empty(self):
        config = load_exclusion_config(None)
        assert config == {"exclude": [], "include": []}

    def test_missing_file_returns_empty(self):
        config = load_exclusion_config("/nonexistent/path.yaml")
        assert config == {"exclude": [], "include": []}

    def test_empty_yaml(self, tmp_path: Path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        config = load_exclusion_config(str(cfg))
        assert config == {"exclude": [], "include": []}

    def test_exclude_only(self, tmp_path: Path):
        cfg = tmp_path / "exclude.yaml"
        cfg.write_text("exclude:\n  - repo_delete\n  - admin_*")
        config = load_exclusion_config(str(cfg))
        assert config["exclude"] == ["repo_delete", "admin_*"]
        assert config["include"] == []

    def test_both_exclude_and_include(self, tmp_path: Path):
        cfg = tmp_path / "both.yaml"
        cfg.write_text("exclude:\n  - admin_*\ninclude:\n  - admin_list_users")
        config = load_exclusion_config(str(cfg))
        assert config["exclude"] == ["admin_*"]
        assert config["include"] == ["admin_list_users"]

    def test_malformed_yaml_returns_empty(self, tmp_path: Path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("{bad: yaml: [broken")
        config = load_exclusion_config(str(cfg))
        assert config == {"exclude": [], "include": []}

    def test_only_include_no_exclude(self, tmp_path: Path):
        cfg = tmp_path / "include_only.yaml"
        cfg.write_text("include:\n  - repo_get")
        config = load_exclusion_config(str(cfg))
        assert config["exclude"] == []
        assert config["include"] == ["repo_get"]


# ---------------------------------------------------------------------------
# ExclusionTransform — list_tools
# ---------------------------------------------------------------------------

class TestExclusionTransformListTools:
    """Tests for ExclusionTransform.list_tools."""

    async def test_empty_config_passes_all(self, sample_tools):
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.list_tools(sample_tools)
        assert len(result) == len(sample_tools)

    async def test_exclude_exact_name(self, sample_tools):
        transform = ExclusionTransform(exclude=["repo_delete"], include=[])
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "repo_delete" not in names
        assert "repo_get" in names

    async def test_exclude_glob_pattern(self, sample_tools):
        transform = ExclusionTransform(exclude=["admin_*"], include=[])
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "admin_create_user" not in names
        assert "admin_list_users" not in names
        assert "repo_get" in names

    async def test_exclude_tag(self, sample_tools):
        transform = ExclusionTransform(exclude=["tag:admin"], include=[])
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "admin_create_user" not in names
        assert "admin_list_users" not in names
        assert "repo_get" in names
        assert "repo_delete" in names

    async def test_include_overrides_exclude_exact(self, sample_tools):
        transform = ExclusionTransform(exclude=["admin_*"], include=["admin_list_users"])
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "admin_create_user" not in names
        assert "admin_list_users" in names  # re-included

    async def test_include_overrides_exclude_tag(self, sample_tools):
        transform = ExclusionTransform(exclude=["tag:admin"], include=["admin_list_users"])
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "admin_create_user" not in names
        assert "admin_list_users" in names

    async def test_exclude_star(self, sample_tools):
        transform = ExclusionTransform(exclude=["*"], include=[])
        result = await transform.list_tools(sample_tools)
        assert len(result) == 0

    async def test_exclude_star_with_include(self, sample_tools):
        transform = ExclusionTransform(exclude=["*"], include=["repo_get"])
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert names == ["repo_get"]

    async def test_no_side_effects_on_tool_objects(self, sample_tools):
        original_names = [t.name for t in sample_tools]
        transform = ExclusionTransform(exclude=["admin_*"], include=[])
        await transform.list_tools(sample_tools)
        assert [t.name for t in sample_tools] == original_names

    async def test_exclude_with_prefix_matches_prefixed_name(self, sample_tools):
        """Pattern ``gitea_repo_*`` should match ``repo_get`` when prefix is set."""
        transform = ExclusionTransform(exclude=["gitea_repo_*"], include=[], tool_prefix="gitea_")
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "repo_get" not in names
        assert "repo_delete" not in names
        assert "admin_create_user" in names

    async def test_exclude_with_prefix_no_match_without_prefix(self, sample_tools):
        """Pattern ``gitea_repo_*`` should NOT match without tool_prefix."""
        transform = ExclusionTransform(exclude=["gitea_repo_*"], include=[])
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "repo_get" in names  # unprefixed name doesn't start with gitea_

    async def test_include_with_prefix_overrides_exclude(self, sample_tools):
        transform = ExclusionTransform(
            exclude=["gitea_repo_*"],
            include=["gitea_repo_get"],
            tool_prefix="gitea_",
        )
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "repo_delete" not in names
        assert "repo_get" in names  # included

    async def test_exclude_with_wildcard_and_prefix(self, sample_tools):
        """Pattern ``wiki_*`` with prefix should not match repo tools."""
        transform = ExclusionTransform(exclude=["wiki_*"], include=[], tool_prefix="gitea_")
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "repo_get" in names  # not matching wiki_*
        assert "admin_create_user" in names

    async def test_exclude_with_wiki_glob_matches_wiki_tool(self):
        """Pattern ``*wiki*`` should match ``repo_get_wiki_page``."""
        tool = Tool(name="repo_get_wiki_page", parameters={"properties": {}}, tags={"repository"})
        transform = ExclusionTransform(exclude=["*wiki*"], include=[])
        result = await transform.list_tools([tool])
        assert len(result) == 0

    async def test_exclude_with_wiki_glob_no_prefix_no_match(self):
        """Pattern ``gitea_*wiki*`` should NOT match without tool_prefix."""
        tool = Tool(name="repo_get_wiki_page", parameters={"properties": {}}, tags={"repository"})
        transform = ExclusionTransform(exclude=["gitea_*wiki*"], include=[])
        result = await transform.list_tools([tool])
        assert len(result) == 1

    async def test_exclude_with_wiki_glob_prefix_matches(self):
        """Pattern ``gitea_*wiki*`` should match ``repo_get_wiki_page`` with tool_prefix."""
        tool = Tool(name="repo_get_wiki_page", parameters={"properties": {}}, tags={"repository"})
        transform = ExclusionTransform(exclude=["gitea_*wiki*"], include=[], tool_prefix="gitea_")
        result = await transform.list_tools([tool])
        assert len(result) == 0


# ---------------------------------------------------------------------------
# ExclusionTransform — get_tool
# ---------------------------------------------------------------------------

class TestExclusionTransformGetTool:
    """Tests for ExclusionTransform.get_tool."""

    async def test_allowed_tool_passes_through(self):
        tool = Tool(name="repo_get", parameters={"properties": {}})
        call_next = AsyncMock(return_value=tool)
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.get_tool("repo_get", call_next)
        assert result is tool
        call_next.assert_called_once_with("repo_get", version=None)

    async def test_excluded_tool_returns_none(self):
        tool = Tool(name="repo_delete", parameters={"properties": {}})
        call_next = AsyncMock(return_value=tool)
        transform = ExclusionTransform(exclude=["repo_delete"], include=[])
        result = await transform.get_tool("repo_delete", call_next)
        assert result is None
        call_next.assert_called_once_with("repo_delete", version=None)

    async def test_included_tool_passes_despite_exclude(self):
        tool = Tool(name="admin_list_users", parameters={"properties": {}}, tags={"admin"})
        call_next = AsyncMock(return_value=tool)
        transform = ExclusionTransform(exclude=["tag:admin"], include=["admin_list_users"])
        result = await transform.get_tool("admin_list_users", call_next)
        assert result is tool

    async def test_none_from_call_next_passes_through(self):
        call_next = AsyncMock(return_value=None)
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.get_tool("nonexistent", call_next)
        assert result is None

    async def test_excluded_tool_returns_none_even_without_call_next(self):
        """Excluded tools return None regardless of whether call_next finds them."""
        tool = Tool(name="repo_delete", parameters={"properties": {}})
        call_next = AsyncMock(return_value=tool)
        transform = ExclusionTransform(exclude=["repo_delete"], include=[])
        result = await transform.get_tool("repo_delete", call_next)
        assert result is None

    async def test_version_passed_through(self):
        tool = Tool(name="repo_get", parameters={"properties": {}})
        call_next = AsyncMock(return_value=tool)
        transform = ExclusionTransform(exclude=[], include=[])
        await transform.get_tool("repo_get", call_next, version="2.0")
        call_next.assert_called_once_with("repo_get", version="2.0")

    async def test_get_tool_exclude_with_prefix(self):
        tool = Tool(name="repo_get_wiki_page", parameters={"properties": {}})
        call_next = AsyncMock(return_value=tool)
        transform = ExclusionTransform(exclude=["gitea_*wiki*"], include=[], tool_prefix="gitea_")
        result = await transform.get_tool("repo_get_wiki_page", call_next)
        assert result is None


# ---------------------------------------------------------------------------
# ExclusionTransform — list_resources
# ---------------------------------------------------------------------------

class TestExclusionTransformListResources:
    """Tests for ExclusionTransform.list_resources."""

    async def test_empty_config_passes_all_resources(self, sample_resources):
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.list_resources(sample_resources)
        assert len(result) == len(sample_resources)

    async def test_exclude_by_name(self, sample_resources):
        transform = ExclusionTransform(exclude=["admin_list_users"], include=[])
        result = await transform.list_resources(sample_resources)
        names = [r.name for r in result]
        assert "admin_list_users" not in names

    async def test_exclude_by_tag(self, sample_resources):
        transform = ExclusionTransform(exclude=["tag:admin"], include=[])
        result = await transform.list_resources(sample_resources)
        names = [r.name for r in result]
        assert "admin_list_users" not in names
        assert "repo_get" in names

    async def test_include_overrides_exclude(self, sample_resources):
        transform = ExclusionTransform(exclude=["tag:admin"], include=["admin_list_users"])
        result = await transform.list_resources(sample_resources)
        names = [r.name for r in result]
        assert "admin_list_users" in names


# ---------------------------------------------------------------------------
# ExclusionTransform — get_resource
# ---------------------------------------------------------------------------

class TestExclusionTransformGetResource:
    """Tests for ExclusionTransform.get_resource."""

    async def test_allowed_passes_through(self):
        resource = Resource(uri="gitea://repos/owner/repo", name="repo_get")
        call_next = AsyncMock(return_value=resource)
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.get_resource("gitea://repos/owner/repo", call_next)
        assert result is resource

    async def test_excluded_returns_none(self):
        resource = Resource(uri="gitea://admin/users", name="admin_list_users", tags={"admin"})
        call_next = AsyncMock(return_value=resource)
        transform = ExclusionTransform(exclude=["tag:admin"], include=[])
        result = await transform.get_resource("gitea://admin/users", call_next)
        assert result is None

    async def test_none_from_call_next_passes_through(self):
        call_next = AsyncMock(return_value=None)
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.get_resource("gitea://nonexistent", call_next)
        assert result is None


# ---------------------------------------------------------------------------
# ExclusionTransform — list_resource_templates
# ---------------------------------------------------------------------------

class TestExclusionTransformListTemplates:
    """Tests for ExclusionTransform.list_resource_templates."""

    async def test_empty_config_passes_all(self, sample_templates):
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.list_resource_templates(sample_templates)
        assert len(result) == len(sample_templates)

    async def test_exclude_by_name(self, sample_templates):
        transform = ExclusionTransform(exclude=["admin_user_template"], include=[])
        result = await transform.list_resource_templates(sample_templates)
        names = [t.name for t in result]
        assert "admin_user_template" not in names

    async def test_exclude_by_tag(self, sample_templates):
        transform = ExclusionTransform(exclude=["tag:admin"], include=[])
        result = await transform.list_resource_templates(sample_templates)
        names = [t.name for t in result]
        assert "admin_user_template" not in names
        assert "repo_template" in names

    async def test_include_overrides_exclude(self, sample_templates):
        transform = ExclusionTransform(exclude=["tag:admin"], include=["admin_user_template"])
        result = await transform.list_resource_templates(sample_templates)
        names = [t.name for t in result]
        assert "admin_user_template" in names


# ---------------------------------------------------------------------------
# ExclusionTransform — get_resource_template
# ---------------------------------------------------------------------------

class TestExclusionTransformGetTemplate:
    """Tests for ExclusionTransform.get_resource_template."""

    async def test_allowed_passes_through(self):
        template = ResourceTemplate(
            uri_template="gitea://repos/{owner}/{repo}",
            name="repo_template",
            parameters={"owner": str, "repo": str},
        )
        call_next = AsyncMock(return_value=template)
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.get_resource_template("gitea://repos/owner/repo", call_next)
        assert result is template

    async def test_excluded_returns_none(self):
        template = ResourceTemplate(
            uri_template="gitea://admin/users/{user}",
            name="admin_user_template",
            parameters={"user": str},
            tags={"admin"},
        )
        call_next = AsyncMock(return_value=template)
        transform = ExclusionTransform(exclude=["tag:admin"], include=[])
        result = await transform.get_resource_template("gitea://admin/users/user", call_next)
        assert result is None

    async def test_none_template_returns_none(self):
        """get_resource_template returns None when call_next returns None (line 158)."""
        call_next = AsyncMock(return_value=None)
        transform = ExclusionTransform(exclude=[], include=[])
        result = await transform.get_resource_template("gitea://repos/owner/repo", call_next)
        assert result is None


# ---------------------------------------------------------------------------
# Integration: load + transform
# ---------------------------------------------------------------------------

class TestConfigFileIntegration:
    """Tests that load_exclusion_config output feeds ExclusionTransform correctly."""

    async def test_load_then_exclude(self, tmp_path: Path, sample_tools):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("exclude:\n  - repo_delete")
        config = load_exclusion_config(str(cfg))
        transform = ExclusionTransform(**config)
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "repo_delete" not in names
        assert "repo_get" in names

    async def test_load_then_exclude_with_include(self, tmp_path: Path, sample_tools):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(
            "exclude:\n  - tag:admin\ninclude:\n  - admin_list_users"
        )
        config = load_exclusion_config(str(cfg))
        transform = ExclusionTransform(**config)
        result = await transform.list_tools(sample_tools)
        names = [t.name for t in result]
        assert "admin_create_user" not in names
        assert "admin_list_users" in names

    async def test_none_path_no_filtering(self, sample_tools):
        config = load_exclusion_config(None)
        transform = ExclusionTransform(**config)
        result = await transform.list_tools(sample_tools)
        assert len(result) == len(sample_tools)

    async def test_include_without_exclude_does_nothing(self, tmp_path: Path, sample_tools):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("include:\n  - repo_get")
        config = load_exclusion_config(str(cfg))
        transform = ExclusionTransform(**config)
        result = await transform.list_tools(sample_tools)
        assert len(result) == len(sample_tools)
