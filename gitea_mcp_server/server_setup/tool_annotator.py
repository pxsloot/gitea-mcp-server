"""Tool annotation and customization utilities.

This module contains functions for customizing FastMCP tools generated from OpenAPI,
including title generation, categorization, hint inference, label handling, and
cache invalidation pattern computation.
"""

import json
import logging
import re
from collections.abc import Sequence
from typing import Annotated, Any

import httpx
from fastmcp.server.context import Context
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.search.bm25 import _BM25Index as _BaseBM25Index
from fastmcp.server.transforms.search.bm25 import _catalog_hash
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.cache_invalidation import register_tool_invalidation
from gitea_mcp_server.constants import (
    HTTP_METHODS_DESTRUCTIVE,
    HTTP_METHODS_IDEMPOTENT,
    HTTP_METHODS_SAFE,
    LABEL_GUIDANCE,
    PATTERN_FILES,
    PATTERN_ISSUES_CLOSED,
    PATTERN_ISSUES_LIST,
    PATTERN_ISSUES_OPEN,
    PATTERN_PULLS_CLOSED,
    PATTERN_PULLS_LIST,
    PATTERN_PULLS_OPEN,
    PATTERN_REPO,
    RESOURCE_NOTE,
    TITLE_TRUNCATE_LIMIT,
)
from gitea_mcp_server.server_setup.label_manager import LabelManager
from gitea_mcp_server.validation import (
    SINGLE_VALIDATORS,
    ValidationError,
    augment_schema_with_validation,
    validate_pagination,
)

logger = logging.getLogger(__name__)


def _raise_value_error(message: str) -> None:
    """Raise ValueError with pre-computed message."""
    raise ValueError(message) from None


def _raise_value_error_from(message: str, cause: Exception) -> None:
    """Raise ValueError with message and cause."""
    raise ValueError(message) from cause


def _raise_validation_error(message: str, field: str, cause: Exception) -> None:
    """Raise ValidationError with pre-computed message."""
    raise ValidationError(message, field=field) from cause


def generate_tool_title(route: Any) -> str:
    """Generate a human-readable title for a tool from its OpenAPI route metadata.

    Args:
        route: FastMCP route object with summary and operation_id attributes

    Returns:
        Title string (max TITLE_TRUNCATE_LIMIT chars, truncated with "..." if needed)
    """
    summary = getattr(route, "summary", None)
    operation_id = getattr(route, "operation_id", None)

    title: str

    # Prefer summary if available and non-empty
    if summary and summary.strip():
        title = str(summary).strip()
    elif operation_id:
        # Convert snake_case to Title Case
        words = str(operation_id).replace("_", " ").title()
        title = words
    else:
        return "Unnamed Tool"

    # Truncate to TITLE_TRUNCATE_LIMIT characters
    if len(title) > TITLE_TRUNCATE_LIMIT:
        title = title[: TITLE_TRUNCATE_LIMIT - 3] + "..."

    return title


TAG_TO_SCOPE: dict[str, str] = {
    "admin": "sudo",
    "repository": "repository",
    "issue": "issue",
    "organization": "organization",
    "user": "user",
    "notification": "notification",
    "package": "package",
    "activitypub": "activitypub",
    "miscellaneous": "misc",
    "settings": "repository",
}


def derive_required_scope(swagger_tags: set[str] | None, method: str | None) -> str | None:
    """Derive the required Gitea token scope from Swagger tags and HTTP method.

    Args:
        swagger_tags: Set of Swagger tag names from the OpenAPI spec
        method: HTTP method (GET, POST, PUT, DELETE, etc.)

    Returns:
        Scope string like "read:repository" or "write:issue",
        "sudo" for admin tools, or None if no scope is needed.
    """
    if not swagger_tags:
        return None

    scope_name = None
    for tag in swagger_tags:
        s = TAG_TO_SCOPE.get(tag)
        if s is not None:
            scope_name = s
            break

    if scope_name is None:
        return None

    if scope_name == "sudo":
        return "sudo"

    if method and method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return f"read:{scope_name}"
    return f"write:{scope_name}"


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


def categorize_tool(path: str) -> str:
    """Categorize a tool based on its OpenAPI path.

    Args:
        path: The OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")

    Returns:
        Category string: "repository", "issue", "pull_request", "user", "organization", "admin", or "misc"
    """
    for prefix, category, contains in _CATEGORY_PREFIXES:
        if contains:
            if prefix in path:
                return category
        elif path.startswith(prefix):
            return category
    return "misc"


def add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
    """Infer and add annotation hints from HTTP route properties.

    Hints are based on HTTP method semantics:
    - readOnlyHint: True for safe methods (GET, HEAD, OPTIONS)
    - destructiveHint: True for DELETE (and any method that destroys data)
    - idempotentHint: True for idempotent methods (GET, PUT, DELETE, HEAD, OPTIONS)
    - openWorldHint: Always True for Gitea tools (they interact with external server)

    Existing annotation values are preserved; only None values are set.

    Args:
        route: HTTPRoute object with method attribute
        annotations: ToolAnnotations instance to update
    """
    method = getattr(route, "method", None)

    # Only add hints if they are currently None (preserve existing manual settings)
    if annotations.readOnlyHint is None:
        annotations.readOnlyHint = method in HTTP_METHODS_SAFE

    if annotations.destructiveHint is None:
        annotations.destructiveHint = method in HTTP_METHODS_DESTRUCTIVE

    if annotations.idempotentHint is None:
        annotations.idempotentHint = method in HTTP_METHODS_IDEMPOTENT

    if annotations.openWorldHint is None:
        # All Gitea MCP tools interact with external Gitea server
        annotations.openWorldHint = True


_INVALIDATION_PATTERNS: list[tuple[str, str | None, list[str]]] = [
    (
        "/repos/{owner}/{repo}/issues",
        None,
        [PATTERN_ISSUES_LIST, PATTERN_ISSUES_OPEN, PATTERN_ISSUES_CLOSED],
    ),
    (
        "/repos/{owner}/{repo}/pulls",
        None,
        [PATTERN_PULLS_LIST, PATTERN_PULLS_OPEN, PATTERN_PULLS_CLOSED],
    ),
    ("/repos/{owner}/{repo}", "exact", [PATTERN_REPO]),
    ("/repos/{owner}/{repo}/contents", None, [PATTERN_FILES]),
    ("/repos/{owner}/{repo}/labels", None, [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]),
    ("/repos/{owner}/{repo}/milestones", None, [PATTERN_ISSUES_LIST, PATTERN_PULLS_LIST]),
    ("/repos/{owner}/{repo}/releases", None, [PATTERN_REPO]),
    ("/repos/{owner}/{repo}/topics", None, [PATTERN_REPO]),
]


def compute_invalidation_patterns(path: str, method: str) -> list[str]:
    """Compute resource invalidation patterns for a tool based on its path and method.

    This function analyzes the OpenAPI path and HTTP method to determine which
    MCP resource patterns should be invalidated when this tool is called.

    Args:
        path: OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)

    Returns:
        List of pattern names (keys in RESOURCE_PATTERNS) to invalidate.
        Empty list if no invalidation needed.
    """
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return []

    for prefix, match_type, patterns in _INVALIDATION_PATTERNS:
        if match_type == "exact":
            if path == prefix:
                return patterns
        elif path.startswith(prefix):
            return patterns
    return []


def update_labels_schema(component: OpenAPITool) -> None:
    """Update the tool's schema to show that labels accept both string and integer types.

    The OpenAPI spec defines labels as array of integers, but the runtime accepts strings
    (with auto-conversion). This function updates the JSON schema to reflect the actual
    accepted types: ["string", "integer"].

    Args:
        component: OpenAPITool to update
    """
    params = getattr(component, "parameters", None)
    if not params:
        return

    props = params.get("properties", {})
    if "labels" not in props:
        return

    labels_schema = props["labels"]
    if labels_schema.get("type") != "array":
        return

    # Get or create items schema
    items_schema = labels_schema.get("items", {})

    # Update items.type to accept both string and integer
    current_type = items_schema.get("type")
    if current_type == "integer":
        # Change from "integer" to ["string", "integer"]
        items_schema["type"] = ["string", "integer"]
    elif current_type == "string":
        # Already accepts string; might as well add integer for completeness
        items_schema["type"] = ["string", "integer"]
    # If already a list, don't modify (could be already set)


def _resolve_ref(openapi_spec: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Resolve a $ref pointer in an OpenAPI spec.

    Args:
        openapi_spec: The OpenAPI specification dictionary
        ref: The $ref path (e.g., "#/components/responses/NotFound")

    Returns:
        The resolved component, or None if not found.
    """
    parts = ref.lstrip("#/").split("/")
    current: dict[str, Any] | None = openapi_spec
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _lookup_response_description(
    openapi_spec: dict[str, Any],
    route: Any,
    status_code: int,
) -> str:
    """Look up the response description for a given route and HTTP status code.

    Args:
        openapi_spec: The OpenAPI specification dictionary
        route: The OpenAPI route object (with path and method attributes)
        status_code: HTTP status code (e.g., 404, 422)

    Returns:
        The response description string, or a generic fallback if not found.
    """
    fallback = f"HTTP error {status_code}"
    result = fallback
    try:
        paths = openapi_spec.get("paths", {})
        path_item = paths.get(route.path)
        if not path_item:
            result = fallback
        else:
            method = getattr(route, "method", "").lower()
            operation = path_item.get(method) if method else None
            if not operation:
                result = fallback
            else:
                responses = operation.get("responses", {})
                response_def = responses.get(str(status_code))
                if not response_def or not isinstance(response_def, dict):
                    result = fallback
                elif "description" in response_def:
                    result = str(response_def["description"])
                elif "$ref" in response_def:
                    resolved = _resolve_ref(openapi_spec, response_def["$ref"])
                    if isinstance(resolved, dict):
                        desc = resolved.get("description")
                        result = str(desc) if desc else fallback
    except (KeyError, TypeError, AttributeError, ValueError):
        result = fallback
    return result


def _run_validation(kwargs: dict[str, Any]) -> None:
    """Run runtime validation on tool arguments."""
    for name, value in kwargs.items():
        if name in SINGLE_VALIDATORS:
            try:
                SINGLE_VALIDATORS[name](value, field=name)
            except ValidationError:
                raise
            except (TypeError, ValueError, KeyError) as e:
                msg = f"Validation error for {name}: {e}"
                _raise_validation_error(msg, name, e)
    if "page" in kwargs or "per_page" in kwargs:
        validate_pagination(kwargs.get("page"), kwargs.get("per_page"))


async def _convert_labels(
    kwargs: dict[str, Any],
    has_labels: bool,
    component: Any,
    label_manager: LabelManager,
) -> None:
    """Convert label names to IDs if needed."""
    if not has_labels:
        return
    labels = kwargs.get("labels", [])
    if not labels or all(isinstance(label, int) for label in labels):
        return

    owner = kwargs.get("owner") or kwargs.get("org")
    repo = kwargs.get("repo")
    if not owner or not repo:
        return

    client = getattr(component, "_client", None)
    if client is None:
        return

    label_map = await label_manager.get_label_map(owner, repo, client)
    converted = []
    unknown = []
    for label in labels:
        if isinstance(label, str):
            label_lower = label.lower()
            if label_lower in label_map:
                converted.append(label_map[label_lower]["id"])
            else:
                unknown.append(label)
        else:
            converted.append(label)

    if unknown:
        available = sorted(label_map.keys())
        msg = (
            f"Unknown label(s): {unknown}. "
            f"Available labels: {', '.join(available)}. "
            f"Use list_labels(owner, repo) or read gitea://repos/{owner}/{repo}/labels to see details."
        )
        _raise_value_error(msg)

    kwargs["labels"] = converted


async def _run_with_error_handling(
    kwargs: dict[str, Any],
    component: Any,
    route: Any,
    openapi_spec: dict[str, Any] | None,
) -> Any:
    """Run the component with enhanced error handling."""
    try:
        return await component.run(kwargs)
    except ValueError as e:
        cause = e.__cause__
        if isinstance(cause, httpx.HTTPStatusError) and openapi_spec is not None:
            status_code = cause.response.status_code
            description = _lookup_response_description(openapi_spec, route, status_code)
            try:
                error_body = cause.response.json()
                message = error_body.get("message", "")
                formatted = f"{description}\n\nDetails: {message}" if message else description
            except (ValueError, AttributeError):
                formatted = f"{description}\n\nDetails: {cause.response.text[:200]}"
            raise ValueError(formatted) from e
        raise
    except httpx.HTTPError as e:
        formatted = f"Network error: Could not reach the Gitea server.\n\nDetails: {e!s}"
        _raise_value_error_from(formatted, e)
    except (KeyError, TypeError, AttributeError, RuntimeError):
        logger.exception("Unexpected error during tool execution")
        _raise_value_error(
            "An unexpected error occurred. Please check the server logs for details."
        )


def _prepare_annotations(component: Any, title: str) -> ToolAnnotations:
    """Prepare tool annotations from component."""
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


def _prepare_description(annotations: ToolAnnotations, component: Any) -> tuple[str, bool]:
    """Prepare tool description and return has_labels flag."""
    description = getattr(component, "description", "") or ""
    if annotations.readOnlyHint and RESOURCE_NOTE not in description:
        description += RESOURCE_NOTE

    params = getattr(component, "parameters", None) or {}
    props = params.get("properties", {})
    has_labels = "labels" in props and props["labels"].get("type") == "array"
    if has_labels and LABEL_GUIDANCE.strip() not in description:
        description += LABEL_GUIDANCE
    return description, has_labels


def customize_component(
    route: Any,
    component: Any,
    label_manager: LabelManager,
    openapi_spec: dict[str, Any] | None = None,
) -> Tool | None:
    """Customize FastMCP components with tool annotations.

    This function is called by FastMCP's from_openapi for each generated component.
    It adds title, category, hints, invalidation patterns, and label handling.

     Args:
         route: The OpenAPI route object
         component: The generated FastMCP component (tool, resource, etc.)
         label_manager: LabelManager instance for label validation
         openapi_spec: Optional OpenAPI spec dictionary for enhanced error handling.
                      If provided, HTTP errors from component.run will be formatted
                      using the spec's response descriptions.

     Returns:
         A new Tool instance with customizations applied, or None if the component is not an OpenAPITool.
    """
    if not isinstance(component, OpenAPITool):
        return None

    title = generate_tool_title(route)
    category = categorize_tool(route.path)

    tags = (set(component.tags) if component.tags else set()) | {category}

    annotations = _prepare_annotations(component, title)
    add_inferred_hints(route, annotations)

    method = getattr(route, "method", None)
    if method:
        patterns = compute_invalidation_patterns(route.path, method)
        if patterns:
            register_tool_invalidation(component.name, patterns)

    required_scope = derive_required_scope(
        set(component.tags) if component.tags else None,
        method,
    )

    description, has_labels = _prepare_description(annotations, component)

    augment_schema_with_validation(component)
    if has_labels:
        update_labels_schema(component)

    async def transform_fn(**kwargs: Any) -> Any:
        _run_validation(kwargs)
        await _convert_labels(kwargs, has_labels, component, label_manager)
        return await _run_with_error_handling(kwargs, component, route, openapi_spec)

    component_meta = dict(component.meta) if component.meta else {}
    component_meta.setdefault("fastmcp", {}).setdefault("_internal", {})[
        "required_scope"
    ] = required_scope

    return Tool.from_tool(
        component,
        title=title,
        tags=tags,
        annotations=annotations,
        description=description,
        transform_fn=transform_fn,
        meta=component_meta,
    )


def _compact_search_serializer(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Return minimal tool info for search results to avoid massive payloads.

    Only includes name, description, and a simplified parameters schema.
    """
    result = []
    for tool in tools:
        # Simplify parameters: keep property names and basic types, drop detailed descriptions
        params = tool.parameters or {}
        if "properties" in params:
            simple_props = {}
            for name, info in params["properties"].items():
                if isinstance(info, dict):
                    simple_props[name] = {"type": info.get("type", "any")}
                else:
                    simple_props[name] = {"type": "any"}
            simple_params = {
                "properties": simple_props,
                "required": params.get("required", []),
            }
        else:
            simple_params = params

        result.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": simple_params,
            }
        )
    return result


MIN_TOKEN_LENGTH = 2


def _tokenize_len2(text: str) -> list[str]:
    """Tokenize with support for 2-character tokens like 'pr'."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= MIN_TOKEN_LENGTH]


class _BM25IndexLen2(_BaseBM25Index):
    """BM25 index that supports 2-character tokens."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        super().__init__(k1, b)

    def build(self, documents: list[str]) -> None:
        self._doc_tokens = [_tokenize_len2(doc) for doc in documents]
        self._doc_lengths = [len(tokens) for tokens in self._doc_tokens]
        self._n = len(documents)
        self._avg_dl = sum(self._doc_lengths) / self._n if self._n else 0.0

        self._df = {}
        self._tf = []
        for tokens in self._doc_tokens:
            tf: dict[str, int] = {}
            seen: set[str] = set()
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
                if token not in seen:
                    self._df[token] = self._df.get(token, 0) + 1
                    seen.add(token)
            self._tf.append(tf)


NAME_BOOST = 3

CATEGORY_SEARCH_ALIASES = {
    "pull_request": "pull request pr",
    "issue": "issue issues bug",
    "repository": "repo repository repos",
    "repo": "repo repository repos",
    "organization": "org organization team",
    "org": "org organization team",
    "user": "user users account",
}


def _expand_word_aliases(text: str) -> str:
    """Expand common abbreviations and fragments for better search matching.

    BM25 uses whitespace tokenization, so singular/plural variations like
    "repo"/"repos" don't match unless both forms are present.
    """
    alias_expansions = [
        ("repo", "repo repository repos"),
        ("pr", "pr pull request"),
        ("current", "current authenticated"),
        ("user", "user users account"),
    ]
    text_lower = text.lower()
    parts = [text]
    for word, expansion in alias_expansions:
        if word in text_lower:
            parts.append(expansion)
    return " ".join(parts)


def _extract_searchable_text_enhanced(tool: Tool) -> str:
    """Enhanced searchable text extraction for better tool discoverability.

    Includes:
    - Tool name
    - Description
    - Parameter names and descriptions
    - Tags with expanded aliases (e.g., "pull_request" -> "pull request pr")
    - Title
    - Word aliases for singular/plural variations
    """
    parts = [tool.name] * NAME_BOOST

    if tool.annotations and tool.annotations.title:
        parts.append(tool.annotations.title)

    if tool.description:
        parts.append(tool.description)

    schema = tool.parameters
    if schema:
        properties = schema.get("properties", {})
        for param_name, param_info in properties.items():
            parts.append(param_name)
            if isinstance(param_info, dict):
                desc = param_info.get("description", "")
                if desc:
                    parts.append(desc)

    if tool.tags:
        for tag in tool.tags:
            parts.append(tag)
            if tag in CATEGORY_SEARCH_ALIASES:
                parts.append(CATEGORY_SEARCH_ALIASES[tag])

    result = " ".join(parts)
    return _expand_word_aliases(result)


class TolerantBM25SearchTransform(BM25SearchTransform):
    """BM25SearchTransform with tolerant argument handling for OpenCode compatibility.

    Override the synthetic call_tool to accept any arguments (including JSON strings).
    Uses a compact result serializer to avoid massive payloads.
    Enhanced searchable text extraction for better Pr/issue discoverability.
    """

    def __init__(self, **kwargs: Any) -> None:
        # Force our compact serializer if not provided
        if "search_result_serializer" not in kwargs:
            kwargs["search_result_serializer"] = _compact_search_serializer
        super().__init__(**kwargs)

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        """Override to use enhanced searchable text extraction."""
        current_hash = _catalog_hash(tools)
        if current_hash != self._last_hash:
            documents = [_extract_searchable_text_enhanced(t) for t in tools]
            new_index = _BM25IndexLen2(self._index.k1, self._index.b)
            new_index.build(documents)
            self._index, self._indexed_tools, self._last_hash = (
                new_index,
                tools,
                current_hash,
            )

        expanded_query = _expand_word_aliases(query)
        indices = self._index.query(expanded_query, self._max_results)
        return [self._indexed_tools[i] for i in indices]

    def _make_call_tool(self) -> Tool:
        """Create the call_tool proxy that executes discovered tools."""
        transform = self

        async def call_tool(
            name: Annotated[str, "The name of the tool to call"],
            arguments: Annotated[Any, "Arguments to pass to the tool (dict or JSON string)"] = None,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Call a tool by name with the given arguments.

            Use this to execute tools discovered via search_tools.
            """
            if name in {transform._call_tool_name, transform._search_tool_name}:
                msg = f"'{name}' is a synthetic search tool and cannot be called via the call_tool proxy"
                _raise_value_error(msg)
            # If arguments is a string, attempt to parse as JSON
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as e:
                    msg = f"Invalid JSON in arguments: {e}"
                    _raise_value_error_from(msg, e)
            # Ensure arguments is a dict (or None)
            if arguments is not None and not isinstance(arguments, dict):
                msg = f"Arguments must be a dict or JSON string, got {type(arguments).__name__}"
                _raise_value_error(msg)
            return await ctx.fastmcp.call_tool(name, arguments)

        return Tool.from_function(fn=call_tool, name=self._call_tool_name)


__all__ = [
    "TAG_TO_SCOPE",
    "TolerantBM25SearchTransform",
    "add_inferred_hints",
    "categorize_tool",
    "compute_invalidation_patterns",
    "customize_component",
    "derive_required_scope",
    "generate_tool_title",
    "update_labels_schema",
]
