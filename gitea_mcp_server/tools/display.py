"""Domain-specific display formatters for resources.

All resources return raw data.  This module provides the registered
formatters that the unified display pipeline (``_format_resource_content``
in ``tools/resource_display.py``) dispatches to when a ``format_hint`` is present.

Each formatter has the signature ``(data, *, detail='full') -> str``.
The ``detail`` parameter is passed through from the read_resource tool
so that ``detail=concise`` produces collapsed markdown everywhere.
"""

from collections.abc import Callable
from typing import Any

from gitea_mcp_server.format import _format_as_markdown

# ---------------------------------------------------------------------------
# Formatter registry
# ---------------------------------------------------------------------------

_FORMATTERS: dict[str, Callable[..., str]] = {}

_FORMATTER_META: dict[str, dict[str, Any]] = {}
"""Optional per-formatter metadata (e.g. ``{"need_extra": True}``)."""


def register_formatter(
    name: str,
    **meta: Any,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Decorator that registers a domain-specific markdown formatter.

    Args:
        name: Unique name used as ``format_hint`` in resource metadata.
        **meta: Optional metadata (``need_extra``, etc.) stored alongside
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
            (checked via ``need_extra`` metadata flag).

    Returns:
        Markdown string.
    """
    fn = get_formatter(name)
    if fn is None:
        msg = f"No formatter registered for {name!r}"
        raise ValueError(msg)
    meta = get_formatter_meta(name)
    if meta.get("need_extra"):
        return fn(data, detail=detail, extra=extra)
    return fn(data, detail=detail)


# ---------------------------------------------------------------------------
# Shared field specifications
# Each entry maps a field name to a dict of render hints:
#   {}                         — default (expand nested, scalars as-is)
#   {"render": "compact_ref",  — render nested dict as flat table row
#    "template": "{k1}/{k2}"}    using template expansion
#   {"render": "badge"}        — render as Yes/No indicator
# ---------------------------------------------------------------------------

_ISSUE_FIELDS: dict[str, dict] = {
    "number": {},
    "title": {},
    "state": {},
    "user": {},
    "created_at": {},
    "pull_request": {"render": "badge"},
    "comments": {},
    "labels": {"render": "compact_ref", "template": "{name}"},
    "html_url": {},
}
_PULL_FIELDS: dict[str, dict] = {
    "number": {},
    "title": {},
    "state": {},
    "user": {},
    "created_at": {},
    # Gitea's base/head objects use `ref` for the branch name and nest
    # owner/repo under `repo.owner.login`/`repo.name`.  Flat `{ref}` is
    # universally available; owner/repo context is visible on the PR itself.
    "base": {"render": "compact_ref", "template": "{ref}"},
    "head": {"render": "compact_ref", "template": "{ref}"},
    "comments": {},
    "html_url": {},
}
_REPO_FIELDS: dict[str, dict] = {
    "name": {},
    "full_name": {},
    "description": {},
    # Owner is a full User object (10+ fields). Compact to just the login.
    "owner": {"render": "compact_ref", "template": "{login}"},
    "html_url": {},
    "default_branch": {},
    "stargazers_count": {},
    "forks_count": {},
    "open_issues_count": {},
    "size": {},
    "created_at": {},
    "updated_at": {},
    "topics": {},
    "license": {},
}
_USER_FIELDS: dict[str, dict] = {
    "login": {},
    "full_name": {},
    "type": {},
    "html_url": {},
    "public_repos": {},
    "followers_count": {},
    "following_count": {},
    "created_at": {},
    "bio": {},
    "location": {},
    "website": {},
}
_RELEASE_FIELDS: dict[str, dict] = {
    "tag_name": {},
    "name": {},
    "draft": {},
    "prerelease": {},
    "created_at": {},
    "published_at": {},
    "body": {},
}


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


@register_formatter("issues", need_extra=True)
def _format_issues_markdown(data: list, *, detail: str = "full", extra: dict | None = None) -> str:
    # The /issues endpoint returns both issues and pull requests by default.
    # When available, use the ``type`` query param from the handler context
    # (forwarded via content meta → extra dict) to determine the title
    # without scanning the full data list.
    #
    #   type="issues" → "Issues"
    #   type="pulls"  → "Pull Requests"
    #   absent/None  → "Issues and Pull Requests" (mixed, the default)
    if extra and extra.get("type"):
        type_value = extra["type"]
        title_label = "Pull Requests" if type_value == "pulls" else "Issues"
    elif data and isinstance(data[0], str):
        # When items are collapsed to ``$ref`` strings (``detail=concise``),
        # we can't scan; fall back to the safe default.
        title_label = "Issues and Pull Requests"
    else:
        # Guard against non-dict items (unexpected data shape).
        has_prs = (
            any(isinstance(item, dict) and item.get("pull_request") for item in data)
        ) if data else False
        title_label = "Issues and Pull Requests" if has_prs else "Issues"
    title = f"{title_label} - {len(data)} items" if data else title_label
    return _format_as_markdown(
        data,
        title=title,
        field_filter=_ISSUE_FIELDS,
        item_title_key="title",
        detail=detail,
    )


@register_formatter("pull_requests")
def _format_pulls_markdown(data: list, *, detail: str = "full") -> str:
    title = f"Pull Requests - {len(data)} items" if data else "Pull Requests"
    return _format_as_markdown(
        data,
        title=title,
        field_filter=_PULL_FIELDS,
        item_title_key="title",
        detail=detail,
    )


@register_formatter("user")
def _format_user_markdown(data: Any, *, detail: str = "full") -> str:
    # Guard against non-dict input (unexpected data shape).
    if not isinstance(data, dict):
        # Show the type and a truncated repr so agents can still reason
        # about what was returned, without producing a misleading login
        # field (e.g. ``str([...])`` for list input).
        fallback_data = {
            "_type": type(data).__name__,
            "_raw": str(data)[:500],
        }
        return _format_as_markdown(fallback_data, title="User", detail=detail)
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
def _format_release_markdown(data: list, *, detail: str = "full") -> str:
    """Format a list of releases as markdown."""
    title = f"Releases - {len(data)} releases" if data else "Releases"
    return _format_as_markdown(
        data,
        title=title,
        field_filter=_RELEASE_FIELDS,
        item_title_key="tag_name",
        detail=detail,
    )


@register_formatter("labels", need_extra=True)
def _format_labels_markdown(
    data: list,
    *,
    detail: str = "full",
    extra: dict[str, Any] | None = None,
) -> str:
    """Format labels list as Markdown with format and validation hints.

    Needs ``extra`` with ``owner`` and ``repo`` keys for the heading.

    When ``detail=concise``, the data items may be collapsed to ``$ref:Label``
    strings by the display pipeline before reaching this formatter — the
    per-label detail section is replaced with a compact summary.
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
    elif detail == "concise":
        # Pre-collapsed items: the display pipeline collapses nested
        # objects to ``$ref:Label`` strings before reaching this
        # formatter.  Show a compact listing with type-name items
        # instead of full per-label detail.
        lines.append(f"## Labels ({len(data)})")
        lines.append("")
        for label in data:
            lines.append(f"- {label}")
        lines.append("")
    else:
        lines.append(f"## Labels ({len(data)})")
        lines.append("")
        for label in data:
            if not isinstance(label, dict):
                # Guard against non-dict items (unexpected data shape).
                lines.append(f"- {label}")
                continue
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


__all__ = [
    "_build_labels_markdown",
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
