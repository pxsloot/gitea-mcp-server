"""Unit tests for exclusion config loading and spec-level route exclusion.

Tests verify that ``load_exclusion_config`` loads exclude/include patterns
correctly, and that the spec-level filtering path (``compute_filtered_tools_info``
+ ``_compute_excluded_routes``) drops the right operations via ``route_map_fn``.

Filtering happens at spec-prep time — no runtime transform applies exclusion.
"""

from pathlib import Path

import pytest

from gitea_mcp_server.server_setup.spec_loader import (
    _compute_excluded_routes,
    load_exclusion_config,
)
from gitea_mcp_server.tools.exclusion import (
    matches_any,
    matches_pattern,
)
from gitea_mcp_server.tools.filter_info import compute_filtered_tools_info


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestLoadExclusionConfig:
    """Tests for load_exclusion_config - the YAML config file loader."""

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
# Pattern matching tests
# ---------------------------------------------------------------------------


class TestPatternMatching:
    """Tests for matches_pattern / matches_any used by spec-level filtering."""

    def test_exact_name(self):
        assert matches_pattern("repo_delete", set(), "repo_delete")
        assert not matches_pattern("repo_get", set(), "repo_delete")

    def test_glob_pattern(self):
        assert matches_pattern("admin_create_user", set(), "admin_*")
        assert not matches_pattern("repo_get", set(), "admin_*")

    def test_tag_pattern(self):
        assert matches_pattern("admin_create_user", {"admin"}, "tag:admin")
        assert not matches_pattern("repo_get", {"repository"}, "tag:admin")

    def test_prefix_matches_prefixed_name(self):
        assert matches_pattern("repo_get", set(), "gitea_repo_*", tool_prefix="gitea_")
        assert not matches_pattern("repo_get", set(), "gitea_repo_*")

    def test_matches_any(self):
        assert matches_any("admin_x", {"admin"}, ["repo_*", "admin_*"])
        assert not matches_any("repo_get", {"repository"}, ["admin_*"])


# ---------------------------------------------------------------------------
# Spec-level route exclusion (replaces the old transform)
# ---------------------------------------------------------------------------


SPEC = {
    "openapi": "3.1.1",
    "info": {"title": "Test", "version": "1.0.0"},
    "paths": {
        "/repos/{owner}/{repo}": {
            "get": {"operationId": "repo_get", "tags": ["repository"]},
            "delete": {"operationId": "repo_delete", "tags": ["repository"]},
        },
        "/admin/users": {
            "get": {"operationId": "admin_list_users", "tags": ["admin"]},
        },
    },
    "components": {"schemas": {}},
}


class TestSpecLevelExclusion:
    """Exclusion now flows through compute_filtered_tools_info + route_map_fn."""

    def test_exclude_by_exact_name(self, tmp_path: Path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("exclude:\n  - repo_delete")
        config = load_exclusion_config(str(cfg))
        filtered = compute_filtered_tools_info(
            SPEC, available_scopes={"sudo"}, exclusion_config=config, tool_prefix=""
        )
        excluded = _compute_excluded_routes(SPEC, filtered)
        assert ("/repos/{owner}/{repo}", "DELETE") in excluded
        assert ("/repos/{owner}/{repo}", "GET") not in excluded

    def test_exclude_by_glob(self, tmp_path: Path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("exclude:\n  - admin_*")
        config = load_exclusion_config(str(cfg))
        filtered = compute_filtered_tools_info(
            SPEC, available_scopes={"sudo"}, exclusion_config=config, tool_prefix=""
        )
        excluded = _compute_excluded_routes(SPEC, filtered)
        assert ("/admin/users", "GET") in excluded

    def test_exclude_by_tag(self, tmp_path: Path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("exclude:\n  - tag:admin")
        config = load_exclusion_config(str(cfg))
        filtered = compute_filtered_tools_info(
            SPEC, available_scopes={"sudo"}, exclusion_config=config, tool_prefix=""
        )
        excluded = _compute_excluded_routes(SPEC, filtered)
        assert ("/admin/users", "GET") in excluded

    def test_include_overrides_exclude(self, tmp_path: Path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("exclude:\n  - tag:admin\ninclude:\n  - admin_list_users")
        config = load_exclusion_config(str(cfg))
        filtered = compute_filtered_tools_info(
            SPEC, available_scopes={"sudo"}, exclusion_config=config, tool_prefix=""
        )
        excluded = _compute_excluded_routes(SPEC, filtered)
        assert ("/admin/users", "GET") not in excluded

    def test_exclude_star_with_include_whitelist(self, tmp_path: Path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text('exclude:\n  - "*"\ninclude:\n  - admin_list_users')
        config = load_exclusion_config(str(cfg))
        filtered = compute_filtered_tools_info(
            SPEC, available_scopes={"sudo"}, exclusion_config=config, tool_prefix=""
        )
        excluded = _compute_excluded_routes(SPEC, filtered)
        assert ("/admin/users", "GET") not in excluded
        assert ("/repos/{owner}/{repo}", "GET") in excluded
        assert ("/repos/{owner}/{repo}", "DELETE") in excluded

    def test_no_config_no_exclusion(self):
        filtered = compute_filtered_tools_info(
            SPEC,
            available_scopes={"sudo"},
            exclusion_config={"exclude": [], "include": []},
            tool_prefix="",
        )
        excluded = _compute_excluded_routes(SPEC, filtered)
        assert excluded == set()
