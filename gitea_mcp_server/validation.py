"""Comprehensive input validation for tool arguments.

This module provides validation functions and schema augmentation to ensure
tool arguments meet Gitea API requirements before execution.

Architecture — two layers of enum validation:

1. **Schema-driven validation** (runtime in ``_run_validation``):
   Before calling a hardcoded ``SINGLE_VALIDATORS`` entry, ``_run_validation``
   checks whether the parameter's own JSON Schema defines an ``enum``. If it
   does, validation uses that enum — no hardcoded values needed.

2. **Description-to-enum inference** (schema time in
   ``augment_schema_with_validation``): Some Gitea spec types (e.g.
   ``CommitStatusState``) have no machine-readable ``enum`` — valid values
   live only in the description as quoted strings. A second pass in
   ``augment_schema_with_validation`` parses those descriptions and injects a
   proper ``enum``, so both validation and agent-facing schemas work correctly.
"""

import re
from collections.abc import Callable
from typing import Any

from fastmcp.server.providers.openapi import OpenAPITool

from gitea_mcp_server.constants import LABEL_MAX_LENGTH, PAGE_SIZE_MAX
from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.schema_utils import schema_type_matches

# Regex patterns for common Gitea parameters

# Owner/repo/username: alphanumeric words separated by single separator characters
# Must start and end with alphanumeric; separators (dot, underscore, hyphen) must be surrounded by alphanumeric
OWNER_REPO_PATTERN = r"^[a-zA-Z0-9]+([._-][a-zA-Z0-9]+)*$"

# Filepath: relative path, must start and end with alphanumeric; allow slashes and other safe characters
FILEPATH_PATTERN = r"^[a-zA-Z0-9.](?:[a-zA-Z0-9_./ -]*[a-zA-Z0-9.])?$"

# Git reference: similar to owner/repo but with additional git-specific characters (~,^,@)
REF_PATTERN = r"^[a-zA-Z0-9](?:[a-zA-Z0-9_./~^@-]*[a-zA-Z0-9])?$"

# Username: same as owner/repo
USERNAME_PATTERN = OWNER_REPO_PATTERN

# SHA1 (full): exactly 40 hexadecimal characters
SHA_PATTERN = r"^[a-fA-F0-9]{40}$"


# Validator functions


def _raise_validation_error(message: str, field: str) -> None:
    """Raise ValidationError with pre-computed message."""
    raise ValidationError(message, field=field)


def _validate_string(
    value: Any,
    *,
    field: str,
    pattern: str | None = None,
    error_message: str | None = None,
) -> None:
    """Validate a string parameter with optional regex pattern check.

    Args:
        value: The value to validate.
        field: The parameter name (used in error messages).
        pattern: Optional regex pattern for fullmatch validation.
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


# ---------------------------------------------------------------------------
# Schema-driven enum validation
# ---------------------------------------------------------------------------


def _collect_enum_values(schema: dict[str, Any]) -> list[Any] | None:
    """Collect enum values from a schema, walking anyOf/oneOf if needed.

    The resolved OpenAPI schema may place enum on the top-level object or
    inside an ``anyOf``/``oneOf`` branch (e.g., ``{"anyOf": [{"type":
    "string", "enum": [...]}, {"type": "null"}]}``).  This helper finds
    whichever location has the values.

    .. note::

       Only the **first** enum found is returned.  In practice schemas
       define at most one enum branch (the others are type-only, e.g.
       ``"null"``), so this is sufficient.  If a future spec version
       produces multiple branches with distinct enums, this function
       should merge them into a union rather than returning the first.

    Args:
        schema: The resolved JSON Schema dict for a parameter.

    Returns:
        The list of enum values, or ``None`` if no enum is defined
        anywhere in the schema.
    """
    if "enum" in schema:
        values = schema["enum"]
        if isinstance(values, list):
            return values
        return None
    for wrapper in ("anyOf", "oneOf"):
        branches = schema.get(wrapper)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict) and "enum" in branch:
                    values = branch["enum"]
                    if isinstance(values, list):
                        return values
                    return None
    return None


def _validate_enum_from_schema(
    value: Any,
    *,
    field: str,
    enum_values: list[Any],
) -> None:
    """Validate a value against an enum list from the parameter's own schema.

    Args:
        value: The value to validate.
        field: Parameter name for error messages.
        enum_values: The allowed values from the schema's ``enum``.

    Raises:
        ValidationError: If the value is not in ``enum_values``.
    """
    if value not in enum_values:
        valid = ", ".join(str(v) for v in enum_values)
        _raise_validation_error(f"{field} must be one of: {valid}", field)


# ---------------------------------------------------------------------------
# Description-to-enum inference
# ---------------------------------------------------------------------------

# Regex: find all double-quoted strings in a description.
# Heuristic: this catches values like ``"pending"`` but may misidentify
# incidental prose with quoted terms (e.g. ``the "state" field...``).
# The ``_MIN_ENUM_VALUES_FOR_INFERENCE`` threshold mitigates false
# positives by requiring at least 2 quoted values.  If false positives
# appear in practice, add comma/"and"/"or" adjacency verification.
_QUOTED_VALUE_RE = re.compile(r'"([^"]+)"')

# Minimum number of quoted values needed in a description to consider it
# an enum-like list rather than incidental prose.
_MIN_ENUM_VALUES_FOR_INFERENCE = 2


def _find_string_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """Walk ``anyOf``/``oneOf`` wrappers to find the first string-type schema.

    Some parameters are wrapped in ``{"anyOf": [{"type": "string", ...},
    {"type": "null"}]}`` while others have a flat ``{"type": ["string",
    "null"]}``.  This helper drills through both forms to find the actual
    string-leaf schema.

    Uses :func:`schema_type_matches` internally to handle ``type``-as-list
    (e.g. ``["string", "null"]``).

    Args:
        schema: A resolved JSON Schema dict.

    Returns:
        The first string-typed sub-schema, or ``None``.
    """
    if schema_type_matches(schema, "string"):
        return schema
    for wrapper in ("anyOf", "oneOf"):
        branches = schema.get(wrapper)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    result = _find_string_schema(branch)
                    if result is not None:
                        return result
    return None


def _infer_enum_from_description(schema: dict[str, Any]) -> bool:
    """Parse enum values from a description that lists quoted values.

    Some Gitea spec types (e.g. ``CommitStatusState``) have no machine-
    readable ``enum`` — the valid values are only documented in the
    description as quoted strings::

        CommitStatusState holds the state of a CommitStatus
        It can be "pending", "success", "error", "failure" and "warning"

    This function extracts those values and injects a proper ``enum`` key
    into the schema so that schema-driven validation and agent-facing
    ``tool_info`` both work correctly.

    Works on flat schemas and inside ``anyOf``/``oneOf`` wrappers (drills
    through to find the string branch).  Modifies *schema* in-place.

    If the schema (or its string branch) already has an ``enum``, the
    function is a no-op — the spec's own enum takes priority.

    Args:
        schema: A resolved JSON Schema dict for a parameter (mutated
            in-place if enum values are found).

    Returns:
        ``True`` if an ``enum`` was added to the schema, ``False`` otherwise.
    """
    target = _find_string_schema(schema)
    if target is None:
        return False
    if "enum" in target:
        return False  # Already has an enum — spec is sufficient.

    desc = target.get("description", "")
    if not desc:
        return False

    quoted = _QUOTED_VALUE_RE.findall(desc)
    # Require at least two quoted values to avoid false positives
    # (e.g. a single referenced term in prose).
    # Deduplicate while preserving order — spec descriptions
    # don't normally repeat values, but cheap insurance is cheap.
    seen: set[str] = set()
    deduped: list[str] = []
    for v in quoted:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    if len(deduped) < _MIN_ENUM_VALUES_FOR_INFERENCE:
        return False

    target["enum"] = deduped
    return True


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
    # NOTE: ``state`` is intentionally absent from SCHEMA_CONSTRAINTS.
    # Parameter-specific enums come from the spec itself (e.g.
    # issueListIssues has ``enum: [closed, open, all]``) or are inferred
    # from the description via ``_infer_enum_from_description`` below
    # (e.g. CommitStatusState).  Hardcoding state values here would
    # silently break tools with a different ``state`` meaning.
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


def _resolve_local_refs(
    schema: dict[str, Any],
    defs: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve ``$ref`` pointers in *schema* using *defs*, returning a new dict.

    Operates on the first level of ``anyOf``/``oneOf`` branches — does NOT
    deep-resolve nested ``$ref`` pointers inside resolved types (the
    inference only needs the description on the string branch).

    When a branch contains ``{"$ref": "#/$defs/TypeName"}`` and *defs*
    has ``TypeName``, the branch dict is replaced with the resolved
    definition (so ``type``, ``description``, and ``enum`` become
    directly accessible).

    Args:
        schema: The parameter schema (e.g. ``{"anyOf": [{"$ref": ...}]}``).
        defs: The ``$defs`` dict from the tool's parameters, or ``None``.

    Returns:
        A new dict with ``$ref`` branches resolved (or the original dict
        if no resolution was needed).
    """
    if not defs:
        return schema

    result = dict(schema)

    for wrapper in ("anyOf", "oneOf"):
        branches = result.get(wrapper)
        if not isinstance(branches, list):
            continue

        resolved_branches: list[dict[str, Any]] = []
        for branch in branches:
            if not isinstance(branch, dict):
                resolved_branches.append(branch)
                continue

            ref = branch.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                type_name = ref[len("#/$defs/"):]
                resolved = defs.get(type_name)
                if isinstance(resolved, dict):
                    resolved_branches.append(dict(resolved))
                    continue

            resolved_branches.append(branch)

        result[wrapper] = resolved_branches

    return result


def _inject_enum_into_defs(
    existing_schema: dict[str, Any],
    resolved: dict[str, Any],
    defs: dict[str, Any] | None,
) -> None:
    """Copy enum from *resolved* into *existing_schema* and *defs*.

    After ``_infer_enum_from_description`` adds an enum to the resolved
    schema (where ``$ref`` was replaced with the actual type), this
    function injects that enum back into:
    1. The original ``$defs`` definition so ``$ref`` resolution benefits.
    2. The original schema's ``$ref`` branch so direct access works.

    Args:
        existing_schema: The original (unresolved) param schema.
        resolved: The ``$ref``-resolved copy (modified by inference).
        defs: The ``$defs`` dict, or ``None``.
    """
    source_enum = _collect_enum_values(resolved)
    if source_enum is None:
        return
    if _collect_enum_values(existing_schema) is not None:
        return  # Already has enum — no injection needed.

    # 1. Inject into the $defs definition so any downstream $ref
    #    resolution produces a schema with the enum.
    for wrapper in ("anyOf", "oneOf"):
        branches = existing_schema.get(wrapper)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            ref = branch.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                type_name = ref[len("#/$defs/"):]
                if defs and type_name in defs and "enum" not in defs[type_name]:
                    defs[type_name]["enum"] = list(source_enum)

    # 2. Inject into the $ref branch itself so direct access
    #    (without $ref resolution) also sees the enum.
    for wrapper in ("anyOf", "oneOf"):
        existing_branches = existing_schema.get(wrapper)
        resolved_branches = resolved.get(wrapper)
        if not isinstance(existing_branches, list) or not isinstance(resolved_branches, list):
            continue
        for i, (eb, rb) in enumerate(zip(existing_branches, resolved_branches)):
            if isinstance(eb, dict) and isinstance(rb, dict) and "enum" in rb and "enum" not in eb:
                eb["enum"] = list(rb["enum"])


def augment_schema_with_validation(component: OpenAPITool) -> None:
    """Add JSON schema constraints to tool parameters for agent visibility.

    Two passes:

    1. **Structural constraints** — injects ``minLength``, ``maxLength``,
       ``pattern``, ``minimum``, and ``maximum`` from
       :data:`SCHEMA_CONSTRAINTS` for recognised parameter names.  These
       are invariants (e.g. owner names are always 1-50 chars) that the
       spec doesn't normally define.

    2. **Description-to-enum inference** — for parameters whose resolved
       schema still has no ``enum``, tries to extract valid values from
       the description text (e.g. ``CommitStatusState`` lists values as
       ``"pending", "success", ...`` in prose).  See
       :func:`_infer_enum_from_description`.

       Before inference, any unresolved ``$ref`` pointers in the parameter
       schema (e.g. ``{"$ref": "#/$defs/CommitStatusState"}`` inside
       ``anyOf``) are resolved against the tool's ``$defs`` section so that
       ``_find_string_schema`` can follow them to the actual string type.

       If inference adds an enum to the resolved schema, it is injected
       back into both the ``$defs`` definition and the original parameter
       schema's ``$ref`` branch, so downstream consumers (``_run_validation``,
       ``tool_info``) see the enum regardless of ``$ref`` resolution.

    Args:
        component: The OpenAPITool to augment.
    """
    params = getattr(component, "parameters", None)
    if not params:
        return

    props = params.get("properties", {})
    if not props:
        return

    defs: dict[str, Any] | None = params.get("$defs")

    # Structural constraints from SCHEMA_CONSTRAINTS
    for name, constraints in SCHEMA_CONSTRAINTS.items():
        if name in props:
            existing_schema = props[name]
            if not isinstance(existing_schema, dict):
                continue
            for key, value in constraints.items():
                if key not in existing_schema:
                    existing_schema[key] = value

    # Description-to-enum for params that still lack an enum
    for existing_schema in props.values():
        if not isinstance(existing_schema, dict):
            continue
        # Resolve $ref so _collect_enum_values and _find_string_schema
        # can see through to the actual type definition.
        resolved = _resolve_local_refs(existing_schema, defs)
        if _collect_enum_values(resolved) is not None:
            # Spec already has an enum on the resolved type — skip.
            continue
        if _infer_enum_from_description(resolved):
            # Inference added enum to resolved schema — inject back.
            _inject_enum_into_defs(existing_schema, resolved, defs)
