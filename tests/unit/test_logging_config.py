"""Tests for gitea_mcp_server/logging_config.py.

Covers JSONFormatter and setup_logging function.
"""

import json
import logging

import pytest

from gitea_mcp_server.logging_config import JSONFormatter, SENSITIVE_KEYS, setup_logging


class TestJSONFormatter:
    def test_basic_format_produces_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        result = json.loads(formatter.format(record))
        assert result["timestamp"] is not None
        assert result["level"] == "INFO"
        assert result["logger"] == "test_logger"
        assert result["message"] == "hello world"

    def test_format_with_exc_info(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="an error occurred",
                args=(),
                exc_info=exc_info,
            )
        result = json.loads(formatter.format(record))
        assert "exception" in result
        assert "ValueError" in result["exception"]
        assert "test error" in result["exception"]

    def test_format_with_extra_fields(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="with extra",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc-123"
        record.user = "admin"
        result = json.loads(formatter.format(record))
        assert result["request_id"] == "abc-123"
        assert result["user"] == "admin"

    @pytest.mark.parametrize("sensitive_key", list(SENSITIVE_KEYS))
    def test_sensitive_keys_are_redacted(self, sensitive_key):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="sensitive data",
            args=(),
            exc_info=None,
        )
        setattr(record, sensitive_key, "my-secret-value")
        result = json.loads(formatter.format(record))
        assert result[sensitive_key] == "***REDACTED***"

    def test_standard_attrs_are_not_emitted_as_extras(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=10,
            msg="msg",
            args=(),
            exc_info=None,
        )
        result = json.loads(formatter.format(record))
        standard_keys = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "lineno", "funcName", "created",
            "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "getMessage",
        }
        for key in standard_keys:
            assert key not in result, f"Standard key '{key}' leaked into extra fields"

    def test_debug_level_log(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="debug message",
            args=(),
            exc_info=None,
        )
        result = json.loads(formatter.format(record))
        assert result["level"] == "DEBUG"


class TestSetupLogging:
    def test_json_format_sets_json_formatter(self):
        setup_logging(level="ERROR", log_format="json")
        root = logging.getLogger()
        found = any(
            isinstance(h.formatter, JSONFormatter) for h in root.handlers
        )
        assert found, "Expected JSONFormatter on at least one handler"

    def test_text_format_sets_standard_formatter(self):
        setup_logging(level="ERROR", log_format="text")
        root = logging.getLogger()
        found = any(
            isinstance(h.formatter, logging.Formatter)
            and not isinstance(h.formatter, JSONFormatter)
            for h in root.handlers
        )
        assert found, "Expected standard logging.Formatter on at least one handler"

    def test_respects_log_level(self):
        setup_logging(level="DEBUG", log_format="text")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_sets_httpx_to_warning(self):
        setup_logging(level="DEBUG", log_format="text")
        httpx_logger = logging.getLogger("httpx")
        assert httpx_logger.level == logging.WARNING

    def test_sets_fastmcp_to_info(self):
        setup_logging(level="DEBUG", log_format="text")
        fastmcp_logger = logging.getLogger("fastmcp")
        assert fastmcp_logger.level == logging.INFO

    def test_removes_existing_handlers(self):
        root = logging.getLogger()
        initial_count = len(root.handlers)
        setup_logging(level="INFO", log_format="json")
        assert len(root.handlers) > 0
        final_handlers = len(root.handlers)
        root.handlers.clear()
        setup_logging(level="INFO", log_format="json")
        assert len(root.handlers) <= final_handlers
        root.handlers.clear()

    def test_text_format_has_correct_format_string(self):
        setup_logging(level="ERROR", log_format="text")
        root = logging.getLogger()
        for handler in root.handlers:
            if isinstance(handler.formatter, logging.Formatter) and not isinstance(
                handler.formatter, JSONFormatter
            ):
                assert "%(asctime)s" in handler.formatter._fmt
                return
