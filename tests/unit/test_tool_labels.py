"""Unit tests for label conversion and formatting."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.tools.labels import _convert_labels, _format_available_labels

class TestFormatAvailableLabels:
    """Tests for _format_available_labels."""

    def test_groups_labels_by_prefix(self):
        """Labels with same prefix should be grouped together."""
        labels = ["type/bug", "priority/high", "type/feature", "priority/low", "status/triage"]
        result = _format_available_labels(labels)
        assert "type/bug, type/feature" in result
        assert "priority/high, priority/low" in result
        assert "status/triage" in result

    def test_labels_without_prefix(self):
        """Labels without a '/' should be grouped under empty prefix."""
        labels = ["urgent", "type/bug", "wontfix"]
        result = _format_available_labels(labels)
        assert "urgent, wontfix" in result
        assert "type/bug" in result

    def test_single_label(self):
        """Single label should produce one line."""
        result = _format_available_labels(["type/bug"])
        assert result == "  - type/bug"

    def test_empty_list(self):
        """Empty list should produce empty string."""
        result = _format_available_labels([])
        assert result == ""


class TestConvertLabels:
    """Tests for _convert_labels."""

    @pytest.fixture
    def _gitea_client(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_converts_known_string_labels_to_ids(self, _gitea_client):
        """Known string label names should be converted to integer IDs."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
            "type/feature": {"id": 2, "name": "type/feature"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug", "type/feature"]}
        await _convert_labels(kwargs, True, label_manager, _gitea_client)

        assert kwargs["labels"] == [1, 2]

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_unknown_labels(self, _gitea_client):
        """Unknown label names should raise ValidationError with available labels."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
            "type/feature": {"id": 2, "name": "type/feature"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/nonexistent"]}
        with pytest.raises(ValidationError) as excinfo:
            await _convert_labels(kwargs, True, label_manager, _gitea_client)

        msg = str(excinfo.value)
        assert "type/nonexistent" in msg
        assert "test-owner/test-repo" in msg
        assert "type/bug" in msg
        assert "type/feature" in msg
        assert excinfo.value.field == "labels"

    @pytest.mark.asyncio
    async def test_preserves_integer_labels(self):
        """Integer labels should be passed through unchanged."""
        label_manager = AsyncMock(spec=LabelManager)

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": [1, 2, 3]}
        await _convert_labels(kwargs, True, label_manager)

        assert kwargs["labels"] == [1, 2, 3]
        label_manager.get_label_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_has_labels_is_false(self):
        """When has_labels is False, no conversion should happen."""
        kwargs = {"labels": ["type/bug"]}
        await _convert_labels(kwargs, False, MagicMock())
        assert kwargs["labels"] == ["type/bug"]

    @pytest.mark.asyncio
    async def test_skips_when_labels_not_in_kwargs(self):
        """When labels key is missing from kwargs, no conversion should happen."""
        kwargs = {"owner": "test-owner", "repo": "test-repo"}
        await _convert_labels(kwargs, True, MagicMock())
        assert "labels" not in kwargs

    @pytest.mark.asyncio
    async def test_skips_when_owner_missing(self):
        """When owner is missing, no conversion should happen."""
        label_manager = AsyncMock(spec=LabelManager)

        kwargs = {"repo": "test-repo", "labels": ["type/bug"]}
        await _convert_labels(kwargs, True, label_manager, AsyncMock())
        assert kwargs["labels"] == ["type/bug"]
        label_manager.get_label_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_gitea_client_missing(self):
        """When gitea_client is None, no conversion should happen."""
        label_manager = AsyncMock(spec=LabelManager)

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug"]}
        await _convert_labels(kwargs, True, label_manager, gitea_client=None)
        assert kwargs["labels"] == ["type/bug"]
        label_manager.get_label_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_mixed_strings_and_integers(self, _gitea_client):
        """Mixed string and integer labels should all be converted/preserved."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug", 42]}
        await _convert_labels(kwargs, True, label_manager, _gitea_client)

        assert kwargs["labels"] == [1, 42]

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, _gitea_client):
        """Label matching should be case-insensitive."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "kind/enhancement": {"id": 5, "name": "Kind/Enhancement"},
        }

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["Kind/Enhancement"]}
        await _convert_labels(kwargs, True, label_manager, _gitea_client)

        assert kwargs["labels"] == [5]

    @pytest.mark.asyncio
    async def test_formats_error_with_grouped_labels(self, _gitea_client):
        """Error message should group available labels by prefix."""
        label_manager = AsyncMock(spec=LabelManager)
        label_manager.get_label_map.return_value = {
            "type/bug": {"id": 1, "name": "type/bug"},
            "type/feature": {"id": 2, "name": "type/feature"},
            "priority/high": {"id": 3, "name": "priority/high"},
            "priority/low": {"id": 4, "name": "priority/low"},
        }

        kwargs = {"owner": "my-org", "repo": "my-repo", "labels": ["bad/label"]}
        with pytest.raises(ValidationError) as excinfo:
            await _convert_labels(kwargs, True, label_manager, _gitea_client)

        msg = str(excinfo.value)
        assert "my-org/my-repo" in msg
        assert "  - priority/high, priority/low" in msg
        assert "  - type/bug, type/feature" in msg
