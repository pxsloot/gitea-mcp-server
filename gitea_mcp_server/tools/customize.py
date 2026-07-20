"""Core tool customization pipeline.

Immediate helpers for the customization pipeline (annotations, hint inference,
categorization, title generation, scope derivation, invalidation computation).
"""

import logging
from dataclasses import dataclass
from typing import Any, cast

from mcp.types import ToolAnnotations

from gitea_mcp_server.constants import (
    HTTP_METHODS_DESTRUCTIVE,
    HTTP_METHODS_IDEMPOTENT,
    HTTP_METHODS_SAFE,
    LABEL_GUIDANCE,
    TOOL_INVALIDATION_PATTERNS,
)
from gitea_mcp_server.tools.schemas import _schema_type_is_array

logger = logging.getLogger(__name__)

_CATEGORY_PREFIXES: list[tuple[str, str, bool]] = [
    ("/admin", "admin", False),
    ("/orgs", "organization", False),
    ("/org/", "organization", False),
    ("/user", "user", False),
    ("/users/", "user", False),
    ("/repos/{owner}/{repo}/issues", "issue", False),
    ("/repos/{owner}/{repo}/pulls", "pull_request", False),
    ("/issues", "issue", True),
    ("/pulls", "pull_request", True),
    ("/repos", "repository", False),
]


# Verbs that map to domain-level actions - when one of these remains after
# stripping the domain prefix, the domain noun is appended as the object.
_ACTION_VERBS: set[str] = {
    "accept",
    "action",
    "add",
    "adopt",
    "cancel",
    "check",
    "clear",
    "convert",
    "create",
    "delete",
    "disable",
    "dispatch",
    "dismiss",
    "edit",
    "enable",
    "fork",
    "generate",
    "get",
    "link",
    "list",
    "merge",
    "migrate",
    "mirror",
    "move",
    "notify",
    "pin",
    "post",
    "publicize",
    "register",
    "reject",
    "remove",
    "rename",
    "render",
    "replace",
    "reset",
    "run",
    "search",
    "set",
    "start",
    "stop",
    "submit",
    "sync",
    "test",
    "transfer",
    "unblock",
    "unlink",
    "unpin",
    "update",
    "validate",
    "verify",
}


@dataclass
class _DomainConfig:
    """Configuration for an operationId domain prefix.

    Attributes:
        noun: Display noun for the domain (e.g., ``"Issue"``, ``"Repository"``).
        strip: Whether to strip the domain prefix from the title.
               Set to ``False`` when the prefix *is* the entity name
               (e.g., ``activitypub``).
    """

    noun: str
    strip: bool = True


# Single source of truth for known operationId domain prefixes.
# Every known domain has a noun and a strip flag - no more keeping
# _DOMAIN_PREFIXES, _KEEP_PREFIX, and _DOMAIN_NOUNS in sync manually.
_DOMAINS: dict[str, _DomainConfig] = {
    "issue": _DomainConfig(noun="Issue"),
    "repo": _DomainConfig(noun="Repository"),
    "user": _DomainConfig(noun="User"),
    "org": _DomainConfig(noun="Organization"),
    "admin": _DomainConfig(noun="Admin"),
    "notification": _DomainConfig(noun="Notification"),
    "package": _DomainConfig(noun="Package"),
    "settings": _DomainConfig(noun="Settings"),
    "topic": _DomainConfig(noun="Topic"),
    "team": _DomainConfig(noun="Team"),
    "activitypub": _DomainConfig(noun="Activitypub", strip=False),
}


def _snake_to_title(snake_op_id: str) -> str:
    """Convert a snake_case operationId to a human-readable title.

    Handles three patterns based on Gitea's naming convention
    ``{domain}_{action}_{object}``:

    1. **Domain + verb + object** (most common): ``issue_create_issue`` → ``"Create Issue"``
       - the domain prefix is dropped, action parts are Title Cased.
    2. **Verb-only after strip**: ``issue_delete`` → ``"Delete Issue"``
       - the domain noun is appended as the object when only one verb remains.
    3. **Kept-prefix domains**: ``activitypub_person`` → ``"Activitypub Person"``
       - domains with ``strip=False`` are retained because the prefix *is* the entity name.

    Logs a warning at startup when an unknown domain prefix is encountered,
    surfacing drift when the Gitea API adds new operationId domains.
    """
    if not snake_op_id:
        return "Unnamed Tool"

    parts = snake_op_id.split("_")
    config = _DOMAINS.get(parts[0]) if parts else None

    if parts and config is None and parts[0] not in _ACTION_VERBS:
        logger.warning(
            "Unknown operationId domain '%s' in '%s' - title may be suboptimal. "
            "Add entry to _DOMAINS if this is a recurring Gitea domain.",
            parts[0],
            snake_op_id,
        )

    domain = parts[0] if config else None
    keep_prefix = not config.strip if config else False

    action_parts = parts[1:] if domain and not keep_prefix and len(parts) > 1 else parts

    title = " ".join(p.title() for p in action_parts)

    if domain and not keep_prefix and len(action_parts) == 1:
        word = action_parts[0].lower()
        if word in _ACTION_VERBS:
            # domain truthy ⇒ config is set (narrow for mypy)
            title = f"{title} {cast('_DomainConfig', config).noun}"

    return title


def generate_tool_title(route: Any) -> str:
    """Generate a human-readable title from the route's operationId.

    Uses the ``operationId`` (already converted to snake_case by the
    OpenAPI converter) as the sole source.  The OpenAPI ``summary``
    lives on as the tool's MCP ``description`` - it is a description,
    not a title.
    """
    operation_id = getattr(route, "operation_id", None)
    if not operation_id:
        return "Unnamed Tool"
    return _snake_to_title(str(operation_id))


def categorize_tool(path: str) -> str:
    """Assign a category tag based on the request path prefix."""
    for prefix, category, contains in _CATEGORY_PREFIXES:
        if contains:
            if prefix in path:
                return category
        elif path.startswith(prefix):
            return category
    return "misc"


def add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
    """Set readOnly, destructive, idempotent, and openWorld hints from HTTP method."""
    method = getattr(route, "method", None)

    if annotations.readOnlyHint is None:
        annotations.readOnlyHint = method in HTTP_METHODS_SAFE

    if annotations.destructiveHint is None:
        annotations.destructiveHint = method in HTTP_METHODS_DESTRUCTIVE

    if annotations.idempotentHint is None:
        annotations.idempotentHint = method in HTTP_METHODS_IDEMPOTENT

    if annotations.openWorldHint is None:
        annotations.openWorldHint = True


def synthetic_annotations(
    *,
    read_only: bool = True,
    open_world: bool = False,
) -> ToolAnnotations:
    """Create ToolAnnotations with all 4 hints explicitly set.

    Synthetic tools are registered directly via ``mcp.tool(annotations=...)``
    and don't go through ``add_inferred_hints()``.  This helper ensures every
    hint field is populated consistently at all registration sites.

    Hint derivation:
        - ``readOnlyHint`` = *read_only*
        - ``destructiveHint`` = always ``False`` (synthetic tools never delete)
        - ``idempotentHint`` = *read_only* (read-only operations are idempotent)
        - ``openWorldHint`` = *open_world*

    .. caution::
       ``read_only=False`` is for tools that delegate to arbitrary API
       operations.  Even though the tool itself does nothing destructive,
       its *results* can be - agents should not assume safety.

    Args:
        read_only: Tool only reads/transforms in-memory data without side
                   effects.  Set to ``False`` for tools that delegate to
                   arbitrary API operations.
        open_world: Tool makes external API calls.  Local synthetic tools
                    (``search``, ``search_tools``, ``tool_info``, etc.)
                    operate entirely in-memory - pass ``False``.

    Returns:
        ToolAnnotations with all four hint fields explicitly set
        (never ``None``).
    """
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=False,
        idempotentHint=read_only,
        openWorldHint=open_world,
    )


def compute_invalidation_patterns(path: str, method: str) -> list[str]:
    """Return cache invalidation URI patterns for the given path and HTTP method."""
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return []

    for prefix, match_type, patterns in TOOL_INVALIDATION_PATTERNS:
        if match_type == "exact":
            if path == prefix:
                return patterns
        elif path.startswith(prefix):
            return patterns
    return []


def _prepare_annotations(component: Any, title: str) -> ToolAnnotations:
    if component.annotations is None:
        new_annotations = ToolAnnotations()
    elif isinstance(component.annotations, ToolAnnotations):
        new_annotations = component.annotations.model_copy()
    else:
        try:
            new_annotations = ToolAnnotations(**component.annotations)
        except (TypeError, ValueError):
            new_annotations = ToolAnnotations()
    new_annotations.title = title
    return new_annotations


def _is_array_response(output_schema: dict[str, Any] | None) -> bool:
    if not output_schema or not isinstance(output_schema, dict):
        return False

    properties = output_schema.get("properties", {})
    if not isinstance(properties, dict):
        return False

    result_schema = properties.get("result")
    if not isinstance(result_schema, dict):
        return False

    return _schema_type_is_array(result_schema)


def _prepare_description(component: Any) -> tuple[str, bool]:
    description = getattr(component, "description", "") or ""

    params = getattr(component, "parameters", None) or {}
    props = params.get("properties", {})
    has_labels = "labels" in props and _schema_type_is_array(props["labels"])
    if has_labels and LABEL_GUIDANCE.strip() not in description:
        description += LABEL_GUIDANCE
    return description, has_labels


__all__ = [
    "_snake_to_title",
    "add_inferred_hints",
    "categorize_tool",
    "compute_invalidation_patterns",
    "generate_tool_title",
    "synthetic_annotations",
]
