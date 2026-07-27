"""Tests for display formatters (tools/display.py).

Covers gap areas not yet exercised by resource tests:
    - call_formatter error path (unknown formatter)
    - _format_user_markdown created_at fallback
"""

import pytest

from gitea_mcp_server.tools.display import (
    _FORMATTER_META,
    _FORMATTERS,
    _build_labels_markdown,
    _format_labels_markdown,
    _format_user_markdown,
    call_formatter,
    register_formatter,
)


@pytest.fixture(autouse=True)
def _clean_formatters() -> None:
    """Save and restore the global formatter registry around each test.

    Tests register ad-hoc formatters via ``@register_formatter`` which
    mutates the module-level ``_FORMATTERS`` and ``_FORMATTER_META``
    dicts.  This fixture ensures each test starts with a clean slate
    and does not leak registrations to subsequent tests.
    """
    saved_formatters = dict(_FORMATTERS)
    saved_meta = dict(_FORMATTER_META)
    yield
    _FORMATTERS.clear()
    _FORMATTERS.update(saved_formatters)
    _FORMATTER_META.clear()
    _FORMATTER_META.update(saved_meta)


class TestCallFormatter:
    """Tests for call_formatter."""

    def test_unknown_formatter_raises(self):
        """Unknown formatter name raises ValueError."""
        with pytest.raises(ValueError, match="No formatter registered for 'nonexistent'"):
            call_formatter("nonexistent", {"key": "value"})

    def test_known_formatter_invoked(self):
        """Known formatter is called and returns expected output."""

        @register_formatter("test_formatter")
        def _test_fmt(data, *, detail="full"):
            return f"formatted: {data}"

        result = call_formatter("test_formatter", {"hello": "world"})
        assert "formatted:" in result

    def test_formatter_with_extra_needed(self):
        """Formatter registered with need_extra=True receives extra dict."""

        @register_formatter("test_extra", need_extra=True)
        def _test_extra(data, *, detail="full", extra=None):
            ctx = (extra or {}).get("ctx", "none")
            return f"data={data} ctx={ctx}"

        result = call_formatter(
            "test_extra", "val", extra={"ctx": "my_context"}
        )
        assert "ctx=my_context" in result

    def test_formatter_without_detail(self):
        """Formatter that ignores detail still works."""

        @register_formatter("test_no_detail")
        def _test_no_detail(data, **kwargs):
            return f"ok:{data}"

        result = call_formatter("test_no_detail", 42)
        assert result == "ok:42"


class TestFormatUserMarkdown:
    """Tests for _format_user_markdown edge cases."""

    def test_created_fallback(self):
        """When 'created_at' absent but 'created' present, use 'created'."""
        data = {
            "login": "testuser",
            "created": "2024-06-01T00:00:00Z",
            "type": "User",
        }
        result = _format_user_markdown(data)
        # Should show created_at in output (normalized from created)
        assert "2024-06-01" in result
        assert "| Created At |" in result or "created_at" in result.lower()

    def test_created_at_present_no_fallback(self):
        """When 'created_at' is present, 'created' is ignored."""
        data = {
            "login": "testuser",
            "created_at": "2024-01-01T00:00:00Z",
            "created": "2024-06-01T00:00:00Z",
        }
        result = _format_user_markdown(data)
        # Should use created_at, not created
        assert "2024-01-01" in result


class TestFormatLabelsMarkdownEdgeCases:
    """Edge cases for _format_labels_markdown."""

    def test_empty_data_labels(self):
        """Empty labels list produces 'no labels' message."""
        result = _format_labels_markdown(
            [],
            detail="full",
            extra={"owner": "org", "repo": "repo"},
        )
        assert "No labels configured for this repository" in result

    def test_empty_data_labels_no_extra(self):
        """Empty labels list with no extra still works (uses ? placeholders)."""
        result = _format_labels_markdown([], detail="full")
        assert "?/?" in result


class TestBuildLabelsMarkdown:
    """Tests for _build_labels_markdown shorthand."""

    def test_build_labels_markdown(self):
        """_build_labels_markdown delegates correctly."""
        data = [{"id": 1, "name": "bug", "color": "ff0000", "description": "A bug"}]
        result = _build_labels_markdown(data, "myorg", "myrepo", detail="full")
        assert "myorg/myrepo" in result
        assert "bug" in result
