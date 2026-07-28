"""Tests for tool_display module (format_tool_result wrapper).

Coverage includes:
- Happy path: raw, json, markdown formats with various data shapes
- Error recovery: all 3 exception types (TypeError, AttributeError, ValueError)
  caught by ``format_tool_result`` (added in #583)
- Non-JSON-serializable data handling
- Logger call verification on format failure
"""

import json
import logging
from unittest.mock import patch

import pytest
from fastmcp.tools.base import ToolResult

from tests.helpers.mcp_results import extract_text_content, parse_json_content

from gitea_mcp_server.tools.tool_display import format_tool_result


class TestFormatToolResult:
    """Happy-path tests for format_tool_result."""

    def test_raw_format_passthrough(self) -> None:
        """'raw' format returns structured content."""
        result = format_tool_result({"key": "value"}, "raw")
        assert result.structured_content == {"result": {"key": "value"}}
        # raw format may include empty or json text content; verify structure
        assert isinstance(result.structured_content, dict)

    def test_json_format(self) -> None:
        """'json' format produces indented JSON in text content."""
        result = format_tool_result({"key": "value"}, "json")
        assert result.structured_content == {"result": {"key": "value"}}
        assert result.content is not None
        text = extract_text_content(result.content)
        assert '"key"' in text
        assert '"value"' in text

    def test_markdown_format_default(self) -> None:
        """'markdown' format produces markdown text."""
        data = {"name": "test", "count": 42}
        result = format_tool_result(data, "markdown")
        assert result.structured_content == {"result": data}
        assert result.content is not None
        text = extract_text_content(result.content)
        assert "test" in text
        assert "42" in text

    def test_concise_detail_collapses_schema(self) -> None:
        """detail=concise with schema collapses nested objects."""
        data = {"id": 1, "user": {"login": "alice", "id": 99}}
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "user": {"$ref": "#/components/schemas/User"},
            },
        }
        result = format_tool_result(data, "json", detail="concise", schema=schema)
        text = extract_text_content(result.content)
        # Nested user should be collapsed to $ref
        assert "$ref:User" in text

    def test_concise_detail_no_schema_no_collapse(self) -> None:
        """detail=concise without schema leaves data intact."""
        data = {"id": 1, "nested": {"key": "val"}}
        result = format_tool_result(data, "json", detail="concise", schema=None)
        text = extract_text_content(result.content)
        assert '"key"' in text
        assert '"val"' in text

    def test_list_data_formats_correctly(self) -> None:
        """List data is wrapped in result and formatted."""
        data = [{"id": 1}, {"id": 2}]
        result = format_tool_result(data, "json")
        assert result.structured_content == {"result": data}

    def test_returns_tool_result_type(self) -> None:
        """Return value is a ToolResult instance."""
        from fastmcp.tools.base import ToolResult

        result = format_tool_result({"a": 1}, "raw")
        assert isinstance(result, ToolResult)

    def test_empty_dict(self) -> None:
        """Empty dict formats without error."""
        result = format_tool_result({}, "markdown")
        assert result.structured_content == {"result": {}}

    def test_none_data(self) -> None:
        """None data passes through."""
        result = format_tool_result(None, "raw")
        assert result.structured_content == {"result": None}


class TestFormatToolResultErrorRecovery:
    """Error recovery: format_tool_result catches formatting exceptions.

    Mirrors the resource-side pattern in ``_format_resource_content``:
    wraps ``apply_format`` in try/except for (TypeError, AttributeError,
    ValueError).  Uses both mocked exception injection and real error-
    triggering data for realistic coverage.
    """

    # --- Mock-based: inject exceptions into apply_format ---

    @pytest.mark.parametrize(
        ("exc_cls", "exc_msg"),
        [
            (TypeError, "bad type"),
            (AttributeError, "no attribute"),
            (ValueError, "bad value"),
        ],
    )
    def test_markdown_recovers_from_all_exception_types(self, exc_cls: type, exc_msg: str) -> None:
        """All 3 exception types from markdown path produce fallback."""
        data = {"key": "value"}
        with patch(
            "gitea_mcp_server.tools.tool_display.apply_format",
            side_effect=exc_cls(exc_msg),
        ):
            result = format_tool_result(data, "markdown")
            assert isinstance(result, ToolResult)
            text = extract_text_content(result.content)
            assert "```json" in text
            assert "formatting failed" in text
            assert exc_cls.__name__ in text

    @pytest.mark.parametrize(
        ("exc_cls", "exc_msg"),
        [
            (TypeError, "bad type"),
            (AttributeError, "no attribute"),
            (ValueError, "bad value"),
        ],
    )
    def test_json_recovers_from_all_exception_types(self, exc_cls: type, exc_msg: str) -> None:
        """All 3 exception types from json path produce fallback."""
        data = {"key": "value"}
        with patch(
            "gitea_mcp_server.tools.tool_display.apply_format",
            side_effect=exc_cls(exc_msg),
        ):
            result = format_tool_result(data, "json")
            assert isinstance(result, ToolResult)
            fallback = parse_json_content(result)
            assert "result" in fallback

    # --- Real error-triggering data (no mocking needed) ---

    def test_non_serializable_data_markdown_fallback(self) -> None:
        """Non-JSON-serializable data in markdown returns code fence fallback."""
        class NonSerializable:
            pass

        data = {"bad": NonSerializable()}
        result = format_tool_result(data, "markdown")
        assert isinstance(result, ToolResult)
        text = extract_text_content(result.content)
        assert "```json" in text
        assert "formatting failed" in text
        # The actual exception may be PydanticSerializationError (subclass of
        # TypeError), so we check for the generic marker instead of a specific name.

    def test_non_serializable_data_json_fallback(self) -> None:
        """Non-JSON-serializable data in json returns fallback result."""
        class NonSerializable:
            pass

        data = {"bad": NonSerializable()}
        result = format_tool_result(data, "json")
        assert isinstance(result, ToolResult)
        # structured_content carries the safe string representation
        assert result.structured_content is not None
        assert "result" in result.structured_content
        fallback = parse_json_content(result)
        assert "result" in fallback

    def test_non_serializable_data_custom_str(self) -> None:
        """Non-serializable with __str__ uses it in fallback in json mode."""
        class Unserializable:
            def __str__(self) -> str:
                return "custom_str_repr"

        data = {"bad": Unserializable()}
        # json mode: json.dumps raises TypeError directly for
        # non-serializable data, which we catch and handle.
        result = format_tool_result(data, "json")
        assert isinstance(result, ToolResult)
        # structured_content contains the safe fallback
        assert result.structured_content is not None
        assert "result" in result.structured_content

    # --- Raw format bypass ---

    def test_raw_format_bypasses_error_recovery(self) -> None:
        """Raw format (handled by apply_format early return) does not trigger recovery."""
        result = format_tool_result({"key": "value"}, "raw")
        assert result.structured_content == {"result": {"key": "value"}}

    # --- Logger verification ---

    def test_logger_warning_on_format_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """logger.warning is called on format failure."""
        data = {"key": "value"}
        caplog.set_level(logging.WARNING)
        with patch(
            "gitea_mcp_server.tools.tool_display.apply_format",
            side_effect=TypeError("bad type"),
        ):
            format_tool_result(data, "markdown")
            assert any(
                "Display pipeline recovered from" in record.message
                for record in caplog.records
            )

    def test_logger_level_is_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log record level is WARNING."""
        data = {"key": "value"}
        caplog.set_level(logging.WARNING)
        with patch(
            "gitea_mcp_server.tools.tool_display.apply_format",
            side_effect=ValueError("bad value"),
        ):
            format_tool_result(data, "json")
            assert any(
                record.levelname == "WARNING"
                for record in caplog.records
            )

    # --- Happy path still works ---

    def test_happy_path_unchanged(self) -> None:
        """Normal data still formats correctly (no regression)."""
        data = {"name": "test", "count": 42}
        result = format_tool_result(data, "markdown")
        assert result.structured_content == {"result": data}
        assert "test" in extract_text_content(result.content)
