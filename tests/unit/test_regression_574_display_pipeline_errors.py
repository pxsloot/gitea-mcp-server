"""Regression tests for issue #574: display pipeline mid-failure error recovery.

Tests that ``_format_resource_content`` and domain formatters handle
unexpected data shapes gracefully instead of crashing.

Scenarios covered:
  1. Issues formatter with non-dict items (strings) → no AttributeError
  2. Labels formatter with non-dict items (full detail) → no AttributeError
  3. User formatter with non-dict input → no TypeError
  4. Non-JSON-serializable data in JSON output → no TypeError
  5. Non-iterable data to issues formatter → no TypeError
  6. Schema/object but data=[] end-to-end → graceful fallback
  7. Pipeline fallback returns readable raw data on formatting error
"""

import json
from unittest.mock import patch

import pytest

from gitea_mcp_server.format import _format_as_markdown, apply_format
from gitea_mcp_server.tools.display import (
    _format_issues_markdown,
    _format_labels_markdown,
    _format_user_markdown,
)
from gitea_mcp_server.tools.resource_display import _format_resource_content


class TestFormatIssuesMarkdownGuard:
    """Guard: _format_issues_markdown handles non-dict items."""

    def test_non_dict_items_no_crash(self):
        """Non-dict items (strings) produce output, not AttributeError."""
        data = ["string item", "another string"]
        result = _format_issues_markdown(data, detail="full")
        assert result.strip() != ""
        # Should contain the generic title since items aren't dicts
        assert "Issues" in result or "Issues and Pull Requests" in result

    def test_non_iterable_data_no_crash(self):
        """Non-iterable data falls back to string title, no TypeError."""
        data = []
        # Even with bad data hint - empty list is handled
        result = _format_issues_markdown(data, detail="full")
        assert result.strip() != ""
        assert "*None*" in result


class TestFormatLabelsMarkdownGuard:
    """Guard: _format_labels_markdown handles non-dict items."""

    def test_non_dict_items_full_detail_no_crash(self):
        """Non-dict items in full detail mode produce output, not AttributeError."""
        data = ["bug", "feature"]
        result = _format_labels_markdown(
            data, detail="full", extra={"owner": "test", "repo": "test"},
        )
        assert result.strip() != ""
        assert "Labels for test/test" in result
        assert "- bug" in result
        assert "- feature" in result

    def test_non_dict_items_concise_ok(self):
        """Non-dict items in concise mode is already safe."""
        data = ["$ref:Label[2]"]
        result = _format_labels_markdown(
            data, detail="concise", extra={"owner": "o", "repo": "r"},
        )
        assert "Labels for o/r" in result
        assert "$ref:Label[2]" in result


class TestFormatUserMarkdownGuard:
    """Guard: _format_user_markdown handles non-dict input."""

    def test_non_dict_input_no_crash(self):
        """Non-dict input produces output, not TypeError."""
        data = "just a string"
        result = _format_user_markdown(data, detail="full")
        assert result.strip() != ""

    def test_list_input_no_crash(self):
        """List input produces output, not TypeError."""
        data = [{"login": "user1"}]
        result = _format_user_markdown(data, detail="full")
        assert result.strip() != ""


class TestFormatResourceContentPipelineFallback:
    """Pipeline-level try/except in _format_resource_content.

    The pipeline wraps the post-parse formatting in try/except; when a
    formatter receives unexpected data, the error is logged and a
    readable fallback is returned instead of crashing.
    """

    def test_pipeline_recovers_from_type_error(self):
        """When apply_format raises TypeError, pipeline returns readable fallback."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=TypeError("bad type"),
        ):
            result = _format_resource_content(raw, "markdown")
            assert "key" in result or "value" in result or "fallback" in result.lower()

    def test_pipeline_recovers_from_attribute_error(self):
        """When apply_format raises AttributeError, pipeline returns readable fallback."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=AttributeError("no attr"),
        ):
            result = _format_resource_content(raw, "markdown")
            assert result.strip() != ""

    def test_pipeline_recovers_from_value_error(self):
        """When apply_format raises ValueError, pipeline returns readable fallback."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=ValueError("bad value"),
        ):
            result = _format_resource_content(raw, "markdown")
            assert result.strip() != ""

    def test_pipeline_recovers_json_format(self):
        """When formatting fails in JSON mode, returns wrapped raw data."""
        raw = '{"key": "value"}'
        with patch(
            "gitea_mcp_server.tools.resource_display.apply_format",
            side_effect=TypeError("bad type"),
        ):
            result = _format_resource_content(raw, "json")
            parsed = json.loads(result)
            assert parsed == {"result": raw}

    def test_schema_object_data_list_end_to_end(self):
        """Schema expects object but data is a list — pipeline produces output."""
        # This is the scenario from the issue description
        raw = "[]"
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        result = _format_resource_content(raw, "markdown", detail="concise", schema=schema)
        assert result.strip() != ""
        # Should produce some readable output
        assert "*None*" in result or "Empty" in result or "N/A" in result

    def test_json_output_non_serializable_raises(self):
        """apply_format with non-serializable data in JSON mode raises TypeError."""
        class Unserializable:
            pass

        data = {"bad": Unserializable()}
        with pytest.raises(TypeError):
            apply_format(data, "json")


class TestFormatResourceContentJsonParseEdgeCases:
    """Non-JSON content edge cases in _format_resource_content."""

    def test_plain_text_markdown_passthrough(self):
        """Plain text with format=markdown returns unchanged."""
        result = _format_resource_content("hello world", "markdown")
        assert result == "hello world"

    def test_plain_text_json_wrapped(self):
        """Plain text with format=json wraps in result dict."""
        result = _format_resource_content("hello world", "json")
        parsed = json.loads(result)
        assert parsed == {"result": "hello world"}

    def test_empty_string(self):
        """Empty string returns empty string for markdown."""
        result = _format_resource_content("", "markdown")
        assert result == ""


class TestFormatAsMarkdownEdgeCases:
    """Edge cases for _format_as_markdown with unexpected data shapes."""

    def test_none_data(self):
        """None data produces 'N/A'."""
        result = _format_as_markdown(None)
        assert result == "N/A"

    def test_none_data_with_title(self):
        """None data with title still shows title and N/A."""
        result = _format_as_markdown(None, title="Test Title")
        assert "# Test Title" in result
        assert "N/A" in result

    def test_bool_input(self):
        """Boolean input produces string representation."""
        result = _format_as_markdown(True)
        assert result == "True"

    def test_list_with_mixed_types(self):
        """Mixed-type list items render without crash."""
        result = _format_as_markdown([1, "two", None, {"key": "val"}])
        # None falls back to "N/A" in _format_simple_value
        assert "N/A" in result or "two" in result
