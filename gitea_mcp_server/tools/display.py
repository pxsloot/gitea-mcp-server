"""Domain-specific display formatters for resources and type-bound tools.

All resources and tools return raw data.  This module provides the registered
formatters that the single result pipeline (``tools/result_pipeline.py``)
resolves into the markdown channel through two paths:

- **Resources** — the ``read_resource`` executor (``tools/mcp_tools.py``)
  resolves the resource's ``format_hint`` (content metadata) into an
  ``ExecutionResult.markdown_formatter`` and forwards the remaining content
  meta (``owner``/``repo``/``type``) as ``ExecutionResult.extra``.
- **Tools** — ``format.resolve_formatter`` binds a formatter by the response
  schema's root type name (the ``types=`` argument below, issue #760), so a
  tool renders the same domain view as its resource sibling.

This module is a pure plugin set: the registry itself lives in
``format.py`` (the format layer) so the result pipeline resolves formatters
without importing this module — Result → Format → Display.  Each formatter
below registers via :func:`~gitea_mcp_server.format.register_formatter`.

Every formatter here is a :data:`~gitea_mcp_server.format.MarkdownFormatter`
— a pure renderer that takes ``data`` and may declare a keyword-only
``extra`` for context.  The contract itself (and why ``detail`` is not part
of it) is stated canonically in ``format.py``; this module does not restate
it.

Formatters are *shape-tolerant*: a list renders the **collection view** (the
curated per-item table the resource surface has always shown); a dict renders
the **detail view** — the collection fields plus the payload fields (``body``,
``milestone``, …) that a single-resource read must never drop.  The pipeline
may hand a formatter either shape: list tools and list resources produce
lists, detail tools (``repo_get``, ``issue_get_issue``, …) produce dicts.
"""

from typing import Any

from gitea_mcp_server.format import (
    format_as_markdown,
    register_formatter,
)

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

# Detail views: a *dict* result renders the collection fields plus the fields
# a single-resource read must never drop — issue/pull keep their payload
# (``body``, milestone, assignees, merge state); repo/user keep visibility,
# permissions, and profile fields.  Reached when a *dict* arrives
# (``issue_get_issue``, ``repo_get``, …); the collection whitelists above stay
# untouched (resource parity).  Stale collection field names are intentionally
# left to the whitelist-drift cleanup, not fixed here.
_REPO_DETAIL_FIELDS: dict[str, dict] = {
    **_REPO_FIELDS,
    "id": {},
    "private": {},
    "fork": {},
    "mirror": {},
    "archived": {},
    "template": {},
    "internal": {},
    "empty": {},
    "permissions": {
        "render": "compact_ref",
        "template": "admin={admin}, push={push}, pull={pull}",
    },
    "website": {},
    "language": {},
    "watchers_count": {},
    "open_pr_counter": {},
    "release_counter": {},
    "has_issues": {},
    "has_wiki": {},
    "has_pull_requests": {},
    "has_projects": {},
    "has_releases": {},
    "has_packages": {},
    "has_actions": {},
    "archived_at": {},
}
# Profile fields a single-user read must keep (email, avatar, account state).
_USER_DETAIL_FIELDS: dict[str, dict] = {
    **_USER_FIELDS,
    "id": {},
    "email": {},
    "avatar_url": {},
    "description": {},
    "visibility": {},
    "is_admin": {},
    "restricted": {},
    "active": {},
    "last_login": {},
    "starred_repos_count": {},
    "pronouns": {},
}
_ISSUE_DETAIL_FIELDS: dict[str, dict] = {
    "number": {},
    "title": {},
    "state": {},
    "user": {},
    "body": {},
    "labels": {"render": "compact_ref", "template": "{name}"},
    "milestone": {},
    "assignee": {},
    "assignees": {},
    "comments": {},
    "created_at": {},
    "updated_at": {},
    "closed_at": {},
    "due_date": {},
    # Expanded (not badge) here: PullRequestMeta's merged/draft state matters
    # on a detail read of an issue that is a PR.
    "pull_request": {},
    "ref": {},
    "html_url": {},
}
_PULL_DETAIL_FIELDS: dict[str, dict] = {
    "number": {},
    "title": {},
    "state": {},
    "user": {},
    "body": {},
    "labels": {"render": "compact_ref", "template": "{name}"},
    "milestone": {},
    "assignees": {},
    "base": {"render": "compact_ref", "template": "{ref}"},
    "head": {"render": "compact_ref", "template": "{ref}"},
    "comments": {},
    "review_comments": {},
    "draft": {},
    "mergeable": {},
    "merged": {},
    "merged_at": {},
    "closed_at": {},
    "created_at": {},
    "updated_at": {},
    "html_url": {},
}


# ---------------------------------------------------------------------------
# Domain formatters
# ---------------------------------------------------------------------------


@register_formatter("repository", types=["Repository"])
def _format_repo_markdown(data: Any) -> str:
    if isinstance(data, list):
        # Collection view (repo_list_* siblings, org_list_repos, …).
        title = f"Repositories - {len(data)} items" if data else "Repositories"
        return format_as_markdown(
            data,
            title=title,
            field_filter=_REPO_FIELDS,
            item_title_key="full_name",
        )
    if isinstance(data, dict):
        return format_as_markdown(
            data,
            title=data.get("full_name", "Repository"),
            field_filter=_REPO_DETAIL_FIELDS,
        )
    # Unexpected shape: render through the generic path so the agent still
    # sees the payload (#574 guard discipline).
    return format_as_markdown(data, title="Repository")


@register_formatter("issues", types=["Issue"])
def _format_issues_markdown(data: Any, *, extra: dict | None = None) -> str:
    if isinstance(data, dict):
        return _format_issue_detail(data, extra=extra)
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
    else:
        # Guard against non-dict items (unexpected data shape).  Under
        # detail=concise items are summarized dicts (#759), never bare
        # $ref strings, so the scan sees real ``pull_request`` fields.
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


def _format_issue_detail(data: dict, *, extra: dict | None = None) -> str:
    """Detail view for a single Issue dict (``issue_get_issue``, #760).

    The collection whitelist drops ``body`` — the payload of a detail read —
    so this view renders the detail field set instead.  An issue that is a
    pull request (``pull_request`` set, or ``type=pulls`` context) is titled
    accordingly.
    """
    is_pr = bool(data.get("pull_request")) or (extra or {}).get("type") == "pulls"
    label = "Pull Request" if is_pr else "Issue"
    number = data.get("number", "?")
    title = data.get("title")
    heading = f"{label} #{number}: {title}" if title else f"{label} #{number}"
    return format_as_markdown(
        data,
        title=heading,
        field_filter=_ISSUE_DETAIL_FIELDS,
    )


@register_formatter("pull_requests", types=["PullRequest"])
def _format_pulls_markdown(data: Any) -> str:
    if isinstance(data, dict):
        # Detail view (repo_get_pull_request, the /pulls/{index} resource).
        number = data.get("number", "?")
        title = data.get("title")
        heading = f"Pull Request #{number}: {title}" if title else f"Pull Request #{number}"
        return format_as_markdown(
            data,
            title=heading,
            field_filter=_PULL_DETAIL_FIELDS,
        )
    title = f"Pull Requests - {len(data)} items" if data else "Pull Requests"
    return format_as_markdown(
        data,
        title=title,
        field_filter=_PULL_FIELDS,
        item_title_key="title",
    )


@register_formatter("user", types=["User", "Organization"])
def _format_user_markdown(data: Any) -> str:
    if isinstance(data, list):
        # Collection view (org_list_members, user_list_followers, …).
        items = [_normalize_user(item) for item in data]
        title = f"Users - {len(data)} items" if data else "Users"
        return format_as_markdown(
            items,
            title=title,
            field_filter=_USER_FIELDS,
            item_title_key="login",
        )
    # Guard against non-dict input (unexpected data shape).
    if not isinstance(data, dict):
        # Show the type and a truncated repr so agents can still reason
        # about what was returned, without producing a misleading login
        # field (e.g. ``str(42)`` for scalar input).
        fallback_data = {
            "_type": type(data).__name__,
            "_raw": str(data)[:500],
        }
        return format_as_markdown(fallback_data, title="User")
    normalized = _normalize_user(data)
    return format_as_markdown(
        normalized,
        title=normalized.get("login", "User"),
        field_filter=_USER_DETAIL_FIELDS,
    )


def _normalize_user(item: Any) -> Any:
    """Normalize the API's ``created``/``created_at`` aliasing for display.

    The User schema emits ``created``; the field spec uses ``created_at``.
    Returns a copy (never mutates the executor's data); non-dict items pass
    through untouched (the generic renderer handles them).
    """
    if isinstance(item, dict) and "created_at" not in item and "created" in item:
        normalized = dict(item)
        normalized["created_at"] = normalized["created"]
        return normalized
    return item


@register_formatter("release", types=["Release"])
def _format_release_markdown(data: Any) -> str:
    """Format a release dict or a list of releases as markdown."""
    if isinstance(data, dict):
        # Detail view (release_get): _RELEASE_FIELDS already carries `body`
        # (the release notes), so the collection set doubles as the detail set.
        tag = data.get("tag_name") or data.get("name") or "Release"
        return format_as_markdown(
            data,
            title=f"Release {tag}",
            field_filter=_RELEASE_FIELDS,
        )
    title = f"Releases - {len(data)} releases" if data else "Releases"
    return format_as_markdown(
        data,
        title=title,
        field_filter=_RELEASE_FIELDS,
        item_title_key="tag_name",
    )


@register_formatter("labels", types=["Label"])
def _format_labels_markdown(
    data: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> str:
    """Format labels as Markdown with format and validation hints.

    Needs ``extra`` with ``owner`` and ``repo`` keys for the heading.

    The formatter is a pure renderer and does not know the requested
    ``detail`` level: under ``detail=concise`` the pipeline summarizes
    items (#759) — the ``Label`` schema has no nested ``$ref`` fields, so
    a concise item is the full scalar dict and renders here unchanged.
    Items are dicts on both detail levels; the non-dict branch below is a
    defensive guard for unexpected shapes, not the collapse contract.

    A *dict* arrives from single-label reads (``issue_get_label``, #760)
    and renders the detail view instead of the collection list.
    """
    if isinstance(data, dict):
        return _format_label_detail(data, extra=extra)

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
            if not isinstance(label, dict):
                # Guard against non-dict items (unexpected data shape).
                lines.append(f"- {label}")
                continue
            name = label.get("name", "Unnamed")
            archived_tag = " *(archived)*" if label.get("is_archived", False) else ""
            lines.append(f"### {name} (#{label.get('id', '?')}){archived_tag}")
            lines.extend(_label_detail_lines(label))
            lines.append("")

    return "\n".join(lines)


def _label_detail_lines(label: dict) -> list[str]:
    """Bullet lines for one label dict — shared by both labels views."""
    name = label.get("name", "Unnamed")
    color = label.get("color", "")
    desc = label.get("description") or "(no description)"
    exclusive = label.get("exclusive", False)

    scope_info = ""
    if "/" in name:
        scope = name.rsplit("/", 1)[0]
        scope_info = f" (scope: `{scope}`)"

    return [
        f"- **Color**: `#{color}`",
        f"- **Description**: {desc}",
        f"- **Exclusive**: {'Yes' if exclusive else 'No'}{scope_info}",
    ]


def _format_label_detail(label: dict, *, extra: dict | None = None) -> str:
    """Detail view for a single Label dict (``issue_get_label``, #760).

    No collection heading or validation-format section — those guide label
    *selection*; a detail read already holds one label.  The repo scope is
    shown when ``extra`` carries it (``issue_get_label`` passes ``owner``/
    ``repo`` from the call args); graceful otherwise.
    """
    owner = (extra or {}).get("owner")
    repo = (extra or {}).get("repo")
    scope = f" for {owner}/{repo}" if owner and repo else ""
    name = label.get("name", "Unnamed")
    archived_tag = " *(archived)*" if label.get("is_archived", False) else ""
    lines = [f"# Label: {name} (#{label.get('id', '?')}){archived_tag}{scope}", ""]
    lines.extend(_label_detail_lines(label))
    return "\n".join(lines)
