"""Unit tests for the resource surface registry (issue #743)."""

from gitea_mcp_server.resources.surface import (
    RESOURCE_SURFACE,
    ResourceSurfaceEntry,
    clear_resource_surface,
    get_resource_surface,
    register_resource_surface,
)


class TestResourceSurface:
    """Tests for the registered resource surface."""

    def test_register_and_get(self) -> None:
        """Registering a resource makes it retrievable by base URI."""
        clear_resource_surface()
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/issues{?state,type}",
            "/repos/{owner}/{repo}/issues",
        )
        surface = get_resource_surface()
        assert "gitea://repos/{owner}/{repo}/issues" in surface
        entry = surface["gitea://repos/{owner}/{repo}/issues"]
        assert entry.api_path == "/repos/{owner}/{repo}/issues"
        assert entry.method == "GET"

    def test_base_uri_strips_query_suffix(self) -> None:
        """base_uri removes the {?query} suffix from the template."""
        entry = ResourceSurfaceEntry(
            uri_template="gitea://repos/{owner}/{repo}/issues{?state,type}",
            api_path="/repos/{owner}/{repo}/issues",
        )
        assert entry.base_uri == "gitea://repos/{owner}/{repo}/issues"

    def test_base_uri_without_suffix_unchanged(self) -> None:
        """A template without a query suffix is its own base URI."""
        entry = ResourceSurfaceEntry(
            uri_template="gitea://repos/{owner}/{repo}",
            api_path="/repos/{owner}/{repo}",
        )
        assert entry.base_uri == "gitea://repos/{owner}/{repo}"

    def test_register_deduplicates_by_base_uri(self) -> None:
        """Registering the same base URI twice keeps the latest entry."""
        clear_resource_surface()
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/issues",
            "/repos/{owner}/{repo}/issues",
        )
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/issues{?state}",
            "/repos/{owner}/{repo}/issues",
        )
        assert len(get_resource_surface()) == 1

    def test_clear_empties_registry(self) -> None:
        """clear_resource_surface empties the registry."""
        clear_resource_surface()
        register_resource_surface(
            "gitea://repos/{owner}/{repo}",
            "/repos/{owner}/{repo}",
        )
        clear_resource_surface()
        assert RESOURCE_SURFACE == {}
