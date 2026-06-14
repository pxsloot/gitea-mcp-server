"""Unit tests for label validation and auto-conversion functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.server_setup.mcp_builder import _customize_metadata
from gitea_mcp_server.tools.labels import (
    update_labels_schema as _update_labels_schema_impl,
)

# Create a dedicated label manager for these tests
_label_manager = LabelManager()


async def _get_repository_label_map(owner, repo, client):
    """Fetch label map using the test label manager."""
    return await _label_manager.get_label_map(owner, repo, client)


def _update_labels_schema(component):
    """Update labels schema."""
    return _update_labels_schema_impl(component)


class TestLabelCache:
    """Tests for label cache infrastructure."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the label manager cache before each test."""
        _label_manager.clear_cache()
        yield
        _label_manager.clear_cache()

    def test_cache_miss_fetches_and_caches(self):
        """Cache miss should fetch labels and populate cache."""
        client = MagicMock()
        client.request = AsyncMock(
            return_value=[
                {"id": 1, "name": "bug", "color": "ff0000", "description": "Bug"},
                {"id": 2, "name": "enhancement", "color": "00ff00", "description": "Feature"},
            ]
        )

        # First call - cache miss
        result = asyncio.run(_get_repository_label_map("owner", "repo", client))

        assert result == {
            "bug": {"id": 1, "name": "bug"},
            "enhancement": {"id": 2, "name": "enhancement"},
        }
        assert ("owner", "repo") in _label_manager._label_cache

        # Verify client was called correctly
        client.request.assert_called_once_with("GET", "/repos/owner/repo/labels")

    def test_cache_hit_returns_cached(self):
        """Second call with same repo should hit cache."""
        client = MagicMock()
        client.request = AsyncMock(
            return_value=[
                {"id": 1, "name": "bug", "color": "ff0000", "description": "Bug"},
            ]
        )

        # First call
        asyncio.run(_get_repository_label_map("owner", "repo", client))
        # Second call
        asyncio.run(_get_repository_label_map("owner", "repo", client))

        # Should only call API once (cache hit second time)
        assert client.request.call_count == 1

    def test_different_repos_separate_cache_entries(self):
        """Different (owner, repo) pairs should have separate cache entries."""
        client = MagicMock()
        client.request = AsyncMock(
            side_effect=[
                [{"id": 1, "name": "bug", "color": "ff0000", "description": "Bug"}],
                [{"id": 2, "name": "feature", "color": "0000ff", "description": "Feature"}],
            ]
        )

        asyncio.run(_get_repository_label_map("owner1", "repo1", client))
        asyncio.run(_get_repository_label_map("owner2", "repo2", client))

        assert client.request.call_count == 2

    def test_cache_ttl_expires(self):
        """Cache entries should expire after TTL."""
        # Save original TTL
        original_ttl = _label_manager._cache_ttl
        _label_manager._cache_ttl = 0.1  # 100ms

        client = MagicMock()
        client.request = AsyncMock(
            return_value=[{"id": 1, "name": "bug", "color": "ff0000", "description": "Bug"}]
        )

        # First call
        asyncio.run(_get_repository_label_map("owner", "repo", client))
        assert client.request.call_count == 1

        # Wait for TTL to expire
        asyncio.run(asyncio.sleep(0.2))

        # Second call should refetch
        asyncio.run(_get_repository_label_map("owner", "repo", client))
        assert client.request.call_count == 2

        # Restore TTL
        _label_manager._cache_ttl = original_ttl

    def test_case_insensitive_matching(self):
        """Label name lookup should be case-insensitive."""
        client = MagicMock()
        client.request = AsyncMock(
            return_value=[
                {"id": 1, "name": "Bug", "color": "ff0000", "description": "Bug label"},
                {"id": 2, "name": "Enhancement", "color": "00ff00", "description": "Feature"},
            ]
        )

        asyncio.run(_get_repository_label_map("owner", "repo", client))
        cache = _label_manager._label_cache[("owner", "repo")]["map"]

        assert "bug" in cache
        assert "enhancement" in cache
        assert cache["bug"]["id"] == 1
        assert cache["enhancement"]["id"] == 2


class TestUpdateLabelsSchema:
    """Tests for the _update_labels_schema function."""

    def test_updates_integer_type_to_union(self):
        """Schema with integer items.type should become [string, integer]."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "integer"},
                }
            }
        }

        _update_labels_schema(tool)

        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]

    def test_updates_string_type_to_union(self):
        """Schema with string items.type should become [string, integer]."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            }
        }

        _update_labels_schema(tool)

        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]

    def test_preserves_existing_union(self):
        """Schema already with union type should not be modified."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": ["string", "integer"]},
                }
            }
        }

        _update_labels_schema(tool)

        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]

    def test_skips_non_array_labels(self):
        """If labels is not array type, schema should not be modified."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {"type": "string"},
            }
        }

        _update_labels_schema(tool)

        # Should remain unchanged
        assert tool.parameters["properties"]["labels"]["type"] == "string"

    def test_skips_no_labels_property(self):
        """Tool without labels property should not be modified."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            }
        }

        _update_labels_schema(tool)

        # Should remain unchanged
        assert "labels" not in tool.parameters["properties"]

    def test_skips_no_parameters(self):
        """Tool without parameters attribute should not crash."""
        tool = MagicMock()
        # No parameters attribute
        del tool.parameters

        # Should not raise
        _update_labels_schema(tool)

    def test_skips_empty_parameters(self):
        """Tool with None parameters should not crash."""
        tool = MagicMock()
        tool.parameters = None

        # Should not raise
        _update_labels_schema(tool)

    def test_updates_schema_during_customize(self):
        """_customize_metadata should trigger schema update for tools with labels."""
        from fastmcp.server.providers.openapi import OpenAPITool

        route = MagicMock(
            path="/repos/{owner}/{repo}/issues",
            summary="Create issue",
            operation_id="issue_create_repo_issue",
        )
        tool = MagicMock(spec=OpenAPITool)
        tool.name = "issue_create_repo_issue"
        tool.annotations = None
        tool.tags = set()
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "integer"}},
                "title": {"type": "string"},
            }
        }
        tool.output_schema = None
        tool.description = "Create issue"
        tool.version = "1"
        tool.auth = None
        tool.serializer = None
        tool.meta = {}

        _customize_metadata(route, tool, openapi_spec={})

        # Verify schema was updated
        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]
