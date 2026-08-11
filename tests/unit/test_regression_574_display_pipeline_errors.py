"""Regression tests for issue #574: display pipeline mid-failure error recovery.

Tests that ``format_resource_content`` and domain formatters handle
unexpected data shapes gracefully instead of crashing.

Scenarios covered:
  1. Issues formatter with non-dict items (strings) → no AttributeError
  2. Labels formatter with non-dict items (full detail) → no AttributeError
  3. User formatter with non-dict input → no TypeError
  4. Non-JSON-serializable data in JSON output → no TypeError (documents
     raw ``apply_format`` behaviour before pipeline catches it)
  5. Empty list to issues formatter → no TypeError
  6. Schema/object but data=[] end-to-end → graceful fallback
  7. Pipeline fallback returns readable raw data on formatting error
  8. Pulls formatter with non-dict items → no crash (generic fallback)
  9. Release formatter with non-dict items → no crash (generic fallback)
"""

import json
from typing import Any
from unittest.mock import patch

import pytest

from gitea_mcp_server.format import apply_format, format_as_markdown
from gitea_mcp_server.tools.display import (
    _format_issues_markdown,
    _format_labels_markdown,
    _format_user_markdown,
)
from gitea_mcp_server.tools.resource_display import format_resource_content


class TestFormatIssuesMarkdownGuard:
    """Guard: _format_issues_markdown handles non-dict items."""

    def test_non_dict_items_no_crash(self) -> None:
        """Non-dict items (strings) produce output, not AttributeError."""
        data = ["string item", "another string"]
        result = _format_issues_markdown(data, detail="full")
        assert result.strip() != ""
        # Should contain the generic title since items aren't dicts
        assert "Issues" in result or "Issues and Pull Requests" in result

    def test_empty_list_no_crash(self) -> None:
        """Empty list produces output, not TypeError or crash."""
        data: list[Any] = []
        result = _format_issues_markdown(data, detail="full")
        assert result.strip() != ""
        assert "_(empty)_" in result


class TestFormatLabelsMarkdownGuard:
    """Guard: _format_labels_markdown handles non-dict items."""

    def test_non_dict_items_full_detail_no_crash(self) -> None:
        """Non-dict items in full detail mode produce output, not AttributeError."""
        data = ["bug", "feature"]
        result = _format_labels_markdown(
            data, detail="full", extra={"owner": "test", "repo": "test"},
        )
        assert result.strip() != ""
        assert "Labels for test/test" in result
        assert "- bug" in result
        assert "- feature" in result

    def test_non_dict_items_concise_ok(self) -> None:
        """Non-dict items in concise mode is already safe."""
        data = ["$ref:Label[2]"]
        result = _format_labels_markdown(
            data, detail="concise", extra={"owner": "o", "repo": "r"},
        )
        assert "Labels for o/r" in result
        assert "$ref:Label[2]" in result


class TestFormatUserMarkdownGuard:
    """Guard: _format_user_markdown handles non-dict input."""

    def test_non_dict_input_no_crash(self) -> None:
        """Non-dict input produces output, not TypeError."""
        data = "just a string"
        result = _format_user_markdown(data, detail="full")
        assert result.strip() != ""

    def test_list_input_no_crash(self) -> None:
        """List input produces output, not TypeError."""
        data = [{"login": "user1"}]
        result = _format_user_markdown(data, detail="full")
        assert result.strip() != ""


class TestFormatResourceContentPipelineFallback:
    """Pipeline-level try/except in format_resource_content.

    The pipeline wraps the post-parse formatting in try/except; when a
    formatter receives unexpected data, the error is logged and a
    readable fallback is returned instead of crashing.
    """

    def test_pipeline_recovers_from_type_error(self) -> None:
        """When apply_format raises TypeError, pipeline returns readable fallback."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=TypeError("bad type"),
        ):
            result = format_resource_content(raw, "markdown")
            assert "key" in result or "value" in result or "fallback" in result.lower()

    def test_pipeline_recovers_from_attribute_error(self) -> None:
        """When apply_format raises AttributeError, pipeline returns readable fallback."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=AttributeError("no attr"),
        ):
            result = format_resource_content(raw, "markdown")
            assert result.strip() != ""

    def test_pipeline_recovers_from_value_error(self) -> None:
        """When apply_format raises ValueError, pipeline returns readable fallback."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=ValueError("bad value"),
        ):
            result = format_resource_content(raw, "markdown")
            assert result.strip() != ""

    def test_pipeline_recovers_json_format(self) -> None:
        """When formatting fails in JSON mode, returns wrapped raw data."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=TypeError("bad type"),
        ):
            result = format_resource_content(raw, "json")
            parsed = json.loads(result)
            assert parsed == {"result": raw}

    def test_schema_object_data_list_end_to_end(self) -> None:
        """Schema expects object but data is a list — pipeline produces output."""
        # This is the scenario from the issue description
        raw = "[]"
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        result = format_resource_content(raw, "markdown", detail="concise", schema=schema)
        assert result.strip() != ""
        # Should produce some readable output
        assert "_(empty)_" in result or "Empty" in result or "N/A" in result


class TestApplyFormatRaiseOnNonSerializable:
    """``apply_format`` raises TypeError for non-JSON-serializable data.

    This documents the raw behavior before the pipeline catches it.
    The pipeline-level catch in ``format_resource_content`` converts
    this into a readable fallback; this test verifies the underlying
    exception is raised when ``apply_format`` is called directly.
    """

    def test_json_output_non_serializable_raises(self) -> None:
        """Non-serializable data in JSON mode raises TypeError."""
        class Unserializable:
            pass

        data = {"bad": Unserializable()}
        with pytest.raises(TypeError):
            apply_format(data, "json")


class TestFormatResourceContentJsonParseEdgeCases:
    """Non-JSON content edge cases in format_resource_content."""

    def test_plain_text_markdown_passthrough(self) -> None:
        """Plain text with format=markdown returns unchanged."""
        result = format_resource_content("hello world", "markdown")
        assert result == "hello world"

    def test_plain_text_json_wrapped(self) -> None:
        """Plain text with format=json wraps in result dict."""
        result = format_resource_content("hello world", "json")
        parsed = json.loads(result)
        assert parsed == {"result": "hello world"}

    def test_empty_string(self) -> None:
        """Empty string returns empty string for markdown."""
        result = format_resource_content("", "markdown")
        assert result == ""


class TestFormatPullsMarkdownGuard:
    """Guard: _format_pulls_markdown handles unexpected data shapes safely."""

    def test_empty_list(self) -> None:
        """Empty list produces output, not crash."""
        from gitea_mcp_server.tools.display import _format_pulls_markdown
        result = _format_pulls_markdown([])
        assert "Pull Requests" in result

    def test_non_dict_items_safe(self) -> None:
        """Non-dict items render through generic fallback, no crash."""
        from gitea_mcp_server.tools.display import _format_pulls_markdown
        result = _format_pulls_markdown(["just", "strings"])
        assert result.strip() != ""


class TestFormatReleaseMarkdownGuard:
    """Guard: _format_release_markdown handles unexpected data shapes safely."""

    def test_empty_list(self) -> None:
        """Empty list produces output, not crash."""
        from gitea_mcp_server.tools.display import _format_release_markdown
        result = _format_release_markdown([])
        assert "Releases" in result

    def test_non_dict_items_safe(self) -> None:
        """Non-dict items render through generic fallback, no crash."""
        from gitea_mcp_server.tools.display import _format_release_markdown
        result = _format_release_markdown(["tag1", "tag2"])
        assert result.strip() != ""


class TestFormatAsMarkdownEdgeCases:
    """Edge cases for format_as_markdown with unexpected data shapes."""

    def test_none_data(self) -> None:
        """None data produces 'N/A'."""
        result = format_as_markdown(None)
        assert result == "N/A"

    def test_none_data_with_title(self) -> None:
        """None data with title still shows title and N/A."""
        result = format_as_markdown(None, title="Test Title")
        assert "# Test Title" in result
        assert "N/A" in result

    def test_bool_input(self) -> None:
        """Boolean input produces string representation."""
        result = format_as_markdown(True)
        assert result == "True"

    def test_list_with_mixed_types(self) -> None:
        """Mixed-type list items render without crash."""
        result = format_as_markdown([1, "two", None, {"key": "val"}])
        # None falls back to "N/A" in _format_simple_value
        assert "N/A" in result or "two" in result
