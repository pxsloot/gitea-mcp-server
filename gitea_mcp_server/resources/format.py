"""Markdown formatters for MCP resources.

Domain-specific resource formatters. General-purpose
schema-aware formatting lives in gitea_mcp_server/format.py.
"""

from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import ResourceError

from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND
from gitea_mcp_server.format import _format_as_markdown, _format_datetime
from gitea_mcp_server.openapi_types import OpenAPISpec

# Type alias for resource return values
ResourceResult = str

# Shared field filters — curated property subsets for each resource type.
# These ensure resources display the same fields (in the same order)
# that the equivalent tool output would, for consistency.
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


def _format_repo_markdown(repo: dict[str, Any]) -> ResourceResult:
    """Format repository data as Markdown."""
    return _format_as_markdown(
        repo,
        title=repo.get("full_name", "Repository"),
        field_filter=_REPO_FIELDS,
    )


def _format_issues_markdown(
    issues: list[dict[str, Any]], title: str = "Issues", total: int | None = None
) -> ResourceResult:
    """Format issues list as Markdown."""
    if not issues:
        return _format_as_markdown(issues, title=title, field_filter=_ISSUE_FIELDS)

    display_title = title
    if total is not None:
        display_title = f"{title} — {len(issues)} of {total} total"
    else:
        display_title = f"{title} — {len(issues)} issues"

    return _format_as_markdown(
        issues,
        title=display_title,
        field_filter=_ISSUE_FIELDS,
        item_title_key="title",
    )


def _format_pulls_markdown(
    pulls: list[dict[str, Any]], title: str = "Pull Requests"
) -> ResourceResult:
    """Format pull requests list as Markdown."""
    if not pulls:
        return _format_as_markdown(pulls, title=title, field_filter=_PULL_FIELDS)

    display_title = f"{title} — {len(pulls)} pull requests"
    return _format_as_markdown(
        pulls,
        title=display_title,
        field_filter=_PULL_FIELDS,
        item_title_key="title",
    )


def _format_user_markdown(user: dict[str, Any]) -> ResourceResult:
    """Format user profile as Markdown."""
    # Normalize: API may return 'created_at' or 'created' for the same field
    data = dict(user)
    if "created_at" not in data and "created" in data:
        data["created_at"] = data["created"]
    return _format_as_markdown(
        data,
        title=data.get("login", "User"),
        field_filter=_USER_FIELDS,
    )


def _format_release_markdown(release: dict[str, Any]) -> ResourceResult:
    """Format a single release as Markdown."""
    return _format_as_markdown(
        release,
        title=release.get("tag_name", "Release"),
        field_filter=_RELEASE_FIELDS,
    )


def _format_labels_markdown(labels: list[dict[str, Any]], owner: str, repo: str) -> ResourceResult:
    """Format labels list as Markdown with format and validation hints.

    Args:
        labels: List of label objects from the Gitea API.
        owner: Repository owner (for display).
        repo: Repository name (for display).

    Returns:
        Markdown string with label details and usage hints.
    """
    lines = [
        f"# Labels for {owner}/{repo}",
        "",
        f"**Total**: {len(labels)} labels",
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

    if not labels:
        lines.append("*No labels configured for this repository.*")
        lines.append("")
    else:
        lines.append(f"## Labels ({len(labels)})")
        lines.append("")
        for label in labels:
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


def _handle_not_found(
    e: Exception, resource_type: str, resource_id: str, custom_message: str | None = None
) -> None:
    """Convert a 404 exception to ResourceError."""
    if getattr(e, "status_code", None) == HTTP_STATUS_NOT_FOUND:
        message = custom_message or f"Resource not found: {resource_id}"
        raise ResourceError(
            {
                "code": "NOT_FOUND",
                "message": message,
                "detail": str(e),
                "resource_type": resource_type,
                "resource_id": resource_id,
            }
        ) from e


def _build_server_info_markdown(openapi_spec: OpenAPISpec) -> ResourceResult:
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
    "ResourceResult",
    "_build_server_info_markdown",
    "_format_datetime",
    "_format_issues_markdown",
    "_format_labels_markdown",
    "_format_pulls_markdown",
    "_format_release_markdown",
    "_format_repo_markdown",
    "_format_user_markdown",
    "_handle_not_found",
]
