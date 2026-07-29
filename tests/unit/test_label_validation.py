"""Unit tests for label validation and auto-conversion functionality."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.server_setup.mcp_builder import _customize_metadata
from tests.helpers.spec_fixtures import make_openapi_spec

if TYPE_CHECKING:
    from collections.abc import Generator

from gitea_mcp_server.tools.labels import (
    update_labels_schema as _update_labels_schema_impl,
)

# Create a dedicated label service for these tests
_label_service = LabelService()


async def _get_repository_label_map(owner: str, repo: str, client: Any) -> dict[str, dict[str, Any]]:
    """Fetch label map using the test label service."""
    return await _label_service.get_label_map(owner, repo, client)


async def _get_repository_id_map(owner: str, repo: str, client: Any) -> dict[int, dict[str, Any]]:
    """Fetch ID map using the test label service."""
    return await _label_service.get_id_map(owner, repo, client)


def _update_labels_schema(component: Any) -> None:
    """Update labels schema."""
    _update_labels_schema_impl(component)


class TestLabelCache:
    """Tests for label cache infrastructure."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> Generator[None, None, None]:
        """Clear the label service cache before each test."""
        _label_service.clear_cache()
        yield
        _label_service.clear_cache()

    def test_cache_miss_fetches_and_caches(self) -> None:
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
        assert ("owner", "repo") in _label_service._label_cache

        # Verify client was called correctly
        client.request.assert_called_once_with("GET", "/repos/owner/repo/labels")

    def test_cache_hit_returns_cached(self) -> None:
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

    def test_different_repos_separate_cache_entries(self) -> None:
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

    def test_cache_ttl_expires(self) -> None:
        """Cache entries should expire after TTL."""
        # Save original TTL
        original_ttl = _label_service._cache_ttl
        _label_service._cache_ttl = 0.1  # type: ignore[assignment]

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
        _label_service._cache_ttl = original_ttl

    def test_case_insensitive_matching(self) -> None:
        """Label name lookup should be case-insensitive."""
        client = MagicMock()
        client.request = AsyncMock(
            return_value=[
                {"id": 1, "name": "Bug", "color": "ff0000", "description": "Bug label"},
                {"id": 2, "name": "Enhancement", "color": "00ff00", "description": "Feature"},
            ]
        )

        asyncio.run(_get_repository_label_map("owner", "repo", client))
        cache = _label_service._label_cache[("owner", "repo")]
        name_map = cache["map"]

        assert "bug" in name_map
        assert "enhancement" in name_map
        assert name_map["bug"]["id"] == 1
        assert name_map["enhancement"]["id"] == 2

    def test_id_map_populated(self) -> None:
        """ID map should be populated alongside name map."""
        client = MagicMock()
        client.request = AsyncMock(
            return_value=[
                {"id": 10, "name": "bug", "color": "ff0000", "description": "Bug"},
                {"id": 20, "name": "feature", "color": "00ff00", "description": "Feature"},
            ]
        )

        asyncio.run(_get_repository_id_map("owner", "repo", client))
        cache = _label_service._label_cache[("owner", "repo")]
        id_map = cache["id_map"]

        assert 10 in id_map
        assert 20 in id_map
        assert id_map[10]["name"] == "bug"
        assert id_map[20]["name"] == "feature"


class TestLabelServiceValidateAndConvert:
    """Tests for LabelService.validate_and_convert."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        _label_service.clear_cache()

    def _make_client(self, labels_data: list[dict[str, Any]]) -> MagicMock:
        client = MagicMock()
        client.request = AsyncMock(return_value=labels_data)
        return client

    @pytest.mark.asyncio
    async def test_converts_known_strings(self) -> None:
        """Known string label names should be converted to integer IDs."""
        client = self._make_client([
            {"id": 1, "name": "bug"},
            {"id": 2, "name": "feature"},
        ])
        result = await _label_service.validate_and_convert(
            ["bug", "feature"], "owner", "repo", client
        )
        assert result == [1, 2]

    @pytest.mark.asyncio
    async def test_passes_through_valid_integers(self) -> None:
        """Integer IDs that exist in the label map should pass through."""
        client = self._make_client([
            {"id": 1, "name": "bug"},
            {"id": 42, "name": "feature"},
        ])
        result = await _label_service.validate_and_convert(
            [1, 42], "owner", "repo", client
        )
        assert result == [1, 42]

    @pytest.mark.asyncio
    async def test_raises_for_unknown_integer(self) -> None:
        """Unknown integer ID should raise ValidationError."""
        client = self._make_client([
            {"id": 1, "name": "bug"},
        ])
        with pytest.raises(ValidationError) as excinfo:
            await _label_service.validate_and_convert(
                [1, 99999], "owner", "repo", client
            )
        assert "99999" in str(excinfo.value)
        assert "owner/repo" in str(excinfo.value)
        assert excinfo.value.field == "labels"

    @pytest.mark.asyncio
    async def test_raises_for_unknown_string(self) -> None:
        """Unknown string label should raise ValidationError."""
        client = self._make_client([
            {"id": 1, "name": "bug"},
        ])
        with pytest.raises(ValidationError) as excinfo:
            await _label_service.validate_and_convert(
                ["nonexistent"], "owner", "repo", client
            )
        assert "nonexistent" in str(excinfo.value)
        assert excinfo.value.field == "labels"

    @pytest.mark.asyncio
    async def test_raises_for_mixed_unknowns(self) -> None:
        """Both unknown strings and integers should be reported in one error."""
        client = self._make_client([
            {"id": 1, "name": "bug"},
        ])
        with pytest.raises(ValidationError) as excinfo:
            await _label_service.validate_and_convert(
                ["bug", "bad_label", 99999], "owner", "repo", client
            )
        msg = str(excinfo.value)
        assert "bad_label" in msg
        assert "99999" in msg
        assert "owner/repo" in msg

    @pytest.mark.asyncio
    async def test_empty_labels_returns_empty_list(self) -> None:
        """Empty labels list should return empty list."""
        client = self._make_client([])
        result = await _label_service.validate_and_convert(
            [], "owner", "repo", client
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_case_insensitive_string_lookup(self) -> None:
        """String label lookup should be case-insensitive."""
        client = self._make_client([
            {"id": 5, "name": "Kind/Enhancement"},
        ])
        result = await _label_service.validate_and_convert(
            ["kind/enhancement", "KIND/ENHANCEMENT", "Kind/Enhancement"],
            "owner", "repo", client,
        )
        assert result == [5, 5, 5]


class TestLabelServiceFormatAvailable:
    """Tests for LabelService.format_available."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        _label_service.clear_cache()

    @pytest.mark.asyncio
    async def test_groups_labels_by_prefix(self) -> None:
        """Labels with same prefix should be grouped together."""
        client = MagicMock()
        client.request = AsyncMock(return_value=[
            {"id": 1, "name": "type/bug"},
            {"id": 2, "name": "priority/high"},
            {"id": 3, "name": "type/feature"},
            {"id": 4, "name": "priority/low"},
            {"id": 5, "name": "status/triage"},
        ])
        result = await _label_service.format_available("owner", "repo", client)
        assert "type/bug, type/feature" in result
        assert "priority/high, priority/low" in result
        assert "status/triage" in result

    @pytest.mark.asyncio
    async def test_labels_without_prefix(self) -> None:
        """Labels without a '/' should be grouped under empty prefix."""
        client = MagicMock()
        client.request = AsyncMock(return_value=[
            {"id": 1, "name": "urgent"},
            {"id": 2, "name": "type/bug"},
            {"id": 3, "name": "wontfix"},
        ])
        result = await _label_service.format_available("owner", "repo", client)
        assert "urgent, wontfix" in result
        assert "type/bug" in result


class TestUpdateLabelsSchema:
    """Tests for the _update_labels_schema function."""

    def test_updates_integer_type_to_union(self) -> None:
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

    def test_updates_string_type_to_union(self) -> None:
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

    def test_preserves_existing_union(self) -> None:
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

    def test_skips_non_array_labels(self) -> None:
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

    def test_skips_no_labels_property(self) -> None:
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

    def test_skips_no_parameters(self) -> None:
        """Tool without parameters attribute should not crash."""
        tool = MagicMock()
        # No parameters attribute
        del tool.parameters

        # Should not raise
        _update_labels_schema(tool)

    def test_skips_empty_parameters(self) -> None:
        """Tool with None parameters should not crash."""
        tool = MagicMock()
        tool.parameters = None

        # Should not raise
        _update_labels_schema(tool)

    def test_updates_schema_during_customize(self) -> None:
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

        _customize_metadata(route, tool, openapi_spec=make_openapi_spec())

        # Verify schema was updated
        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]
