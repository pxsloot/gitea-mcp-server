"""Regression tests for issue #574: display pipeline mid-failure error recovery.

The domain formatters must handle unexpected data shapes gracefully instead
of crashing.  The pipeline-level try/except lives in the single result pipeline
(``tools/result_pipeline.py`` — see ``TestErrorRecovery`` there); these tests
lock the formatter-side guards that make the recovery path reachable.

Scenarios covered:
  1. Issues formatter with non-dict items (strings) → no AttributeError
  2. Labels formatter with non-dict items (full detail) → no AttributeError
  3. User formatter with non-dict input → no TypeError
  4. Empty list to issues formatter → no TypeError
  5. Pulls formatter with non-dict items → no crash (generic fallback)
  6. Release formatter with non-dict items → no crash (generic fallback)
  7. format_as_markdown edge cases (None, bool, mixed lists)
"""

from typing import Any

from gitea_mcp_server.format import format_as_markdown
from gitea_mcp_server.tools.display import (
    _format_issues_markdown,
    _format_labels_markdown,
    _format_user_markdown,
)


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
            data,
            detail="full",
            extra={"owner": "test", "repo": "test"},
        )
        assert result.strip() != ""
        assert "Labels for test/test" in result
        assert "- bug" in result
        assert "- feature" in result

    def test_non_dict_items_concise_ok(self) -> None:
        """Non-dict items in concise mode is already safe."""
        data = ["$ref:Label[2]"]
        result = _format_labels_markdown(
            data,
            detail="concise",
            extra={"owner": "o", "repo": "r"},
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
