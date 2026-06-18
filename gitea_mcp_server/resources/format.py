"""Markdown formatters for MCP resources.

Domain-specific resource formatters. General-purpose
schema-aware formatting lives in gitea_mcp_server/format.py.
"""

from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import ResourceError

from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND
from gitea_mcp_server.format import _format_as_markdown, _format_datetime

# Type alias for resource return values
ResourceResult = str

# Shared field filters — curated property subsets for each resource type.
# These ensure resources display the same fields (in the same order)
# that the equivalent tool output would, for consistency.
_ISSUE_FIELDS: Sequence[str] = [
    "number", "title", "state", "user", "created_at",
    "comments", "labels", "html_url",
]
_PULL_FIELDS: Sequence[str] = [
    "number", "title", "state", "user", "created_at",
    "base", "head", "comments", "html_url",
]
_REPO_FIELDS: Sequence[str] = [
    "name", "full_name", "description", "owner", "html_url",
    "default_branch", "stargazers_count", "forks_count",
    "open_issues_count", "size", "created_at", "updated_at",
    "topics", "license",
]
_USER_FIELDS: Sequence[str] = [
    "login", "full_name", "type", "html_url",
    "public_repos", "followers_count", "following_count",
    "created_at", "bio", "location", "website",
]
_RELEASE_FIELDS: Sequence[str] = [
    "tag_name", "name", "draft", "prerelease",
    "created_at", "published_at", "body",
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
        return _format_as_markdown(issues, title=title, field_filter=_ISSUE_FIELDS, item_title_key="title")

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
        return _format_as_markdown(pulls, title=title, field_filter=_PULL_FIELDS, item_title_key="title")

    display_title = f"{title} — {len(pulls)} pull requests"
    return _format_as_markdown(
        pulls,
        title=display_title,
        field_filter=_PULL_FIELDS,
        item_title_key="title",
    )


def _format_user_markdown(user: dict[str, Any]) -> ResourceResult:
    """Format user profile as Markdown."""
    return _format_as_markdown(
        user,
        title=user.get("login", "User"),
        field_filter=_USER_FIELDS,
    )


def _format_release_markdown(release: dict[str, Any]) -> ResourceResult:
    """Format a single release as Markdown."""
    return _format_as_markdown(
        release,
        title=release.get("tag_name", "Release"),
        field_filter=_RELEASE_FIELDS,
    )


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


def _build_server_info_markdown(openapi_spec: dict[str, Any]) -> ResourceResult:
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
    "_format_pulls_markdown",
    "_format_release_markdown",
    "_format_repo_markdown",
    "_format_user_markdown",
    "_handle_not_found",
]
