"""Markdown formatters for MCP resources and tool results."""

import json as json_module
from datetime import datetime
from typing import Any

from fastmcp.exceptions import ResourceError

from gitea_mcp_server.constants import HTTP_STATUS_NOT_FOUND

# Type alias for resource return values
ResourceResult = str


def _snake_to_title(name: str) -> str:
    result = ""
    for i, ch in enumerate(name):
        if ch == "_":
            result += " "
        elif ch.isupper() and i > 0 and name[i - 1].islower():
            result += " " + ch
        elif ch.isupper() and i > 0 and name[i - 1] == " ":
            result += ch.lower()
        else:
            result += ch
    return result.strip().title()


def _format_scalar(value: Any, schema: dict[str, Any] | None = None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return str(value)
    fmt = schema.get("format") if schema else None
    if fmt == "date-time":
        return _format_datetime(value)
    return value


def _format_simple_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, list):
        parts = [_format_simple_value(v) for v in value]
        return ", ".join(parts)
    if isinstance(value, dict):
        return json_module.dumps(value, indent=2)
    return str(value)


def _format_list_as_markdown(
    data: list[Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
) -> str:
    lines: list[str] = []
    item_schema = schema.get("items") if isinstance(schema, dict) else None
    if not data:
        lines.append(f"{indent}*None*")
    elif data and isinstance(data[0], dict):
        for i, item in enumerate(data):
            sub = _format_as_markdown(
                item, item_schema, title=f"Item {i + 1}", _depth=0
            )
            lines.append(sub)
    elif item_schema and item_schema.get("type") in ("string", "number", "integer", "boolean"):
        items = [_format_scalar(v, item_schema) for v in data]
        lines.append(f"{indent}{', '.join(items)}")
    else:
        for item in data:
            sub = _format_simple_value(item)
            lines.append(f"{indent}- {sub}")
    return "\n".join(lines)


def _merge_allof_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not schema or "allOf" not in schema:
        return schema
    merged: dict[str, Any] = {"properties": {}}
    for sub_schema in schema["allOf"]:
        if isinstance(sub_schema, dict):
            sub_props = sub_schema.get("properties", {})
            if isinstance(sub_props, dict):
                merged["properties"].update(sub_props)
    return merged


def _resolve_anyof_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not schema:
        return None
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for sub in variants:
                if isinstance(sub, dict) and sub.get("type") == "object" and sub.get("properties"):
                    return sub
    return schema


def _render_flat_table(
    lines: list[str], flat: list[tuple[str, str]], indent: str
) -> None:
    lines.append(f"{indent}| Property | Value |")
    lines.append(f"{indent}|----------|-------|")
    for label, val in flat:
        escaped = val.replace("|", "\\|")
        lines.append(f"{indent}| {label} | {escaped} |")
    lines.append("")


def _render_nested_sections(
    lines: list[str], nested: list[tuple[str, str]], indent: str, _depth: int
) -> None:
    for label, sub in nested:
        if _depth == 0:
            lines.append(f"## {label}")
        else:
            lines.append(f"{indent}**{label}:**")
        lines.append("")
        lines.append(sub)
        lines.append("")


def _format_dict_as_markdown(
    data: dict[str, Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    _depth: int = 0,
) -> str:
    lines: list[str] = []
    combined_schema = _merge_allof_schema(schema)
    properties = (
        combined_schema.get("properties", {})
        if combined_schema and isinstance(combined_schema, dict)
        else {}
    )

    if not data:
        lines.append(f"{indent}*Empty*")
    elif properties:
        flat: list[tuple[str, str]] = []
        nested: list[tuple[str, str]] = []

        for key, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                continue
            effective = _resolve_anyof_schema(prop_schema)
            label = _snake_to_title(key)
            raw_val = data.get(key)
            is_nested = isinstance(raw_val, (dict, list))
            if is_nested:
                sub = _format_as_markdown(raw_val, effective or prop_schema, _depth=_depth + 1)
                if sub.strip():
                    nested.append((label, sub))
            else:
                formatted = _format_scalar(raw_val, prop_schema)
                flat.append((label, formatted))

        _render_flat_table(lines, flat, indent)
        _render_nested_sections(lines, nested, indent, _depth)
    else:
        _render_flat_table(lines, [(key, _format_simple_value(val)) for key, val in data.items()], indent)

    return "\n".join(lines)


def _format_as_markdown(
    data: Any,
    schema: dict[str, Any] | None = None,
    title: str | None = None,
    _depth: int = 0,
) -> str:
    lines: list[str] = []
    indent = "  " * _depth

    if title and _depth == 0:
        lines.append(f"# {title}")
        lines.append("")

    if data is None:
        lines.append(f"{indent}N/A")
        return "\n".join(lines)

    if isinstance(data, list):
        return _format_list_as_markdown(data, schema, indent)

    if isinstance(data, dict):
        return _format_dict_as_markdown(data, schema, indent, _depth)

    lines.append(f"{indent}{_format_scalar(data, schema)}")
    return "\n".join(lines)


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
    "_format_as_markdown",
    "_format_datetime",
    "_format_issues_markdown",
    "_format_pulls_markdown",
    "_format_release_markdown",
    "_format_repo_markdown",
    "_format_simple_value",
    "_format_user_markdown",
    "_handle_not_found",
    "_resolve_anyof_schema",
    "_snake_to_title",
]
