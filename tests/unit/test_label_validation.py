"""Unit tests for label validation and auto-conversion functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitea_mcp_server.constants import LABEL_GUIDANCE
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.server_setup.tool_annotator import (
    customize_component as _customize_component_impl,
    inject_label_validation_wrapper as _inject_label_validation_wrapper_impl,
    maybe_wrap_labels as _maybe_wrap_labels_impl,
    update_labels_schema as _update_labels_schema_impl,
)

# Create a dedicated label manager for these tests
_label_manager = LabelManager()


# Compatibility wrappers matching old signatures
async def _get_repository_label_map(owner, repo, client):
    """Fetch label map using the test label manager."""
    return await _label_manager.get_label_map(owner, repo, client)


def _inject_label_validation_wrapper(tool):
    """Inject label validation wrapper into a tool."""
    return _inject_label_validation_wrapper_impl(_label_manager, tool)


def _maybe_wrap_labels(component):
    """Maybe wrap labels parameter with validation."""
    return _maybe_wrap_labels_impl(_label_manager, component)


def _update_labels_schema(component):
    """Update labels schema."""
    return _update_labels_schema_impl(component)


def _customize_component(route, component):
    """Customize component with annotations."""
    return _customize_component_impl(route, component, _label_manager)


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


class TestLabelValidationWrapper:
    """Tests for the label validation/conversion wrapper."""

    @pytest.fixture
    def mock_tool(self):
        """Create a mock tool with a labels parameter."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "integer", "format": "int64"}},
                "title": {"type": "string"},
            },
            "required": ["owner", "repo", "title"],
        }
        tool.run = AsyncMock(return_value={"success": True})
        tool._client = MagicMock()  # needed for label lookup
        return tool

    @pytest.fixture
    def label_map(self):
        """Sample label map."""
        return {
            "bug": {"id": 1, "name": "Bug"},
            "enhancement": {"id": 2, "name": "Enhancement"},
            "security": {"id": 3, "name": "Security"},
        }

    async def test_wrapper_with_ids_only(self, mock_tool):
        """If all labels are IDs, wrapper should pass through unchanged."""
        original_run = mock_tool.run
        wrapped = _inject_label_validation_wrapper(mock_tool)

        arguments = {"owner": "o", "repo": "r", "labels": [1, 2, 3], "title": "Test"}
        result = await wrapped(arguments)

        original_run.assert_called_once_with(arguments)
        assert result == {"success": True}

    async def test_wrapper_converts_string_names_to_ids(self, mock_tool, label_map):
        """String labels should be converted to IDs."""
        original_run = mock_tool.run
        # Patch the label manager's get_label_map to return our label map
        with patch.object(_label_manager, "get_label_map", return_value=label_map):
            wrapped = _inject_label_validation_wrapper(mock_tool)

            arguments = {"owner": "o", "repo": "r", "labels": ["bug", "security"], "title": "Test"}
            result = await wrapped(arguments)

            # Should call underlying tool with IDs [1, 3]
            expected_call = {"owner": "o", "repo": "r", "labels": [1, 3], "title": "Test"}
            original_run.assert_called_once_with(expected_call)
            assert result == {"success": True}

    async def test_wrapper_mixed_ids_and_names(self, mock_tool, label_map):
        """Mixed integer IDs and string names should work."""
        original_run = mock_tool.run
        with patch.object(_label_manager, "get_label_map", return_value=label_map):
            wrapped = _inject_label_validation_wrapper(mock_tool)

            arguments = {
                "owner": "o",
                "repo": "r",
                "labels": [5, "bug", 10, "enhancement"],
                "title": "Test",
            }
            result = await wrapped(arguments)

            expected_call = {"owner": "o", "repo": "r", "labels": [5, 1, 10, 2], "title": "Test"}
            original_run.assert_called_once_with(expected_call)
            assert result == {"success": True}

    async def test_wrapper_rejects_unknown_label_with_helpful_error(self, mock_tool, label_map):
        """Unknown label names should produce clear error with suggestions."""
        with patch.object(_label_manager, "get_label_map", return_value=label_map):
            wrapped = _inject_label_validation_wrapper(mock_tool)

            arguments = {"owner": "o", "repo": "r", "labels": ["unknown", "bug"], "title": "Test"}

            with pytest.raises(ValueError) as excinfo:
                await wrapped(arguments)

            error_msg = str(excinfo.value)
            assert "Unknown label(s): ['unknown']" in error_msg
            assert "Available labels: bug, enhancement, security" in error_msg
            assert "list_labels" in error_msg

    async def test_wrapper_case_insensitive_matching(self, mock_tool, label_map):
        """Label name matching should be case-insensitive."""
        original_run = mock_tool.run
        with patch.object(_label_manager, "get_label_map", return_value=label_map):
            wrapped = _inject_label_validation_wrapper(mock_tool)

            arguments = {
                "owner": "o",
                "repo": "r",
                "labels": ["BUG", "Enhancement"],
                "title": "Test",
            }
            result = await wrapped(arguments)

            expected_call = {"owner": "o", "repo": "r", "labels": [1, 2], "title": "Test"}
            original_run.assert_called_once_with(expected_call)
            assert result == {"success": True}

    async def test_wrapper_no_labels_parameter(self, mock_tool):
        """If no labels provided, should pass through unchanged."""
        original_run = mock_tool.run
        wrapped = _inject_label_validation_wrapper(mock_tool)

        arguments = {"owner": "o", "repo": "r", "title": "Test"}
        result = await wrapped(arguments)

        original_run.assert_called_once_with(arguments)
        assert result == {"success": True}

    async def test_wrapper_empty_labels_list(self, mock_tool, label_map):
        """Empty labels list should pass through."""
        original_run = mock_tool.run
        with patch.object(_label_manager, "get_label_map", return_value=label_map):
            wrapped = _inject_label_validation_wrapper(mock_tool)

            arguments = {"owner": "o", "repo": "r", "labels": [], "title": "Test"}
            result = await wrapped(arguments)

            original_run.assert_called_once_with(arguments)
            assert result == {"success": True}

    async def test_wrapper_fetches_labels_on_demand(self, mock_tool, label_map):
        """Should call label manager to fetch labels."""
        with patch.object(_label_manager, "get_label_map", return_value=label_map) as mock_get:
            wrapped = _inject_label_validation_wrapper(mock_tool)
            await wrapped({"owner": "o", "repo": "r", "labels": ["bug"], "title": "Test"})

            mock_get.assert_called_once_with("o", "r", mock_tool._client)


class TestMaybeWrapLabels:
    """Tests for the _maybe_wrap_labels helper."""

    def test_wraps_tool_with_labels_param(self):
        """Tool with 'labels' array parameter should be wrapped."""
        tool = MagicMock(spec=["parameters", "run", "__doc__"])
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "integer"}},
                "title": {"type": "string"},
            }
        }
        tool.run = MagicMock()
        tool.__doc__ = "Create issue"

        _maybe_wrap_labels(tool)

        # After wrapping, tool.run should be a coroutine function (the wrapper)
        assert asyncio.iscoroutinefunction(tool.run)

    def test_does_not_wrap_without_labels(self):
        """Tool without 'labels' parameter should remain unchanged."""
        tool = MagicMock(spec=["parameters", "run"])
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string"},
            }
        }
        original_run = tool.run

        _maybe_wrap_labels(tool)

        # Should not be changed
        assert tool.run is original_run

    def test_does_not_wrap_if_labels_not_array(self):
        """Tool with 'labels' parameter that is not array type should not be wrapped."""
        tool = MagicMock(spec=["parameters", "run"])
        tool.parameters = {
            "properties": {
                "labels": {"type": "string"},  # Not an array
            }
        }
        original_run = tool.run

        _maybe_wrap_labels(tool)

        assert tool.run is original_run

    def test_enhances_description_with_guidance(self):
        """Tool with labels should have guidance appended to docstring."""
        tool = MagicMock(spec=["parameters", "run", "__doc__"])
        tool.parameters = {
            "properties": {
                "labels": {"type": "array", "items": {"type": "integer"}},
            }
        }
        tool.run = MagicMock()
        tool.__doc__ = "Create a release."

        _maybe_wrap_labels(tool)

        assert LABEL_GUIDANCE.strip() in tool.__doc__

    def test_does_not_duplicate_guidance(self):
        """Guidance should not be added twice."""
        tool = MagicMock(spec=["parameters", "run", "__doc__"])
        tool.parameters = {
            "properties": {
                "labels": {"type": "array", "items": {"type": "integer"}},
            }
        }
        tool.run = MagicMock()
        # Pre-populate with the exact guidance we would add
        tool.__doc__ = f"Create issue.{LABEL_GUIDANCE}"

        _maybe_wrap_labels(tool)

        # Should not append again
        assert tool.__doc__.count(LABEL_GUIDANCE) == 1


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
        """_customize_component should trigger schema update for tools with labels."""
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

        _customize_component(route, tool)

        # Verify schema was updated
        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]
