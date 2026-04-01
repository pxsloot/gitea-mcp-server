"""Logging configuration for Gitea MCP Server."""

import json
import logging
import sys
from datetime import datetime, UTC

# Sensitive keys that should be redacted in logs
SENSITIVE_KEYS = {
    "authorization",
    "token",
    "api_key",
    "password",
    "secret",
    "cookie",
    "x-gitea-token",
    "x-auth-token",
    "bearer",
    "private_key",
    "secret_key",
}


class JSONFormatter(logging.Formatter):
    """Format log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the record as JSON."""
        log_object = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_object["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "getMessage",
                "exc_info",
                "exc_text",
                "stack_info",
            }:
                # Redact sensitive values
                if key.lower() in SENSITIVE_KEYS:
                    log_object[key] = "***REDACTED***"
                else:
                    log_object[key] = value

        return json.dumps(log_object)


def setup_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Configure structured logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Output format: 'json' or 'text'
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))

    formatter: logging.Formatter
    if log_format.lower() == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Configure specific loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("fastmcp").setLevel(logging.INFO)

    logger = logging.getLogger(__name__)
    logger.info(
        "Logging configured", extra={"level": level, "format": log_format, "handlers": ["console"]}
    )
