"""Tests for tests/helpers/mock_tool.py — mock creation helpers.

Covers ``make_async_mock`` and ``make_magic_mock``: happy paths,
mock attribute access with spec, and edge cases (None spec).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.helpers.mock_tool import make_async_mock, make_magic_mock

# ---------------------------------------------------------------------------
# Dummy spec types for testing
# ---------------------------------------------------------------------------


class _DummyService:
    """A class with async and sync methods, used as a spec target."""

    async def fetch(self, key: str) -> dict[str, Any]:
        return {}

    def transform(self, value: int) -> str:
        return ""


async def _dummy_async_fn(key: str) -> list[int]:
    return []


def _dummy_sync_fn(value: int) -> str:
    return ""


# ---------------------------------------------------------------------------
# make_async_mock
# ---------------------------------------------------------------------------


class TestMakeAsyncMock:
    def test_returns_async_mock(self) -> None:
        """Without spec, returns a plain AsyncMock."""
        mock = make_async_mock()
        assert isinstance(mock, AsyncMock)

    def test_method_return_value_accessible(self) -> None:
        """Mock method's return_value is settable and readable."""
        mock = make_async_mock(_DummyService)
        mock.fetch.return_value = {"result": "ok"}
        assert mock.fetch.return_value == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_method_side_effect_accessible(self) -> None:
        """Mock method's side_effect is settable."""
        mock = make_async_mock(_DummyService)
        mock.fetch.side_effect = ValueError("fail")

        with pytest.raises(ValueError, match="fail"):
            await mock.fetch()

    @pytest.mark.asyncio
    async def test_assert_awaited(self) -> None:
        """Mock method supports assert_awaited_once_with."""
        mock = make_async_mock(_DummyService)
        await mock.fetch("key")
        mock.fetch.assert_awaited_once_with("key")

    def test_assert_called_on_sync_method(self) -> None:
        """Mock sync method supports assert_called_once_with."""
        mock = make_async_mock(_DummyService)
        mock.transform(42)
        mock.transform.assert_called_once_with(42)

    def test_with_none_spec(self) -> None:
        """Passing spec=None explicitly is valid and returns AsyncMock."""
        mock = make_async_mock(None)
        assert isinstance(mock, AsyncMock)
        # Arbitrary attribute returns a new mock
        assert isinstance(mock.arbitrary_attr, (AsyncMock, MagicMock))

    def test_assert_not_called(self) -> None:
        """Mock method supports assert_not_called."""
        mock = make_async_mock(_DummyService)
        mock.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# make_magic_mock
# ---------------------------------------------------------------------------


class TestMakeMagicMock:
    def test_returns_magic_mock(self) -> None:
        """Without spec, returns a plain MagicMock."""
        mock = make_magic_mock()
        assert isinstance(mock, MagicMock)

    def test_method_return_value_accessible(self) -> None:
        """Mock callable's return_value is settable and readable."""
        mock = make_magic_mock(_dummy_sync_fn)
        mock.return_value = "hello"
        assert mock() == "hello"

    def test_side_effect_accessible(self) -> None:
        """Mock callable's side_effect is settable."""
        mock = make_magic_mock(_dummy_sync_fn)
        mock.side_effect = ValueError("fail")
        with pytest.raises(ValueError, match="fail"):
            mock()

    def test_assert_called_once_with(self) -> None:
        """Mock callable supports assert_called_once_with."""
        mock = make_magic_mock(_dummy_sync_fn)
        mock(42)
        mock.assert_called_once_with(42)

    def test_with_none_spec(self) -> None:
        """Passing spec=None explicitly is valid and returns MagicMock."""
        mock = make_magic_mock(None)
        assert isinstance(mock, MagicMock)
        assert isinstance(mock.arbitrary_attr, (AsyncMock, MagicMock))

    def test_assert_not_called(self) -> None:
        """Mock callable supports assert_not_called."""
        mock = make_magic_mock(_dummy_sync_fn)
        mock.assert_not_called()

    def test_spec_is_class(self) -> None:
        """Spec of a class preserves mock attribute access on its methods."""
        mock = make_magic_mock(_DummyService)
        mock.transform.return_value = "converted"
        assert mock.transform("ignored") == "converted"
        mock.transform.assert_called_once_with("ignored")
