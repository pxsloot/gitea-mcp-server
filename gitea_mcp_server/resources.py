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
    from gitea_mcp_server.resource_registry import ResourceRegistry

    # In your server setup:
    registry = ResourceRegistry()
    resources.register_auto_generated_resources(mcp, gitea_client, openapi_spec, registry)
    resources.register_custom_resources(mcp, gitea_client, registry)

    # Custom resources override auto-generated ones with the same URI.
    # Access registry for documentation/querying: registry.list_resources(), etc.
"""

import base64
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    AUTO_GENERATED_RESOURCE_SKIP_URIS,
    CACHE_TTL_README,
    CACHE_TTL_RELEASES,
    CACHE_TTL_REPOSITORY,
    CACHE_TTL_USERS,
    HTTP_STATUS_NOT_FOUND,
)
from gitea_mcp_server.server_setup.tool_annotator import derive_required_scope

logger = logging.getLogger(__name__)

# Type alias for resource return values
ResourceResult = str


def _derive_resource_name(operation: dict[str, Any], path: str) -> str:
    """Derive a meaningful resource name from an OpenAPI operation.

    Uses the operationId if available (converting camelCase to snake_case).
    Falls back to deriving from the URI path.

    Args:
        operation: OpenAPI operation dict
        path: OpenAPI path string (e.g., "/repos/{owner}/{repo}/issues/{index}")

    Returns:
        A meaningful snake_case name string
    """
    operation_id = operation.get("operationId")
    if operation_id and operation_id.strip():
        name = operation_id.strip()
        # Convert camelCase to snake_case
        # Handles: "getRepo" -> "get_repo", "issueGetIssue" -> "issue_get_issue"
        # Also handles leading uppercase: "RepoGet" -> "repo_get"
        result = ""
        for i, char in enumerate(name):
            if char.isupper():
                if i > 0 and (name[i - 1].islower() or (i + 1 < len(name) and name[i + 1].islower())):
                    result += "_"
                result += char.lower()
            else:
                result += char
        return result

    # Fallback: derive from path, stripping params
    clean_path = path.strip("/")
    segments = [s for s in clean_path.split("/") if not (s.startswith("{") and s.endswith("}"))]
    if not segments:
        segments = [s.strip("{}") for s in clean_path.split("/") if s]
    return "_".join(segments) if segments else "resource"


def _handle_not_found(
    e: Exception, resource_type: str, resource_id: str, custom_message: str | None = None
) -> None:
    """Convert a 404 exception to ResourceError.

    Helper to reduce boilerplate for repeated 404 error handling.

    Args:
        e: The caught exception
        resource_type: Type of resource (e.g., "repository", "issues")
        resource_id: Identifier for the resource
        custom_message: Optional custom message (defaults to standard message)
    """
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


def _format_datetime(dt: str | None) -> str:
    """Format datetime string to human-readable format."""
    if not dt:
        return "N/A"
    try:
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return dt


def _build_markdown(lines: list[str]) -> ResourceResult:
    """Join lines into markdown content.

    Centralized helper for building markdown from line lists to ensure
    consistent formatting and easy future modifications.

    Args:
        lines: List of markdown lines (with appropriate newlines already handled)

    Returns:
        Joined markdown content
    """
    return "\n".join(lines)


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
    return _build_markdown(lines)


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
        return _build_markdown(lines)

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
        return _build_markdown(lines)

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
    return _build_markdown(lines)


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
    return _build_markdown(lines)


# ============================================================================
# AUTO-GENERATED RESOURCES
# ============================================================================


def _make_resource_func(
    path: str, method: str, operation: dict[str, Any], gitea_client: GiteaClient,
    resource_name: str | None = None,
) -> Callable:
    """Create a resource function for a given OpenAPI operation."""
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
        formatted_path = path
        missing_params = [p for p in path_params if p not in kwargs]
        if missing_params:
            raise ResourceError(
                {
                    "code": "VALIDATION_ERROR",
                    "message": f"Missing required path parameter(s): {', '.join(missing_params)}",
                    "detail": "The resource requires path parameters that were not provided.",
                    "resource_type": "api",
                    "resource_id": formatted_path,
                }
            )
        for param in path_params:
            formatted_path = formatted_path.replace(f"{{{param}}}", str(kwargs[param]))

        query = {p: kwargs[p] for p in query_params if p in kwargs}

        try:
            response = await gitea_client.request(
                method, formatted_path, params=query if query else None
            )
            return json.dumps(response, indent=2)
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status == HTTP_STATUS_NOT_FOUND:
                raise ResourceError(
                    {
                        "code": "NOT_FOUND",
                        "message": f"Resource not found: {formatted_path}",
                        "detail": str(e),
                        "resource_type": "api",
                        "resource_id": formatted_path,
                    }
                ) from e
            if status:
                raise ResourceError(
                    {
                        "code": "API_ERROR",
                        "message": f"API error {status} for {formatted_path}",
                        "detail": str(e),
                        "resource_type": "api",
                        "resource_id": formatted_path,
                    }
                ) from e
            raise ResourceError(
                {
                    "code": "INTERNAL_ERROR",
                    "message": f"Unexpected error fetching resource: {formatted_path}",
                    "detail": str(e),
                    "resource_type": "api",
                    "resource_id": formatted_path,
                }
            ) from e

    summary = operation.get("summary", "")
    description = operation.get("description", "")
    docstring = summary
    if description:
        docstring += "\n\n" + description
    if not docstring:
        docstring = f"Resource for {method.upper()} {path}"
    resource_func.__doc__ = docstring

    if resource_name:
        resource_func.__name__ = resource_name

    return resource_func


def register_auto_generated_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: dict[str, Any],
    registry: Any,
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
        registry: ResourceRegistry to record registered resources
        skip_uris: Set of URI templates to skip. If None, uses default custom URIs.
                   These URIs will be provided by custom resources with better formatting.
    """
    if skip_uris is None:
        skip_uris = AUTO_GENERATED_RESOURCE_SKIP_URIS

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
                # First path segment becomes the URI scheme, e.g.:
                #   /gitea/repos/{owner}/{repo} -> gitea://repos/{owner}/{repo}
                #   /repos/{owner}/{repo}       -> repos://{owner}/{repo}
                uri_template = re.sub(r"^/([^/]+)/", r"\1://", path)

                # Skip if this URI will be covered by a custom resource
                if uri_template in skip_uris:
                    logger.debug(
                        "Skipping auto-generated resource %s: will be provided by custom resource",
                        uri_template,
                    )
                    continue

                # Derive meaningful name from operation
                resource_name = _derive_resource_name(operation, path)

                # Derive required scope from operation tags
                swagger_tags = set(operation.get("tags", [])) or None
                required_scope = derive_required_scope(swagger_tags, "GET")

                # Create the resource function
                resource_func = _make_resource_func(
                    path, method.upper(), operation, gitea_client,
                    resource_name=resource_name,
                )

                resource_meta: dict[str, Any] = {
                    "fastmcp": {"_internal": {"required_scope": required_scope}}
                }

                # Register with FastMCP
                try:
                    mcp.resource(
                        uri_template,
                        name=resource_name,
                        mime_type="application/json",
                        tags={"api", "raw", "auto"},
                        meta=resource_meta,
                    )(resource_func)
                    # Record in registry catalog
                    registry.record(
                        uri=uri_template,
                        func=resource_func,
                        mime_type="application/json",
                        tags={"api", "raw", "auto"},
                        meta=resource_meta,
                    )
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
            return data
        return _format_repo_markdown(data)
    except Exception as e:
        _handle_not_found(
            e, "repository", f"{owner}/{repo}", f"Repository '{owner}/{repo}' not found."
        )
        raise


async def get_readme(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get repository README content."""
    try:
        response = await gitea_client.request("GET", f"/repos/{owner}/{repo}/contents/README.md")
        if isinstance(response, str):
            return response
        if not isinstance(response, dict):
            return str(response)
        # Handle encoding like get_file does
        if response.get("encoding") == "base64":
            content = base64.b64decode(response.get("content", "")).decode("utf-8")
        else:
            content = response.get("content", "")
        return content  # noqa: TRY300
    except Exception as e:
        _handle_not_found(
            e, "readme", f"{owner}/{repo}", f"README not found for repository '{owner}/{repo}'."
        )
        raise


async def list_repo_issues(
    owner: str, repo: str, gitea_client: GiteaClient, state: str | None = None
) -> ResourceResult:
    """List issues for a repository, optionally filtered by state (open/closed)."""
    params = {}
    if state:
        if state not in ("open", "closed"):
            raise ResourceError(
                {
                    "code": "VALIDATION_ERROR",
                    "message": f"Invalid state parameter: '{state}'. Must be 'open' or 'closed'.",
                    "detail": "The 'state' query parameter must be either 'open' or 'closed'.",
                    "resource_type": "issues",
                    "resource_id": f"{owner}/{repo}",
                }
            )
        params["state"] = state

    try:
        issues = await gitea_client.request("GET", f"/repos/{owner}/{repo}/issues", params=params)
        if isinstance(issues, str):
            return issues
    except Exception as e:
        _handle_not_found(
            e,
            "issues",
            f"{owner}/{repo}",
            f"Repository '{owner}/{repo}' not found or has no issues.",
        )
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
            raise ResourceError(
                {
                    "code": "VALIDATION_ERROR",
                    "message": f"Invalid state parameter: '{state}'. Must be 'open' or 'closed'.",
                    "detail": "The 'state' query parameter must be either 'open' or 'closed'.",
                    "resource_type": "pulls",
                    "resource_id": f"{owner}/{repo}",
                }
            )
        params["state"] = state

    try:
        pulls = await gitea_client.request("GET", f"/repos/{owner}/{repo}/pulls", params=params)
        if isinstance(pulls, str):
            return pulls
    except Exception as e:
        _handle_not_found(
            e,
            "pulls",
            f"{owner}/{repo}",
            f"Repository '{owner}/{repo}' not found or has no pull requests.",
        )
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

        return content  # noqa: TRY300
    except Exception as e:
        _handle_not_found(
            e,
            "file",
            f"{owner}/{repo}/{path}",
            f"File '{path}' not found in repository '{owner}/{repo}'.",
        )
        raise


async def list_repo_releases(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """List releases for a repository."""
    try:
        releases = await gitea_client.request("GET", f"/repos/{owner}/{repo}/releases")
        if isinstance(releases, str):
            return releases
    except Exception as e:
        _handle_not_found(
            e,
            "releases",
            f"{owner}/{repo}",
            f"Repository '{owner}/{repo}' not found or has no releases.",
        )
        raise

    if not releases:
        return f"# Releases for {owner}/{repo}\n\nNo releases found."

    lines = [f"# Releases for {owner}/{repo}", "", f"Showing {len(releases)} releases", ""]

    for release in releases:
        lines.append(_format_release_markdown(release))
        lines.append("---")
        lines.append("")

    return _build_markdown(lines)


async def get_user(username: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get user profile information."""
    try:
        user = await gitea_client.request("GET", f"/users/{username}")
        if isinstance(user, str):
            return user
        return _format_user_markdown(user)
    except Exception as e:
        _handle_not_found(e, "user", username, f"User '{username}' not found.")
        raise


async def get_current_user(gitea_client: GiteaClient) -> ResourceResult:
    """Get current authenticated user profile information."""
    try:
        user = await gitea_client.request("GET", "/user")
        if isinstance(user, str):
            return user
        return _format_user_markdown(user)
    except Exception as e:
        _handle_not_found(e, "user", "current user", "Current user not found or not authenticated.")
        raise


async def get_version(gitea_client: GiteaClient) -> ResourceResult:
    """Get server application version."""
    try:
        data = await gitea_client.request("GET", "/version")
        if isinstance(data, str):
            return data
        return str(data.get("version", "Unknown"))
    except Exception as e:
        _handle_not_found(e, "version", "server", "Version information not available.")
        raise


async def get_active_token_scopes(gitea_client: GiteaClient) -> ResourceResult:
    """Get the scopes of the active Gitea token.

    Returns a JSON object with the active token's scopes.
    Returns ``{"scopes": null}`` if the token cannot be identified.
    """
    try:
        user_data = await gitea_client.request("GET", "/user")
        if not isinstance(user_data, dict):
            return json.dumps({"scopes": None})
        username = user_data.get("login")
        if not username:
            return json.dumps({"scopes": None})

        tokens_data = await gitea_client.request("GET", f"/users/{username}/tokens")
        if not isinstance(tokens_data, list):
            return json.dumps({"scopes": None})

        raw_token = gitea_client._config.token
        last_eight = raw_token[-8:]
        for token in tokens_data:
            if isinstance(token, dict) and token.get("token_last_eight") == last_eight:
                scopes = token.get("scopes")
                if scopes and isinstance(scopes, list):
                    return json.dumps({"scopes": sorted(scopes)})
        return json.dumps({"scopes": None})
    except Exception:
        logger.exception("Failed to retrieve active token scopes")
        return json.dumps({"scopes": None})


async def get_org(orgname: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get organization profile information."""
    try:
        org = await gitea_client.request("GET", f"/orgs/{orgname}")
        if isinstance(org, str):
            return org
        return _format_user_markdown(org)
    except Exception as e:
        _handle_not_found(e, "organization", orgname, f"Organization '{orgname}' not found.")
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


def register_custom_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    registry: Any,  # ResourceRegistry - using Any to avoid circular import
    openapi_spec: dict[str, Any] | None = None,
) -> None:
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

    Args:
        mcp: FastMCP server instance
        gitea_client: GiteaClient for API calls
        registry: ResourceRegistry to record registered resources
        openapi_spec: Optional OpenAPI spec dictionary for accessing server metadata
    """

    def make_resource(
        func: Callable[..., Awaitable[str]],
    ) -> Callable[..., Awaitable[str]]:
        """Wrap a resource function to inject gitea_client.

        Creates a wrapper that:
        - For functions with parameters: wrapper has explicit parameters matching
          the original (minus gitea_client). This ensures FastMCP can correctly detect URI template parameters.
        - For functions with no parameters (e.g., gitea://user): wrapper has zero parameters.
          This triggers Resource (fixed URI) instead of ResourceTemplate.
        """

        sig = inspect.signature(func)
        params: list[inspect.Parameter] = []

        # Collect all parameters except 'gitea_client'
        for param in sig.parameters.values():
            if param.name == "gitea_client":
                continue
            # Only support keyword-only or positional-or-keyword parameters
            if param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                msg = f"Resource function {func.__name__} does not support positional-only or *args parameters"
                raise ValueError(msg)
            params.append(param)

        if params:
            # Function has parameters -> wrapper with explicit parameters
            wrapper_sig = inspect.Signature(params, return_annotation=str)

            @wraps(func)
            async def wrapper_with_params(**kwargs: Any) -> str:
                kwargs["gitea_client"] = gitea_client
                return await func(**kwargs)

            wrapper_with_params.__signature__ = wrapper_sig  # type: ignore[attr-defined]
            return wrapper_with_params

        # No-parameter case: wrapper with zero parameters
        @wraps(func)
        async def wrapper_no_params() -> str:
            return await func(gitea_client=gitea_client)

        # Override signature to have zero parameters (avoid inheriting original's gitea_client)
        wrapper_no_params.__signature__ = inspect.Signature(return_annotation=str)  # type: ignore[attr-defined]
        return wrapper_no_params

    # Define server info resource using OpenAPI spec metadata
    if openapi_spec is None:
        # If no spec provided, we cannot create server info; skip registration later
        get_server_info = None
    else:

        async def get_server_info(gitea_client: GiteaClient) -> ResourceResult:  # noqa ARG001
            """Get server metadata from OpenAPI info block."""
            info = openapi_spec.get("info", {})
            title = info.get("title", "Unknown")
            version = info.get("version", "Unknown")
            description = info.get("description", "")
            # Format as Markdown
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
            return _build_markdown(lines)

    # Custom-formatted resources with better UX
    custom_resources: list[
        tuple[str, Callable[..., Awaitable[str]], str, set[str], dict[str, Any] | None]
    ] = [
        (
            "gitea://repos/{owner}/{repo}",
            get_repository,
            "text/markdown",
            {"wrapper", "repository"},
            {
                "cache_ttl": CACHE_TTL_REPOSITORY,
                "fastmcp": {"_internal": {"required_scope": "read:repository"}},
            },
        ),
        (
            "gitea://repos/{owner}/{repo}/readme",
            get_readme,
            "text/plain",
            {"wrapper", "readme"},
            {
                "cache_ttl": CACHE_TTL_README,
                "fastmcp": {"_internal": {"required_scope": "read:repository"}},
            },
        ),
        (
            "gitea://repos/{owner}/{repo}/issues",
            list_repo_issues,
            "text/markdown",
            {"wrapper", "issues"},
            {"fastmcp": {"_internal": {"required_scope": "read:repository"}}},  # Use default TTL (30s) - issues change frequently
        ),
        (
            "gitea://repos/{owner}/{repo}/issues/open",
            list_repo_issues_open,
            "text/markdown",
            {"wrapper", "issues"},
            {"fastmcp": {"_internal": {"required_scope": "read:repository"}}},
        ),
        (
            "gitea://repos/{owner}/{repo}/issues/closed",
            list_repo_issues_closed,
            "text/markdown",
            {"wrapper", "issues"},
            {"fastmcp": {"_internal": {"required_scope": "read:repository"}}},
        ),
        (
            "gitea://repos/{owner}/{repo}/pulls",
            list_repo_pulls,
            "text/markdown",
            {"wrapper", "pull_requests"},
            {"fastmcp": {"_internal": {"required_scope": "read:repository"}}},  # Use default TTL (30s) - PRs change frequently
        ),
        (
            "gitea://repos/{owner}/{repo}/pulls/open",
            list_repo_pulls_open,
            "text/markdown",
            {"wrapper", "pull_requests"},
            {"fastmcp": {"_internal": {"required_scope": "read:repository"}}},
        ),
        (
            "gitea://repos/{owner}/{repo}/files/{path}",
            get_file,
            "text/plain",
            {"wrapper", "files"},
            {"fastmcp": {"_internal": {"required_scope": "read:repository"}}},
        ),  # Default TTL
        (
            "gitea://repos/{owner}/{repo}/releases",
            list_repo_releases,
            "text/markdown",
            {"wrapper", "releases"},
            {"cache_ttl": CACHE_TTL_RELEASES, "fastmcp": {"_internal": {"required_scope": "read:repository"}}},
        ),
        (
            "gitea://users/{username}",
            get_user,
            "text/markdown",
            {"wrapper", "user"},
            {"cache_ttl": CACHE_TTL_USERS, "fastmcp": {"_internal": {"required_scope": "read:user"}}},
        ),
        (
            "gitea://user",
            get_current_user,
            "text/markdown",
            {"wrapper", "user"},
            {"cache_ttl": CACHE_TTL_USERS, "fastmcp": {"_internal": {"required_scope": "read:user"}}},
        ),
        (
            "gitea://orgs/{orgname}",
            get_org,
            "text/markdown",
            {"wrapper", "organization"},
            {"cache_ttl": CACHE_TTL_USERS, "fastmcp": {"_internal": {"required_scope": "read:organization"}}},
        ),
        # Server version (application version from /version endpoint)
        (
            "gitea://version",
            get_version,
            "text/plain",
            {"wrapper", "server"},
            {"fastmcp": {"_internal": {"required_scope": None}}},
        ),
        # Active token scopes (for agent self-discovery)
        (
            "gitea://token/scopes",
            get_active_token_scopes,
            "application/json",
            {"wrapper", "server"},
            {"fastmcp": {"_internal": {"required_scope": "read:user"}}},
        ),
    ]

    # Add server info resource if OpenAPI spec is available
    if get_server_info is not None:
        custom_resources.append(
            (
                "gitea://server/info",
                get_server_info,
                "text/markdown",
                {"wrapper", "server"},
                {"fastmcp": {"_internal": {"required_scope": None}}},
            )
        )

    for uri_template, func, mime_type, tags, meta in custom_resources:
        kwargs: dict[str, Any] = {"mime_type": mime_type, "tags": tags}
        if meta is not None:
            kwargs["meta"] = meta
        wrapped_func = make_resource(func)
        mcp.resource(uri_template, **kwargs)(wrapped_func)
        # Record in registry catalog (custom resources override auto-generated)
        registry.record(
            uri=uri_template,
            func=wrapped_func,
            mime_type=mime_type,
            tags=tags,
            meta=meta,
        )
