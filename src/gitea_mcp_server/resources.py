"""MCP Resources for Gitea data exposure.

Resources provide read-only access to Gitea entities via URI templates.
They complement tools by offering efficient, on-demand data retrieval.

This module supports:
- Auto-generated resources from OpenAPI GET endpoints (raw JSON)
- Manual resource overrides with custom formatting (Markdown, etc.)
- Custom resources not in the OpenAPI spec

Architecture:
1. register_auto_generated_resources(): Creates resources for all GET endpoints in OpenAPI spec
   - Returns raw JSON
   - Skips URIs that will be covered by custom resources
   - These provide comprehensive API coverage

2. register_custom_resources(): Registers manually implemented resources
   - Return formatted content (Markdown, plain text)
   - Automatically override auto-generated resources with matching URIs
   - Provide optimized, user-friendly output for common use cases

Usage:
    from gitea_mcp_server import resources

    # In your server setup:
    resources.register_auto_generated_resources(mcp, gitea_client, openapi_spec)
    resources.register_custom_resources(mcp, gitea_client)

    # Custom resources override auto-generated ones with the same URI.
"""

import base64
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)

# Constants
HTTP_NOT_FOUND = 404

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
        f"| Created | {_format_datetime(user.get('created_at'))} |",
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


# ============================================================================
# AUTO-GENERATED RESOURCES
# ============================================================================


def register_auto_generated_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: dict[str, Any],
    skip_uris: set[str] | None = None,
) -> None:
    """Auto-generate resources from GET endpoints in OpenAPI spec.

    Creates resources for all GET operations, returning raw JSON.
    These can be overridden by custom resources with the same URI.
    Skip URIs that are already covered by custom resources to avoid duplicates.

    This function implements the "comprehensive coverage" layer:
    - Every GET endpoint becomes a resource
    - URI pattern: gitea://<openapi-path> (e.g., /repos/{owner}/{repo} → gitea://repos/{owner}/{repo})
    - Returns raw JSON for maximum flexibility
    - Only endpoints with path parameters are registered (FastMCP requirement)

    Args:
        mcp: FastMCP server instance
        gitea_client: GiteaClient for API calls
        openapi_spec: OpenAPI 3.1 specification dictionary
        skip_uris: Set of URI templates to skip. If None, uses default custom URIs.
                   These URIs will be provided by custom resources with better formatting.
    """
    if skip_uris is None:
        # Default set: URIs that will be provided by custom resources
        skip_uris = {
            "gitea://repos/{owner}/{repo}",
            "gitea://repos/{owner}/{repo}/readme",
            "gitea://repos/{owner}/{repo}/issues",
            "gitea://repos/{owner}/{repo}/issues/open",
            "gitea://repos/{owner}/{repo}/issues/closed",
            "gitea://repos/{owner}/{repo}/pulls",
            "gitea://repos/{owner}/{repo}/pulls/open",
            "gitea://repos/{owner}/{repo}/files/{path}",
            "gitea://repos/{owner}/{repo}/releases",
            "gitea://users/{username}",
            "gitea://orgs/{orgname}",
        }

    def make_resource_func(path: str, method: str, operation: dict[str, Any]) -> Callable:
        """Create a resource function for a given OpenAPI operation."""
        # Extract parameters from path
        path_params = []
        if "parameters" in operation:
            for param in operation["parameters"]:
                if param["in"] == "path":
                    path_params.append(param["name"])

        query_params = []
        if "parameters" in operation:
            for param in operation["parameters"]:
                if param["in"] == "query":
                    query_params.append(param["name"])

        async def resource_func(**kwargs: Any) -> ResourceResult:
            """Auto-generated resource from OpenAPI spec."""
            # Build path with kwargs
            formatted_path = path
            for param in path_params:
                if param not in kwargs:
                    return f"Error: Missing required path parameter '{param}'"
                formatted_path = formatted_path.replace(f"{{{param}}}", str(kwargs[param]))

            # Build query params
            query = {}
            for param in query_params:
                if param in kwargs:
                    query[param] = kwargs[param]

            try:
                response = await gitea_client.request(
                    method, formatted_path, params=query if query else None
                )
                # Return JSON for auto-generated resources
                return json.dumps(response, indent=2)
            except Exception as e:  # noqa: BLE001
                return f"Error fetching resource: {type(e).__name__}: {e}"

        # Set docstring from operation
        summary = operation.get("summary", "")
        description = operation.get("description", "")
        docstring = summary
        if description:
            docstring += "\n\n" + description
        if not docstring:
            docstring = f"Resource for {method.upper()} {path}"
        resource_func.__doc__ = docstring

        return resource_func

    # Iterate over all GET operations
    paths = openapi_spec.get("paths", {})
    count = 0
    for path, path_item in paths.items():
        for method in ["get", "GET"]:
            if method in path_item:
                operation = path_item[method]

                # Skip if the path template has no parameters (FastMCP requires at least one)
                # FastMCP checks for {var} or {var*} patterns in the URI template
                if "{" not in path:
                    logger.debug(
                        "Skipping auto-generated resource for %s: no path parameters in template",
                        path,
                    )
                    continue

                # Convert OpenAPI path to resource URI
                # e.g., /repos/{owner}/{repo} -> gitea://repos/{owner}/{repo}
                uri_template = "gitea://" + path.lstrip("/")

                # Skip if this URI will be covered by a custom resource
                if uri_template in skip_uris:
                    logger.debug(
                        "Skipping auto-generated resource %s: will be provided by custom resource",
                        uri_template,
                    )
                    continue

                # Create the resource function
                resource_func = make_resource_func(path, method.upper(), operation)

                # Register with FastMCP
                try:
                    mcp.resource(
                        uri_template, mime_type="application/json", tags={"api", "raw", "auto"}
                    )(resource_func)
                    count += 1
                    logger.debug("Registered auto-generated resource: %s", uri_template)
                except ValueError as e:
                    logger.warning(
                        "Skipping auto-generated resource %s: %s",
                        uri_template,
                        e,
                    )
                    continue

    logger.info("Auto-generated %d resources from OpenAPI spec", count)


# ============================================================================
# MANUAL RESOURCES
# ============================================================================

# These are custom-formatted resources that override auto-generated ones


async def get_repository(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get full repository metadata with nice Markdown formatting."""
    try:
        data = await gitea_client.request("GET", f"/repos/{owner}/{repo}")
        if isinstance(data, str):
            # If we got text instead of JSON, return as-is
            return data
        return _format_repo_markdown(data)
    except Exception as e:
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"# {owner}/{repo}\n\nRepository not found."
        raise


async def get_readme(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get repository README content."""
    try:
        response = await gitea_client.request("GET", f"/repos/{owner}/{repo}/readme")
        if isinstance(response, str):
            return response
        # If response is dict (from JSON), it might have content/base64
        if isinstance(response, dict):
            content_bytes = base64.b64decode(response["content"])
            return content_bytes.decode("utf-8")
        return str(response)
    except Exception as e:
        # Check for 404 on the exception (GiteaAPIError has status_code)
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"# {owner}/{repo}\n\nNo README found."
        raise


async def list_repo_issues(
    owner: str, repo: str, gitea_client: GiteaClient, state: str | None = None
) -> ResourceResult:
    """List issues for a repository, optionally filtered by state (open/closed)."""
    params = {}
    if state:
        if state not in ("open", "closed"):
            return f"Error: Invalid state '{state}'. Must be 'open' or 'closed'."
        params["state"] = state

    try:
        issues = await gitea_client.request("GET", f"/repos/{owner}/{repo}/issues", params=params)
        if isinstance(issues, str):
            return issues
    except Exception as e:
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"# {owner}/{repo}\n\nNo issues found (repository may not exist)."
        raise

    title = f"Issues ({state})" if state else "All Issues"
    return _format_issues_markdown(issues, title=title)


async def list_repo_pulls(
    owner: str, repo: str, gitea_client: GiteaClient, state: str | None = None
) -> ResourceResult:
    """List pull requests for a repository, optionally filtered by state."""
    params = {}
    if state:
        if state not in ("open", "closed"):
            return f"Error: Invalid state '{state}'. Must be 'open' or 'closed'."
        params["state"] = state

    try:
        pulls = await gitea_client.request("GET", f"/repos/{owner}/{repo}/pulls", params=params)
        if isinstance(pulls, str):
            return pulls
    except Exception as e:
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"# {owner}/{repo}\n\nNo pull requests found (repository may not exist)."
        raise

    title = f"Pull Requests ({state})" if state else "All Pull Requests"
    return _format_pulls_markdown(pulls, title=title)


async def get_file(
    owner: str, repo: str, path: str, gitea_client: GiteaClient, ref: str | None = None
) -> ResourceResult:
    """Get file content from repository."""
    params = {}
    if ref:
        params["ref"] = ref

    try:
        response = await gitea_client.request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params=params
        )

        if isinstance(response, str):
            return response

        if not isinstance(response, dict):
            return str(response)

        if response.get("encoding") == "base64":
            content = base64.b64decode(response["content"]).decode("utf-8")
        else:
            content = response.get("content", "")

        return content
    except Exception as e:
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"Error: File '{path}' not found."
        raise


async def list_repo_releases(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """List releases for a repository."""
    try:
        releases = await gitea_client.request("GET", f"/repos/{owner}/{repo}/releases")
        if isinstance(releases, str):
            return releases
    except Exception as e:
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"# Releases for {owner}/{repo}\n\nNo releases found (repository may not exist)."
        raise

    if not releases:
        return f"# Releases for {owner}/{repo}\n\nNo releases found."

    lines = [f"# Releases for {owner}/{repo}", "", f"Showing {len(releases)} releases", ""]

    for release in releases:
        lines.append(_format_release_markdown(release))
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


async def get_user(username: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get user profile information."""
    try:
        user = await gitea_client.request("GET", f"/users/{username}")
        if isinstance(user, str):
            return user
        return _format_user_markdown(user)
    except Exception as e:
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"# {username}\n\nUser not found."
        raise


async def get_org(orgname: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get organization profile information."""
    try:
        org = await gitea_client.request("GET", f"/orgs/{orgname}")
        if isinstance(org, str):
            return org
        return _format_user_markdown(org)
    except Exception as e:
        if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
            return f"# {orgname}\n\nOrganization not found."
        raise


# Wrapper functions for state-filtered endpoints
async def list_repo_issues_open(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """Open issues only."""
    return await list_repo_issues(owner=owner, repo=repo, state="open", gitea_client=gitea_client)


async def list_repo_issues_closed(
    owner: str, repo: str, gitea_client: GiteaClient
) -> ResourceResult:
    """Closed issues only."""
    return await list_repo_issues(owner=owner, repo=repo, state="closed", gitea_client=gitea_client)


async def list_repo_pulls_open(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """Open pull requests only."""
    return await list_repo_pulls(owner=owner, repo=repo, state="open", gitea_client=gitea_client)


def register_custom_resources(mcp: FastMCP, gitea_client: GiteaClient) -> None:
    """Register custom-formatted and custom resources.

    These override any auto-generated resources with the same URI.

    This function implements the "optimized UX" layer:
    - Manually implemented resources with user-friendly formatting (Markdown)
    - Convenience wrappers that combine data or filter by common criteria
    - Strategically chosen to cover the most frequently accessed endpoints
    - Registration order ensures these override auto-generated ones

    Override mechanism:
    - FastMCP registers resources in order; later registrations replace earlier ones
    - Custom resources are registered AFTER auto-generated ones
    - URIs match exactly, so gitea://repos/{owner}/{repo} custom replaces auto-generated

    Tags semantic:
    - "wrapper": Human-readable formatted output (Markdown/plain text)
    - "repository", "issue", "pull_request", etc.: Entity type for filtering
    - Cache TTLs are tuned per resource type (static data cached longer)
    """

    def make_resource(
        func: Callable[..., Awaitable[str]],
    ) -> Callable[..., Awaitable[str]]:
        """Wrap a resource function to inject gitea_client."""

        async def wrapper(**kwargs: Any) -> str:
            return await func(gitea_client=gitea_client, **kwargs)

        return wrapper

    # Custom-formatted resources with better UX
    custom_resources: list[
        tuple[str, Callable[..., Awaitable[str]], str, set[str], dict[str, Any] | None]
    ] = [
        (
            "gitea://repos/{owner}/{repo}",
            get_repository,
            "text/markdown",
            {"wrapper", "repository"},
            {"cache_ttl": 300.0},  # 5 minutes - repo info rarely changes
        ),
        (
            "gitea://repos/{owner}/{repo}/readme",
            get_readme,
            "text/plain",
            {"wrapper", "readme"},
            {"cache_ttl": 600.0},
        ),  # 10 minutes
        (
            "gitea://repos/{owner}/{repo}/issues",
            list_repo_issues,
            "text/markdown",
            {"wrapper", "issues"},
            None,  # Use default TTL (30s) - issues change frequently
        ),
        (
            "gitea://repos/{owner}/{repo}/issues/open",
            list_repo_issues_open,
            "text/markdown",
            {"wrapper", "issues"},
            None,
        ),
        (
            "gitea://repos/{owner}/{repo}/issues/closed",
            list_repo_issues_closed,
            "text/markdown",
            {"wrapper", "issues"},
            None,
        ),
        (
            "gitea://repos/{owner}/{repo}/pulls",
            list_repo_pulls,
            "text/markdown",
            {"wrapper", "pull_requests"},
            None,  # Use default TTL (30s) - PRs change frequently
        ),
        (
            "gitea://repos/{owner}/{repo}/pulls/open",
            list_repo_pulls_open,
            "text/markdown",
            {"wrapper", "pull_requests"},
            None,
        ),
        (
            "gitea://repos/{owner}/{repo}/files/{path}",
            get_file,
            "text/plain",
            {"wrapper", "files"},
            None,
        ),  # Default TTL
        (
            "gitea://repos/{owner}/{repo}/releases",
            list_repo_releases,
            "text/markdown",
            {"wrapper", "releases"},
            {"cache_ttl": 600.0},  # 10 minutes - releases are infrequent
        ),
        (
            "gitea://users/{username}",
            get_user,
            "text/markdown",
            {"wrapper", "user"},
            {"cache_ttl": 300.0},
        ),  # 5 minutes
        (
            "gitea://orgs/{orgname}",
            get_org,
            "text/markdown",
            {"wrapper", "organization"},
            {"cache_ttl": 300.0},
        ),  # 5 minutes
    ]

    for uri_template, func, mime_type, tags, meta in custom_resources:
        kwargs: dict[str, Any] = {"mime_type": mime_type, "tags": tags}
        if meta is not None:
            kwargs["meta"] = meta
        mcp.resource(uri_template, **kwargs)(make_resource(func))
