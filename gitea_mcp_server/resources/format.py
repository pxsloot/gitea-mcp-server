"""Markdown formatters for MCP resources."""

from datetime import datetime
from typing import Any

from fastmcp.exceptions import ResourceError

from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND

# Type alias for resource return values
ResourceResult = str


def _format_datetime(dt: str | None) -> str:
    """Format datetime string to human-readable format."""
    if not dt:
        return "N/A"
    try:
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return dt


def _format_repo_markdown(repo: dict[str, Any]) -> ResourceResult:
    """Format repository data as Markdown."""
    lines = [
        f"# {repo['full_name']}",
        "",
        f"**Description**: {repo.get('description', 'No description')}",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| Owner | {repo['owner']['login']} |",
        f"| URL | {repo['html_url']} |",
        f"| Default Branch | {repo.get('default_branch', 'N/A')} |",
        f"| Stars | {repo.get('stargazers_count', 0)} |",
        f"| Forks | {repo.get('forks_count', 0)} |",
        f"| Open Issues | {repo.get('open_issues_count', 0)} |",
        f"| Size | {repo.get('size', 0)} KB |",
        f"| Created | {_format_datetime(repo.get('created_at'))} |",
        f"| Updated | {_format_datetime(repo.get('updated_at'))} |",
        "",
        f"**Topics**: {', '.join(repo.get('topics', [])) if repo.get('topics') else 'None'}",
        "",
        f"**License**: {repo.get('license', {}).get('name', 'None') if repo.get('license') else 'None'}",
    ]
    return "\n".join(lines)


def _format_issues_markdown(
    issues: list[dict[str, Any]], title: str = "Issues", total: int | None = None
) -> ResourceResult:
    """Format issues list as Markdown."""
    lines = [f"# {title}", ""]

    if total is not None:
        lines.append(f"Showing {len(issues)} of {total} total issues")
    else:
        lines.append(f"Showing {len(issues)} issues")
    lines.append("")

    if not issues:
        lines.append("No issues found.")
        return "\n".join(lines)

    for issue in issues:
        number = issue["number"]
        title_text = issue["title"]
        state = issue["state"]
        user = issue["user"]["login"]
        created = _format_datetime(issue.get("created_at"))
        comments = issue.get("comments", 0)
        labels = ", ".join([label["name"] for label in issue.get("labels", [])])

        lines.append(f"## #{number}: {title_text}")
        lines.append(f"- **State**: {state}")
        lines.append(f"- **Author**: @{user}")
        lines.append(f"- **Created**: {created}")
        lines.append(f"- **Comments**: {comments}")
        if labels:
            lines.append(f"- **Labels**: {labels}")
        lines.append(f"- **URL**: {issue['html_url']}")
        lines.append("")

    return "\n".join(lines)


def _format_pulls_markdown(
    pulls: list[dict[str, Any]], title: str = "Pull Requests"
) -> ResourceResult:
    """Format pull requests list as Markdown."""
    lines = [f"# {title}", ""]
    lines.append(f"Showing {len(pulls)} pull requests")
    lines.append("")

    if not pulls:
        lines.append("No pull requests found.")
        return "\n".join(lines)

    for pr in pulls:
        number = pr["number"]
        title_text = pr["title"]
        state = pr["state"]
        user = pr["user"]["login"]
        created = _format_datetime(pr.get("created_at"))
        base = pr["base"]["label"]
        head = pr["head"]["label"]
        comments = pr.get("comments", 0)

        lines.append(f"## PR #{number}: {title_text}")
        lines.append(f"- **State**: {state}")
        lines.append(f"- **Author**: @{user}")
        lines.append(f"- **Created**: {created}")
        lines.append(f"- **Base**: {base}")
        lines.append(f"- **Head**: {head}")
        lines.append(f"- **Comments**: {comments}")
        lines.append(f"- **URL**: {pr['html_url']}")
        lines.append("")

    return "\n".join(lines)


def _format_user_markdown(user: dict[str, Any]) -> ResourceResult:
    """Format user profile as Markdown."""
    lines = [
        f"# {user['login']}",
        "",
        f"**Name**: {user.get('full_name', 'Not set')}",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| Type | {'Organization' if user.get('type') == 'Organization' else 'User'} |",
        f"| URL | {user['html_url']} |",
        f"| Public Repos | {user.get('public_repos', 0)} |",
        f"| Followers | {user.get('followers_count', 0)} |",
        f"| Following | {user.get('following_count', 0)} |",
        f"| Created | {_format_datetime(user.get('created_at') or user.get('created'))} |",
        "",
        f"**Bio**: {user.get('bio', 'No bio') if user.get('bio') else 'No bio'}",
        "",
        f"**Location**: {user.get('location', 'Not set') if user.get('location') else 'Not set'}",
        f"**Website**: {user.get('website', 'Not set') if user.get('website') else 'Not set'}",
    ]
    return "\n".join(lines)


def _format_release_markdown(release: dict[str, Any]) -> ResourceResult:
    """Format a single release as Markdown."""
    lines = [
        f"# {release['tag_name']}",
        "",
        f"**Title**: {release.get('name', release['tag_name'])}",
        f"**Draft**: {'Yes' if release.get('draft') else 'No'}",
        f"**Prerelease**: {'Yes' if release.get('prerelease') else 'No'}",
        f"**Created**: {_format_datetime(release.get('created_at'))}",
        f"**Published**: {_format_datetime(release.get('published_at'))}",
        "",
        "## Description",
        "",
        release.get("body", "No description provided."),
    ]
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
