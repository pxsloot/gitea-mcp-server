"""Domain-specific display formatters for resources.

All resources return raw data.  This module provides the registered
formatters that the ``read_resource`` executor (``tools/mcp_tools.py``)
resolves into ``markdown_formatter`` callables for the single result
pipeline (``tools/result_pipeline.py``) when a ``format_hint`` is present.

Formatters are **pure renderers** — they take ``data`` and optionally
``extra`` (formatter context) and/or ``detail`` (for rendering decisions),
declaring only the keyword params they use.  The pipeline dispatches through
``call_markdown_formatter`` (``format.py``), which inspects each signature
once and passes exactly the accepted kwargs.

**Invariant**: when ``detail="concise"`` and the result carries a schema, the
pipeline pre-collapses the page (schema-aware ``$ref`` collapse) *before*
calling the formatter — formatters receive already-collapsed data (nested
``$ref``-backed objects are ``"$ref:TypeName"`` strings).  Formatters must
not re-collapse; a formatter that renders collapsed items differently (e.g.
``_format_labels_markdown``) declares ``detail`` and renders the collapsed
items as-is.
"""

from collections.abc import Callable
from typing import Any

from gitea_mcp_server.format import call_markdown_formatter, format_as_markdown

# ---------------------------------------------------------------------------
# Formatter registry
# ---------------------------------------------------------------------------

_FORMATTERS: dict[str, Callable[..., str]] = {}


def register_formatter(
    name: str,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Decorator that registers a domain-specific markdown formatter.

    Args:
        name: Unique name used as ``format_hint`` in resource metadata.

    Usage::

        @register_formatter("repository")
        def _format_repo_markdown(data): ...
    """

    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        _FORMATTERS[name] = fn
        return fn

    return deco


def get_formatter(name: str) -> Callable[..., str] | None:
    """Look up a registered formatter by name.  Returns ``None`` if not found."""
    return _FORMATTERS.get(name)


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
        extra: Optional context dict passed to formatters that need it.

    Returns:
        Markdown string.
    """
    fn = get_formatter(name)
    if fn is None:
        msg = f"No formatter registered for {name!r}"
        raise ValueError(msg)
    return call_markdown_formatter(fn, data, detail=detail, extra=extra)


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
def _format_repo_markdown(data: dict) -> str:
    return format_as_markdown(
        data,
        title=data.get("full_name", "Repository"),
        field_filter=_REPO_FIELDS,
    )


@register_formatter("issues")
def _format_issues_markdown(data: list, *, extra: dict | None = None) -> str:
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
            (any(isinstance(item, dict) and item.get("pull_request") for item in data))
            if data
            else False
        )
        title_label = "Issues and Pull Requests" if has_prs else "Issues"
    title = f"{title_label} - {len(data)} items" if data else title_label
    return format_as_markdown(
        data,
        title=title,
        field_filter=_ISSUE_FIELDS,
        item_title_key="title",
    )


@register_formatter("pull_requests")
def _format_pulls_markdown(data: list) -> str:
    title = f"Pull Requests - {len(data)} items" if data else "Pull Requests"
    return format_as_markdown(
        data,
        title=title,
        field_filter=_PULL_FIELDS,
        item_title_key="title",
    )


@register_formatter("user")
def _format_user_markdown(data: Any) -> str:
    # Guard against non-dict input (unexpected data shape).
    if not isinstance(data, dict):
        # Show the type and a truncated repr so agents can still reason
        # about what was returned, without producing a misleading login
        # field (e.g. ``str([...])`` for list input).
        fallback_data = {
            "_type": type(data).__name__,
            "_raw": str(data)[:500],
        }
        return format_as_markdown(fallback_data, title="User")
    # Normalize: API may return 'created_at' or 'created' for the same field
    normalized = dict(data)
    if "created_at" not in normalized and "created" in normalized:
        normalized["created_at"] = normalized["created"]
    return format_as_markdown(
        normalized,
        title=normalized.get("login", "User"),
        field_filter=_USER_FIELDS,
    )


@register_formatter("release")
def _format_release_markdown(data: list) -> str:
    """Format a list of releases as markdown."""
    title = f"Releases - {len(data)} releases" if data else "Releases"
    return format_as_markdown(
        data,
        title=title,
        field_filter=_RELEASE_FIELDS,
        item_title_key="tag_name",
    )


@register_formatter("labels")
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
    "call_formatter",
    "get_formatter",
    "register_formatter",
]
