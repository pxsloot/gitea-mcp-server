"""Domain-specific display formatters for resources.

All resources return raw data.  This module provides the registered
formatters that the unified display pipeline (``_format_resource_content``
in ``mcp_tools.py``) dispatches to when a ``format_hint`` is present.

Each formatter has the signature ``(data, *, detail='full') -> str``.
The ``detail`` parameter is passed through from the read_resource tool
so that ``detail=concise`` produces collapsed markdown everywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gitea_mcp_server.format import _format_as_markdown

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from gitea_mcp_server.openapi_types import OpenAPISpec


# ---------------------------------------------------------------------------
# Formatter registry
# ---------------------------------------------------------------------------

_FORMATTERS: dict[str, Callable[..., str]] = {}

_FORMATTER_META: dict[str, dict[str, Any]] = {}
"""Optional per-formatter metadata (e.g. ``{"needs_extra": True}``)."""


def register_formatter(
    name: str,
    **meta: Any,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Decorator that registers a domain-specific markdown formatter.

    Args:
        name: Unique name used as ``format_hint`` in resource metadata.
        **meta: Optional metadata (``needs_extra``, etc.) stored alongside
            the formatter for the display pipeline.

    Usage::

        @register_formatter("repository")
        def _format_repo_markdown(data, *, detail="full"):
            ...
    """
    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        _FORMATTERS[name] = fn
        if meta:
            _FORMATTER_META[name] = meta
        return fn
    return deco


def get_formatter(name: str) -> Callable[..., str] | None:
    """Look up a registered formatter by name.  Returns ``None`` if not found."""
    return _FORMATTERS.get(name)


def get_formatter_meta(name: str) -> dict[str, Any]:
    """Return metadata for a registered formatter, or empty dict."""
    return _FORMATTER_META.get(name, {})


def call_formatter(
    name: str,
    data: Any,
    *,
    detail: str = "full",
    extra: dict[str, Any] | None = None,
) -> str:
    """Look up and call a registered formatter.

    Args:
        name: Formatter name (registered via ``@register_formatter``).
        data: The data to format (already collapsed if ``detail=concise``).
        detail: Output detail level.
        extra: Optional context dict passed to formatters that need it
            (checked via ``needs_extra`` metadata flag).

    Returns:
        Markdown string.
    """
    fn = get_formatter(name)
    if fn is None:
        msg = f"No formatter registered for {name!r}"
        raise ValueError(msg)
    meta = get_formatter_meta(name)
    if meta.get("needs_extra"):
        return fn(data, detail=detail, extra=extra)
    return fn(data, detail=detail)


# ---------------------------------------------------------------------------
# Shared field filters
# ---------------------------------------------------------------------------

_ISSUE_FIELDS: Sequence[str] = [
    "number",
    "title",
    "state",
    "user",
    "created_at",
    "comments",
    "labels",
    "html_url",
]
_PULL_FIELDS: Sequence[str] = [
    "number",
    "title",
    "state",
    "user",
    "created_at",
    "base",
    "head",
    "comments",
    "html_url",
]
_REPO_FIELDS: Sequence[str] = [
    "name",
    "full_name",
    "description",
    "owner",
    "html_url",
    "default_branch",
    "stargazers_count",
    "forks_count",
    "open_issues_count",
    "size",
    "created_at",
    "updated_at",
    "topics",
    "license",
]
_USER_FIELDS: Sequence[str] = [
    "login",
    "full_name",
    "type",
    "html_url",
    "public_repos",
    "followers_count",
    "following_count",
    "created_at",
    "bio",
    "location",
    "website",
]
_RELEASE_FIELDS: Sequence[str] = [
    "tag_name",
    "name",
    "draft",
    "prerelease",
    "created_at",
    "published_at",
    "body",
]


# ---------------------------------------------------------------------------
# Domain formatters
# ---------------------------------------------------------------------------


@register_formatter("repository")
def _format_repo_markdown(data: dict, *, detail: str = "full") -> str:
    return _format_as_markdown(
        data,
        title=data.get("full_name", "Repository"),
        field_filter=_REPO_FIELDS,
        detail=detail,
    )


@register_formatter("issues")
def _format_issues_markdown(data: list, *, detail: str = "full") -> str:
    title = f"Issues - {len(data)} issues" if data else "Issues"
    return _format_as_markdown(
        data,
        title=title,
        field_filter=_ISSUE_FIELDS,
        item_title_key="title",
        detail=detail,
    )


@register_formatter("pull_requests")
def _format_pulls_markdown(data: list, *, detail: str = "full") -> str:
    title = f"Pull Requests - {len(data)} pull requests" if data else "Pull Requests"
    return _format_as_markdown(
        data,
        title=title,
        field_filter=_PULL_FIELDS,
        item_title_key="title",
        detail=detail,
    )


@register_formatter("user")
def _format_user_markdown(data: dict, *, detail: str = "full") -> str:
    # Normalize: API may return 'created_at' or 'created' for the same field
    normalized = dict(data)
    if "created_at" not in normalized and "created" in normalized:
        normalized["created_at"] = normalized["created"]
    return _format_as_markdown(
        normalized,
        title=normalized.get("login", "User"),
        field_filter=_USER_FIELDS,
        detail=detail,
    )


@register_formatter("release")
def _format_release_markdown(data: dict, *, detail: str = "full") -> str:
    return _format_as_markdown(
        data,
        title=data.get("tag_name", "Release"),
        field_filter=_RELEASE_FIELDS,
        detail=detail,
    )


@register_formatter("labels", needs_extra=True)
def _format_labels_markdown(
    data: list,
    *,
    detail: str = "full",  # noqa: ARG001 - kept for uniform formatter signature
    extra: dict[str, Any] | None = None,
) -> str:
    """Format labels list as Markdown with format and validation hints.

    Needs ``extra`` with ``owner`` and ``repo`` keys for the heading.
    """
    owner = (extra or {}).get("owner", "?")
    repo = (extra or {}).get("repo", "?")

    lines = [
        f"# Labels for {owner}/{repo}",
        "",
        f"**Total**: {len(data)} labels",
        "",
        "## Accepted Format",
        "",
        "Labels can be specified as either:",
        '- **Names** (strings): e.g. `"bug"`, `"Kind/Feature"`',
        "- **IDs** (integers): e.g. `1`, `42`",
        "",
        "**Validation**: Both names and IDs are validated against the"
        " repository's existing labels.",
        " Unknown values produce an error listing available labels.",
        "",
    ]

    if not data:
        lines.append("*No labels configured for this repository.*")
        lines.append("")
    else:
        lines.append(f"## Labels ({len(data)})")
        lines.append("")
        for label in data:
            label_id = label.get("id", "?")
            name = label.get("name", "Unnamed")
            color = label.get("color", "")
            desc = label.get("description") or "(no description)"
            exclusive = label.get("exclusive", False)

            scope_info = ""
            if "/" in name:
                scope = name.rsplit("/", 1)[0]
                scope_info = f" (scope: `{scope}`)"

            archived = label.get("is_archived", False)
            archived_tag = " *(archived)*" if archived else ""

            lines.append(f"### {name} (#{label_id}){archived_tag}")
            lines.append(f"- **Color**: `#{color}`")
            lines.append(f"- **Description**: {desc}")
            lines.append(f"- **Exclusive**: {'Yes' if exclusive else 'No'}{scope_info}")
            lines.append("")

    return "\n".join(lines)


def _build_labels_markdown(data: list, owner: str, repo: str, *, detail: str = "full") -> str:
    """Shorthand for calling the labels formatter with context."""
    return call_formatter("labels", data, detail=detail, extra={"owner": owner, "repo": repo})


# ---------------------------------------------------------------------------
# Non-data formatters (no raw data input)
# ---------------------------------------------------------------------------


def _build_server_info_markdown(openapi_spec: OpenAPISpec) -> str:
    """Build server info markdown from OpenAPI spec info block."""
    info = openapi_spec.get("info", {})
    title = info.get("title", "Unknown")
    version = info.get("version", "Unknown")
    description = info.get("description", "")
    lines = [
        "# Server Information",
        "",
        f"**Server Type**: {title}",
        f"**API Version**: {version}",
        "",
    ]
    if description:
        lines.append("## Description")
        lines.append("")
        lines.append(description)
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "_FORMATTERS",
    "_build_labels_markdown",
    "_build_server_info_markdown",
    "_format_issues_markdown",
    "_format_labels_markdown",
    "_format_pulls_markdown",
    "_format_release_markdown",
    "_format_repo_markdown",
    "_format_user_markdown",
    "call_formatter",
    "get_formatter",
    "get_formatter_meta",
    "register_formatter",
]
