"""Unit tests for label conversion and formatting."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.tools.labels import _convert_labels


class TestConvertLabels:
    """Tests for _convert_labels (thin adapter → LabelService)."""

    @pytest.fixture
    def _gitea_client(self):
        return AsyncMock()

    @pytest.fixture
    def _label_service(self):
        return AsyncMock(spec=LabelService)

    @pytest.mark.asyncio
    async def test_converts_known_string_labels_to_ids(self, _gitea_client, _label_service):
        """Known string label names should be converted to integer IDs."""
        _label_service.validate_and_convert.return_value = [1, 2]

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug", "type/feature"]}
        await _convert_labels(kwargs, True, _label_service, _gitea_client)

        assert kwargs["labels"] == [1, 2]
        _label_service.validate_and_convert.assert_called_once_with(
            ["type/bug", "type/feature"], "test-owner", "test-repo", _gitea_client
        )

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_unknown_strings(self, _gitea_client, _label_service):
        """Unknown label names should raise ValidationError."""
        _label_service.validate_and_convert.side_effect = ValidationError(
            message="Unknown label name(s): ['type/nonexistent']", field="labels"
        )

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/nonexistent"]}
        with pytest.raises(ValidationError) as excinfo:
            await _convert_labels(kwargs, True, _label_service, _gitea_client)

        assert "type/nonexistent" in str(excinfo.value)
        assert excinfo.value.field == "labels"

    @pytest.mark.asyncio
    async def test_passes_through_valid_integers(self, _gitea_client, _label_service):
        """Valid integer IDs should pass through unchanged."""
        _label_service.validate_and_convert.return_value = [1, 2, 3]

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": [1, 2, 3]}
        await _convert_labels(kwargs, True, _label_service, _gitea_client)

        assert kwargs["labels"] == [1, 2, 3]
        _label_service.validate_and_convert.assert_called_once_with(
            [1, 2, 3], "test-owner", "test-repo", _gitea_client
        )

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_unknown_integers(self, _gitea_client, _label_service):
        """Unknown integer IDs should raise ValidationError."""
        _label_service.validate_and_convert.side_effect = ValidationError(
            message="Unknown label ID(s): [99999]", field="labels"
        )

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": [99999]}
        with pytest.raises(ValidationError) as excinfo:
            await _convert_labels(kwargs, True, _label_service, _gitea_client)

        assert "99999" in str(excinfo.value)
        assert excinfo.value.field == "labels"

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
    async def test_skips_when_labels_empty_list(self):
        """When labels is an empty list, no conversion should happen."""
        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": []}
        await _convert_labels(kwargs, True, MagicMock())
        assert kwargs["labels"] == []

    @pytest.mark.asyncio
    async def test_skips_when_owner_missing(self, _label_service):
        """When owner is missing, no conversion should happen."""
        kwargs = {"repo": "test-repo", "labels": ["type/bug"]}
        await _convert_labels(kwargs, True, _label_service, AsyncMock())
        assert kwargs["labels"] == ["type/bug"]
        _label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_gitea_client_missing(self, _label_service):
        """When gitea_client is None, no conversion should happen."""
        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug"]}
        await _convert_labels(kwargs, True, _label_service, gitea_client=None)
        assert kwargs["labels"] == ["type/bug"]
        _label_service.validate_and_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_mixed_strings_and_integers(self, _gitea_client, _label_service):
        """Mixed string and integer labels should all be converted/preserved."""
        _label_service.validate_and_convert.return_value = [1, 42]

        kwargs = {"owner": "test-owner", "repo": "test-repo", "labels": ["type/bug", 42]}
        await _convert_labels(kwargs, True, _label_service, _gitea_client)

        assert kwargs["labels"] == [1, 42]

    @pytest.mark.asyncio
    async def test_uses_org_as_fallback_for_owner(self, _gitea_client, _label_service):
        """When owner is absent but org is present, org should be used."""
        _label_service.validate_and_convert.return_value = [1]

        kwargs = {"org": "my-org", "repo": "test-repo", "labels": ["bug"]}
        await _convert_labels(kwargs, True, _label_service, _gitea_client)

        _label_service.validate_and_convert.assert_called_once_with(
            ["bug"], "my-org", "test-repo", _gitea_client
        )
