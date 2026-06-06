"""Comprehensive input validation for tool arguments.

This module provides validation functions and schema augmentation to ensure
tool arguments meet Gitea API requirements before execution.
"""

import re
from collections.abc import Callable
from typing import Any

from fastmcp.server.providers.openapi import OpenAPITool

from gitea_mcp_server.exceptions import ValidationError

# Regex patterns for common Gitea parameters

# Owner/repo/username: alphanumeric words separated by single separator characters
# Must start and end with alphanumeric; separators (dot, underscore, hyphen) must be surrounded by alphanumeric
OWNER_REPO_PATTERN = r"^[a-zA-Z0-9]+([._-][a-zA-Z0-9]+)*$"

# Filepath: relative path, must start and end with alphanumeric; allow slashes and other safe characters
FILEPATH_PATTERN = r"^[a-zA-Z0-9](?:[a-zA-Z0-9_./ -]*[a-zA-Z0-9])?$"

# Git reference: similar to owner/repo but with additional git-specific characters (~,^,@)
REF_PATTERN = r"^[a-zA-Z0-9](?:[a-zA-Z0-9_./~^@-]*[a-zA-Z0-9])?$"

# Username: same as owner/repo
USERNAME_PATTERN = OWNER_REPO_PATTERN

# SHA1 (full): exactly 40 hexadecimal characters
SHA_PATTERN = r"^[a-fA-F0-9]{40}$"

LABEL_MAX_LENGTH = 100
PAGE_SIZE_MAX = 100


# Validator functions


def _raise_validation_error(message: str, field: str) -> None:
    """Raise ValidationError with pre-computed message."""
    raise ValidationError(message, field=field)


def _validate_string(
    value: Any,
    *,
    field: str,
    pattern: str | None = None,
    allowed: set[str] | None = None,
    error_message: str | None = None,
) -> None:
    """Validate a string parameter with optional pattern or allowed-values check.

    Args:
        value: The value to validate.
        field: The parameter name (used in error messages).
        pattern: Optional regex pattern for fullmatch validation.
        allowed: Optional set of allowed values.
        error_message: Custom error message template with {field} placeholder.

    Raises:
        ValidationError: If validation fails.
    """
    if not isinstance(value, str):
        _raise_validation_error(f"{field} must be a string", field)
    if pattern is not None and not value:
        _raise_validation_error(f"{field} cannot be empty", field)
    if pattern is not None and not re.fullmatch(pattern, value):
        msg = error_message or f"{field} contains invalid characters"
        _raise_validation_error(msg.format(field=field), field)
    if allowed is not None and value not in allowed:
        valid = ", ".join(sorted(allowed))
        msg = error_message or f"{field} must be one of: {valid}"
        _raise_validation_error(msg.format(field=field), field)


def validate_owner_repo(value: Any, *, field: str) -> None:
    """Validate an owner, repo, or org name."""
    _validate_string(
        value,
        field=field,
        pattern=OWNER_REPO_PATTERN,
        error_message="{field} contains invalid characters (allowed: letters, digits, underscores, hyphens, dots; must start and end with letter or digit)",
    )


def validate_filepath(value: Any, *, field: str) -> None:
    """Validate a file path within a repository."""
    _validate_string(value, field=field)
    if value.startswith("/"):
        msg = f"{field} must be a relative path (cannot start with '/')"
        raise ValidationError(msg, field=field)
    if ".." in value.split("/"):
        msg = f"{field} cannot contain '..' components"
        raise ValidationError(msg, field=field)
    if not re.fullmatch(FILEPATH_PATTERN, value):
        msg = f"{field} contains invalid characters (allowed: letters, digits, spaces, slashes, underscores, hyphens, dots)"
        raise ValidationError(msg, field=field)


def validate_ref(value: Any, *, field: str) -> None:
    """Validate a git reference (branch, tag, or commit SHA)."""
    _validate_string(
        value,
        field=field,
        pattern=REF_PATTERN,
        error_message="{field} contains invalid characters for a git reference",
    )


def validate_username(value: Any, *, field: str) -> None:
    """Validate a username."""
    _validate_string(
        value,
        field=field,
        pattern=USERNAME_PATTERN,
        error_message="{field} contains invalid characters (allowed: letters, digits, underscores, hyphens, dots; must start and end with letter or digit)",
    )


def validate_sha(value: Any, *, field: str) -> None:
    """Validate a full SHA-1 hash (40 hex characters)."""
    _validate_string(
        value,
        field=field,
        pattern=SHA_PATTERN,
        error_message="{field} must be a 40-character hexadecimal SHA",
    )


def validate_labels(value: Any, *, field: str) -> None:
    """Validate a list of labels (strings or integers).

    Args:
        value: The labels list.
        field: Parameter name for error messages.

    Raises:
        ValidationError: If invalid.
    """
    if not isinstance(value, list):
        _raise_validation_error(f"{field} must be a list", field)
    for label in value:
        if isinstance(label, bool):
            _raise_validation_error("Label must be a string or integer, not bool", field)
        if isinstance(label, int):
            if label < 1:
                _raise_validation_error("Label ID must be positive", field)
        elif isinstance(label, str):
            if not label:
                _raise_validation_error("Empty label string is not allowed", field)
            if not label.strip():
                _raise_validation_error("Label cannot be whitespace only", field)
            if len(label) > LABEL_MAX_LENGTH:
                _raise_validation_error(
                    f"Label name exceeds maximum length ({LABEL_MAX_LENGTH})", field
                )
        else:
            _raise_validation_error(
                f"Label must be a string or integer, got {type(label).__name__}", field
            )


def validate_pagination(page: Any = None, per_page: Any = None) -> None:
    """Validate pagination parameters.

    Args:
        page: Page number (integer >= 1).
        per_page: Items per page (integer between 1 and 100).

    Raises:
        ValidationError: If any parameter is invalid.
    """
    if page is not None:
        if not isinstance(page, int):
            _raise_validation_error("page must be an integer", "page")
        if page < 1:
            _raise_validation_error("page must be >= 1", "page")
    if per_page is not None:
        if not isinstance(per_page, int):
            _raise_validation_error("per_page must be an integer", "per_page")
        if per_page < 1:
            _raise_validation_error("per_page must be >= 1", "per_page")
        if per_page > PAGE_SIZE_MAX:
            msg = f"per_page must be <= {PAGE_SIZE_MAX}"
            _raise_validation_error(msg, "per_page")


def validate_state(value: Any, *, field: str) -> None:
    """Validate an issue/PR state parameter."""
    _validate_string(
        value,
        field=field,
        allowed={"open", "closed", "all"},
        error_message="{field} must be one of: open, closed, all",
    )


# Mapping from parameter name to validator function
# Uses broader Callable to accommodate keyword-only arguments (*, field: str)
SINGLE_VALIDATORS: dict[str, Callable[..., None]] = {
    "owner": validate_owner_repo,
    "repo": validate_owner_repo,
    "org": validate_owner_repo,  # alias for organization
    "username": validate_username,
    "filepath": validate_filepath,
    "ref": validate_ref,
    "sha": validate_sha,
    "labels": validate_labels,
    "state": validate_state,
}

# Schema constraints to augment tool parameter definitions
SCHEMA_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "owner": {
        "minLength": 1,
        "maxLength": 50,
        "pattern": OWNER_REPO_PATTERN,
    },
    "repo": {
        "minLength": 1,
        "maxLength": 100,
        "pattern": OWNER_REPO_PATTERN,
    },
    "org": {
        "minLength": 1,
        "maxLength": 50,
        "pattern": OWNER_REPO_PATTERN,
    },
    "username": {
        "minLength": 1,
        "maxLength": 50,
        "pattern": USERNAME_PATTERN,
    },
    "filepath": {
        "minLength": 1,
        "maxLength": 500,
        "pattern": FILEPATH_PATTERN,
    },
    "ref": {
        "minLength": 1,
        "maxLength": 255,
        "pattern": REF_PATTERN,
    },
    "sha": {
        "minLength": 40,
        "maxLength": 40,
        "pattern": SHA_PATTERN,
    },
    "state": {
        "enum": ["open", "closed", "all"],
    },
    "page": {
        "minimum": 1,
        "type": "integer",
    },
    "per_page": {
        "minimum": 1,
        "maximum": PAGE_SIZE_MAX,
        "type": "integer",
    },
}


def augment_schema_with_validation(component: OpenAPITool) -> None:
    """Add JSON schema constraints to tool parameters for agent visibility.

    This function mutates the component's parameter schema by adding
    minLength, maxLength, pattern, minimum, maximum, or enum constraints
    for recognized parameter names.

    Args:
        component: The OpenAPITool to augment.
    """
    params = getattr(component, "parameters", None)
    if not params:
        return

    props = params.get("properties", {})
    if not props:
        return

    for name, constraints in SCHEMA_CONSTRAINTS.items():
        if name in props:
            existing_schema = props[name]
            if not isinstance(existing_schema, dict):
                continue
            # Merge constraints, only adding if not already present
            for key, value in constraints.items():
                if key not in existing_schema:
                    existing_schema[key] = value
