"""Tests for tests/helpers/mcp_results.py.

Covers all 10 helpers: happy paths and error paths for each.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mcp.types import TextContent

from tests.helpers.mcp_results import (
    assert_call_success,
    assert_low_level_success,
    assert_resource_success,
    extract_low_level_text,
    extract_resource_text,
    extract_text_content,
    extract_text_from_content_items,
    get_low_level_structured,
    get_structured,
    parse_json_content,
)

# ---------------------------------------------------------------------------
# Helpers: SimpleNamespace factories
# ---------------------------------------------------------------------------


def _make_result(
    is_error: bool = False,
    content: list | None = None,
    structured_content: object = None,
    data: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        isError=is_error,
        content=content or [],
        structured_content=structured_content,
        data=data,
    )


def _make_low_level_result(
    is_error: bool = False,
    content: list | None = None,
    structured_content: object = None,
) -> SimpleNamespace:
    """Simulates a low-level JSON-RPC response with .root."""
    root = SimpleNamespace(
        isError=is_error,
        content=content,
        structuredContent=structured_content,
    )
    return SimpleNamespace(root=root)


def _make_resource_result(contents: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(contents=contents)


# ---------------------------------------------------------------------------
# extract_text_content
# ---------------------------------------------------------------------------


class TestExtractTextContent:
    def test_happy_path(self) -> None:
        content = [TextContent(text="hello", type="text")]
        assert extract_text_content(content) == "hello"

    def test_empty_raises(self) -> None:
        with pytest.raises(AssertionError, match="at least one content"):
            extract_text_content([])

    def test_non_text_content_raises(self) -> None:
        content = [MagicMock(spec=object)]  # not TextContent
        with pytest.raises(AssertionError, match="Expected TextContent"):
            extract_text_content(content)

    def test_reads_only_first_item(self) -> None:
        content = [
            TextContent(text="first", type="text"),
            TextContent(text="second", type="text"),
        ]
        assert extract_text_content(content) == "first"


# ---------------------------------------------------------------------------
# extract_text_from_content_items
# ---------------------------------------------------------------------------


class TestExtractTextFromContentItems:
    def test_happy_path(self) -> None:
        content = [
            TextContent(text="hello ", type="text"),
            TextContent(text="world", type="text"),
        ]
        assert extract_text_from_content_items(content) == "hello world"

    def test_empty_returns_empty_string(self) -> None:
        assert extract_text_from_content_items([]) == ""

    def test_non_text_content_raises(self) -> None:
        content = [MagicMock(spec=object)]
        with pytest.raises(AssertionError, match="Expected TextContent"):
            extract_text_from_content_items(content)

    def test_single_item(self) -> None:
        content = [TextContent(text="only", type="text")]
        assert extract_text_from_content_items(content) == "only"


# ---------------------------------------------------------------------------
# assert_call_success
# ---------------------------------------------------------------------------


class TestAssertCallSuccess:
    def test_happy_path(self) -> None:
        result = _make_result(is_error=False)
        assert_call_success(result)  # should not raise

    def test_error_raises(self) -> None:
        result = _make_result(is_error=True, content=[TextContent(text="fail", type="text")])
        with pytest.raises(AssertionError, match="Tool call failed"):
            assert_call_success(result)

    def test_missing_is_error_attr_raises(self) -> None:
        result = object()
        with pytest.raises(AssertionError, match="Expected result with .isError"):
            assert_call_success(result)

    def test_empty_content(self) -> None:
        """Error message handles empty content gracefully."""
        result = _make_result(is_error=True)
        with pytest.raises(AssertionError, match="Tool call failed"):
            assert_call_success(result)


# ---------------------------------------------------------------------------
# get_structured
# ---------------------------------------------------------------------------


class TestGetStructured:
    def test_happy_path(self) -> None:
        result = _make_result(structured_content={"result": [1, 2, 3]})
        assert get_structured(result) == {"result": [1, 2, 3]}

    def test_none_raises(self) -> None:
        result = _make_result(structured_content=None)
        with pytest.raises(AssertionError, match="Expected structured_content to be present"):
            get_structured(result)

    def test_missing_attr_raises(self) -> None:
        result = object()
        with pytest.raises(AssertionError, match="Expected structured_content"):
            get_structured(result)

    def test_non_dict_raises(self) -> None:
        result = _make_result(structured_content="not a dict")
        with pytest.raises(AssertionError, match="Expected dict"):
            get_structured(result)


# ---------------------------------------------------------------------------
# parse_json_content
# ---------------------------------------------------------------------------


class TestParseJsonContent:
    def test_happy_path(self) -> None:
        result = _make_result(content=[TextContent(text='{"key": "val"}', type="text")])
        assert parse_json_content(result) == {"key": "val"}

    def test_empty_content_raises(self) -> None:
        result = _make_result(content=[])
        with pytest.raises(AssertionError, match="at least one content"):
            parse_json_content(result)


# ---------------------------------------------------------------------------
# assert_low_level_success
# ---------------------------------------------------------------------------


class TestAssertLowLevelSuccess:
    def test_happy_path(self) -> None:
        result = _make_low_level_result(is_error=False)
        assert_low_level_success(result)  # should not raise

    def test_error_raises(self) -> None:
        result = _make_low_level_result(
            is_error=True,
            content=[TextContent(text="failed", type="text")],
        )
        with pytest.raises(AssertionError, match="Low-level call failed"):
            assert_low_level_success(result)

    def test_missing_root_raises(self) -> None:
        result = object()
        with pytest.raises(AssertionError, match="Expected result with .root"):
            assert_low_level_success(result)

    def test_error_no_content(self) -> None:
        """Error message works when .root.content is None."""
        result = _make_low_level_result(is_error=True, content=None)
        with pytest.raises(AssertionError, match="Low-level call failed"):
            assert_low_level_success(result)


# ---------------------------------------------------------------------------
# extract_low_level_text
# ---------------------------------------------------------------------------


class TestExtractLowLevelText:
    def test_happy_path(self) -> None:
        result = _make_low_level_result(
            content=[TextContent(text="extracted", type="text")],
        )
        assert extract_low_level_text(result) == "extracted"

    def test_missing_root_raises(self) -> None:
        result = object()
        with pytest.raises(AssertionError, match="Expected result with .root"):
            extract_low_level_text(result)

    def test_missing_root_content_raises(self) -> None:
        result = _make_low_level_result(content=None)
        with pytest.raises(AssertionError, match="Expected .root.content to be present"):
            extract_low_level_text(result)


# ---------------------------------------------------------------------------
# get_low_level_structured
# ---------------------------------------------------------------------------


class TestGetLowLevelStructured:
    def test_happy_path(self) -> None:
        result = _make_low_level_result(structured_content={"key": "val"})
        assert get_low_level_structured(result) == {"key": "val"}

    def test_missing_root_raises(self) -> None:
        result = object()
        with pytest.raises(AssertionError, match="Expected result with .root"):
            get_low_level_structured(result)

    def test_none_structured_content_raises(self) -> None:
        result = _make_low_level_result(structured_content=None)
        with pytest.raises(AssertionError, match="Expected .root.structuredContent"):
            get_low_level_structured(result)

    def test_non_dict_raises(self) -> None:
        result = _make_low_level_result(structured_content="bad")
        with pytest.raises(AssertionError, match="Expected dict"):
            get_low_level_structured(result)


# ---------------------------------------------------------------------------
# assert_resource_success
# ---------------------------------------------------------------------------


class TestAssertResourceSuccess:
    def test_happy_path(self) -> None:
        result = _make_resource_result(contents=[SimpleNamespace(content="data")])
        assert_resource_success(result)  # should not raise

    def test_missing_contents_raises(self) -> None:
        result = _make_resource_result(contents=None)
        with pytest.raises(AssertionError, match="Expected result with .contents"):
            assert_resource_success(result)

    def test_empty_contents_raises(self) -> None:
        result = _make_resource_result(contents=[])
        with pytest.raises(AssertionError, match="at least one resource content"):
            assert_resource_success(result)


# ---------------------------------------------------------------------------
# extract_resource_text
# ---------------------------------------------------------------------------


class TestExtractResourceText:
    def test_happy_path(self) -> None:
        result = _make_resource_result(contents=[SimpleNamespace(content="text content")])
        assert extract_resource_text(result) == "text content"

    def test_missing_contents_raises(self) -> None:
        result = _make_resource_result(contents=None)
        with pytest.raises(AssertionError, match="Expected result with .contents"):
            extract_resource_text(result)

    def test_empty_contents_raises(self) -> None:
        result = _make_resource_result(contents=[])
        with pytest.raises(AssertionError, match="at least one resource content"):
            extract_resource_text(result)
