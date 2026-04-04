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


# Validator functions


def validate_owner_repo(value: Any, *, field: str) -> None:
    """Validate an owner, repo, or org name.

    Args:
        value: The value to validate (should be a string).
        field: The parameter name (used in error messages).

    Raises:
        ValidationError: If validation fails.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field=field)
    if not value:
        raise ValidationError(f"{field} cannot be empty", field=field)
    if not re.fullmatch(OWNER_REPO_PATTERN, value):
        raise ValidationError(
            f"{field} contains invalid characters (allowed: letters, digits, underscores, hyphens, dots; must start and end with letter or digit)",
            field=field,
        )


def validate_filepath(value: Any, *, field: str) -> None:
    """Validate a file path within a repository.

    Args:
        value: The filepath string.
        field: Parameter name for error messages.

    Raises:
        ValidationError: If invalid.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field=field)
    if not value:
        raise ValidationError(f"{field} cannot be empty", field=field)
    # Disallow absolute paths
    if value.startswith("/"):
        raise ValidationError(
            f"{field} must be a relative path (cannot start with '/')", field=field
        )
    # Disallow parent directory traversal
    if ".." in value.split("/"):
        raise ValidationError(f"{field} cannot contain '..' components", field=field)
    # Check allowed characters and basic structure
    if not re.fullmatch(FILEPATH_PATTERN, value):
        raise ValidationError(
            f"{field} contains invalid characters (allowed: letters, digits, spaces, slashes, underscores, hyphens, dots)",
            field=field,
        )


def validate_ref(value: Any, *, field: str) -> None:
    """Validate a git reference (branch, tag, or commit SHA).

    Args:
        value: The ref string.
        field: Parameter name for error messages.

    Raises:
        ValidationError: If invalid.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field=field)
    if not value:
        raise ValidationError(f"{field} cannot be empty", field=field)
    if not re.fullmatch(REF_PATTERN, value):
        raise ValidationError(
            f"{field} contains invalid characters for a git reference",
            field=field,
        )


def validate_username(value: Any, *, field: str) -> None:
    """Validate a username.

    Args:
        value: The username string.
        field: Parameter name for error messages.

    Raises:
        ValidationError: If invalid.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field=field)
    if not value:
        raise ValidationError(f"{field} cannot be empty", field=field)
    if not re.fullmatch(USERNAME_PATTERN, value):
        raise ValidationError(
            f"{field} contains invalid characters (allowed: letters, digits, underscores, hyphens, dots; must start and end with letter or digit)",
            field=field,
        )


def validate_sha(value: Any, *, field: str) -> None:
    """Validate a full SHA-1 hash (40 hex characters).

    Args:
        value: The SHA string.
        field: Parameter name for error messages.

    Raises:
        ValidationError: If invalid.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field=field)
    if not value:
        raise ValidationError(f"{field} cannot be empty", field=field)
    if not re.fullmatch(SHA_PATTERN, value):
        raise ValidationError(f"{field} must be a 40-character hexadecimal SHA", field=field)


def validate_labels(value: Any, *, field: str) -> None:
    """Validate a list of labels (strings or integers).

    Args:
        value: The labels list.
        field: Parameter name for error messages.

    Raises:
        ValidationError: If invalid.
    """
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list", field=field)
    for label in value:
        # Reject booleans explicitly (bool is subclass of int)
        if isinstance(label, bool):
            raise ValidationError(f"Label must be a string or integer, not bool", field=field)
        if isinstance(label, int):
            if label < 1:
                raise ValidationError(f"Label ID must be positive", field=field)
        elif isinstance(label, str):
            if not label:
                raise ValidationError(f"Empty label string is not allowed", field=field)
            # Disallow whitespace-only labels
            if not label.strip():
                raise ValidationError(f"Label cannot be whitespace only", field=field)
            # Limit length
            if len(label) > 100:
                raise ValidationError(f"Label name exceeds maximum length (100)", field=field)
        else:
            raise ValidationError(
                f"Label must be a string or integer, got {type(label).__name__}", field=field
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
            raise ValidationError("page must be an integer", field="page")
        if page < 1:
            raise ValidationError("page must be >= 1", field="page")
    if per_page is not None:
        if not isinstance(per_page, int):
            raise ValidationError("per_page must be an integer", field="per_page")
        if per_page < 1:
            raise ValidationError("per_page must be >= 1", field="per_page")
        if per_page > 100:
            raise ValidationError("per_page must be <= 100", field="per_page")


def validate_state(value: Any, *, field: str) -> None:
    """Validate an issue/PR state parameter.

    Args:
        value: The state string.
        field: Parameter name for error messages.

    Raises:
        ValidationError: If invalid.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field=field)
    if value not in ("open", "closed", "all"):
        raise ValidationError(f"{field} must be one of: open, closed, all", field=field)


# Mapping from parameter name to single-argument validator function
SINGLE_VALIDATORS: dict[str, Callable[[Any, str], None]] = {
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
        "maximum": 100,
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


def inject_validation_wrapper(component: OpenAPITool) -> None:
    """Wrap the component's run method with runtime argument validation.

    The wrapper executes before the original run method and validates
    arguments using the appropriate validator functions. Validation
    occurs before label conversion and before API calls.

    Args:
        component: The OpenAPITool to wrap.
    """
    original_run = getattr(component, "run", None)
    if original_run is None:
        return

    async def validated_run(arguments: dict[str, Any]) -> Any:
        # Single-argument validators
        for name, value in arguments.items():
            if name in SINGLE_VALIDATORS:
                try:
                    SINGLE_VALIDATORS[name](value, field=name)
                except ValidationError:
                    raise
                except Exception as e:
                    raise ValidationError(f"Validation error for {name}: {e}", field=name) from e

        # Combined validators
        if "page" in arguments or "per_page" in arguments:
            validate_pagination(arguments.get("page"), arguments.get("per_page"))

        return await original_run(arguments)

    component.run = validated_run
