"""Hand-written MCP resource implementations with Markdown formatting.

These custom resources override auto-generated ones with the same URI,
providing user-friendly formatted output for common use cases.
"""

import base64
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ResourceError

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    CACHE_TTL_README,
    CACHE_TTL_RELEASES,
    CACHE_TTL_REPOSITORY,
    CACHE_TTL_USERS,
)
from gitea_mcp_server.resources.format import (
    ResourceResult,
    _build_server_info_markdown,
    _format_issues_markdown,
    _format_pulls_markdown,
    _format_release_markdown,
    _format_repo_markdown,
    _format_user_markdown,
    _handle_not_found,
)
from gitea_mcp_server.resources.scope import make_resource_meta

logger = logging.getLogger(__name__)


def resource_handler(
    resource_type: str,
    id_format: str,
    error_message: str,
) -> Callable[..., Callable[..., Awaitable[str]]]:
    """Decorator that wraps a resource function with error handling.

    Catches exceptions, converts 404 to structured ResourceError
    via _handle_not_found. The decorated function only needs to
    perform the API request and formatting logic.

    Args:
        resource_type: Machine-readable type (e.g. "repository", "file")
        id_format: Template string for resource_id using func kwargs
            (e.g. "{owner}/{repo}")
        error_message: User-facing error message template using func kwargs
            (e.g. "Repository '{owner}/{repo}' not found.")
    """

    def decorator(func: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        sig = inspect.signature(func)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> ResourceResult:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # Map positional args to parameter names for format templates
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                resource_id = id_format.format(**bound.arguments)
                msg = error_message.format(**bound.arguments)
                _handle_not_found(e, resource_type, resource_id, msg)
                raise

        return wrapper

    return decorator


@resource_handler("repository", "{owner}/{repo}", "Repository '{owner}/{repo}' not found.")
async def get_repository(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get full repository metadata with nice Markdown formatting."""
    data = await gitea_client.request("GET", f"/repos/{owner}/{repo}")
    if isinstance(data, str):
        return data
    return _format_repo_markdown(data)


@resource_handler("readme", "{owner}/{repo}", "README not found for repository '{owner}/{repo}'.")
async def get_readme(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get repository README content."""
    response = await gitea_client.request("GET", f"/repos/{owner}/{repo}/contents/README.md")
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return str(response)
    if response.get("encoding") == "base64":
        raw: str = base64.b64decode(response.get("content", "")).decode("utf-8")
        return raw
    return cast("str", response.get("content", ""))


@resource_handler("issues", "{owner}/{repo}", "Repository '{owner}/{repo}' not found or has no issues.")
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

    issues = await gitea_client.request("GET", f"/repos/{owner}/{repo}/issues", params=params)
    if isinstance(issues, str):
        return issues

    title = f"Issues ({state})" if state else "All Issues"
    return _format_issues_markdown(issues, title=title)


@resource_handler("pulls", "{owner}/{repo}", "Repository '{owner}/{repo}' not found or has no pull requests.")
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

    pulls = await gitea_client.request("GET", f"/repos/{owner}/{repo}/pulls", params=params)
    if isinstance(pulls, str):
        return pulls

    title = f"Pull Requests ({state})" if state else "All Pull Requests"
    return _format_pulls_markdown(pulls, title=title)


@resource_handler("file", "{owner}/{repo}/{path}", "File '{path}' not found in repository '{owner}/{repo}'.")
async def get_file(
    owner: str, repo: str, path: str, gitea_client: GiteaClient, ref: str | None = None
) -> ResourceResult:
    """Get file content from repository."""
    params = {}
    if ref:
        params["ref"] = ref

    response = await gitea_client.request(
        "GET", f"/repos/{owner}/{repo}/contents/{path}", params=params
    )

    if isinstance(response, str):
        return response

    if not isinstance(response, dict):
        return str(response)

    if response.get("encoding") == "base64":
        raw: str = base64.b64decode(response.get("content", "")).decode("utf-8")
        return raw
    return cast("str", response.get("content", ""))


@resource_handler("releases", "{owner}/{repo}", "Repository '{owner}/{repo}' not found or has no releases.")
async def list_repo_releases(owner: str, repo: str, gitea_client: GiteaClient) -> ResourceResult:
    """List releases for a repository."""
    releases = await gitea_client.request("GET", f"/repos/{owner}/{repo}/releases")
    if isinstance(releases, str):
        return releases

    if not releases:
        return f"# Releases for {owner}/{repo}\n\nNo releases found."

    lines = [f"# Releases for {owner}/{repo}", "", f"Showing {len(releases)} releases", ""]

    for release in releases:
        lines.append(_format_release_markdown(release))
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


@resource_handler("user", "{username}", "User '{username}' not found.")
async def get_user(username: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get user profile information."""
    user = await gitea_client.request("GET", f"/users/{username}")
    if isinstance(user, str):
        return user
    return _format_user_markdown(user)


@resource_handler("user", "current user", "Current user not found or not authenticated.")
async def get_current_user(gitea_client: GiteaClient) -> ResourceResult:
    """Get current authenticated user profile information."""
    user = await gitea_client.request("GET", "/user")
    if isinstance(user, str):
        return user
    return _format_user_markdown(user)


@resource_handler("version", "server", "Version information not available.")
async def get_version(gitea_client: GiteaClient) -> ResourceResult:
    """Get server application version."""
    data = await gitea_client.request("GET", "/version")
    if isinstance(data, str):
        return data
    return str(data.get("version", "Unknown"))


async def get_active_token_scopes(gitea_client: GiteaClient) -> ResourceResult:
    """Get the scopes of the active Gitea token."""
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

        raw_token = gitea_client.config.token
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


@resource_handler("organization", "{orgname}", "Organization '{orgname}' not found.")
async def get_org(orgname: str, gitea_client: GiteaClient) -> ResourceResult:
    """Get organization profile information."""
    org = await gitea_client.request("GET", f"/orgs/{orgname}")
    if isinstance(org, str):
        return org
    return _format_user_markdown(org)


def _inject_gitea_client_depends(
    func: Callable[..., Awaitable[str]],
    gitea_client: GiteaClient,
) -> Callable[..., Awaitable[str]]:
    """Replace ``gitea_client`` parameter with FastMCP ``Depends()``.

    FastMCP's ``Depends()`` marks a parameter as a runtime dependency: it is
    excluded from the MCP schema and auto-injected at call time.  This is the
    FastMCP-native equivalent of the old ``make_resource`` wrapper.

    Functions that lack a ``gitea_client`` parameter are returned unchanged —
    they don't need the client and should not advertise the dependency.
    """
    sig = inspect.signature(func)
    if "gitea_client" not in sig.parameters:
        return func

    def _get_client() -> GiteaClient:
        return gitea_client

    params: list[inspect.Parameter] = []
    for p in sig.parameters.values():
        updated = p.replace(default=Depends(_get_client)) if p.name == "gitea_client" else p
        params.append(updated)

    func.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]
    return func


def register_custom_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    registry: Any,
    openapi_spec: dict[str, Any] | None = None,
) -> None:
    """Register custom-formatted and custom resources.

    These override any auto-generated resources with the same URI.
    Uses FastMCP's last-registration-wins ordering.
    """

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
                **make_resource_meta("read:repository"),
            },
        ),
        (
            "gitea://repos/{owner}/{repo}/readme",
            get_readme,
            "text/plain",
            {"wrapper", "readme"},
            {
                "cache_ttl": CACHE_TTL_README,
                **make_resource_meta("read:repository"),
            },
        ),
        (
            "gitea://repos/{owner}/{repo}/issues{?state}",
            list_repo_issues,
            "text/markdown",
            {"wrapper", "issues"},
            make_resource_meta("read:repository"),
        ),
        (
            "gitea://repos/{owner}/{repo}/pulls{?state}",
            list_repo_pulls,
            "text/markdown",
            {"wrapper", "pull_requests"},
            make_resource_meta("read:repository"),
        ),
        (
            "gitea://repos/{owner}/{repo}/files/{path*}",
            get_file,
            "text/plain",
            {"wrapper", "files"},
            make_resource_meta("read:repository"),
        ),
        (
            "gitea://repos/{owner}/{repo}/releases",
            list_repo_releases,
            "text/markdown",
            {"wrapper", "releases"},
            {
                "cache_ttl": CACHE_TTL_RELEASES,
                **make_resource_meta("read:repository"),
            },
        ),
        (
            "gitea://users/{username}",
            get_user,
            "text/markdown",
            {"wrapper", "user"},
            {
                "cache_ttl": CACHE_TTL_USERS,
                **make_resource_meta("read:user"),
            },
        ),
        (
            "gitea://user",
            get_current_user,
            "text/markdown",
            {"wrapper", "user"},
            {
                "cache_ttl": CACHE_TTL_USERS,
                **make_resource_meta("read:user"),
            },
        ),
        (
            "gitea://orgs/{orgname}",
            get_org,
            "text/markdown",
            {"wrapper", "organization"},
            {
                "cache_ttl": CACHE_TTL_USERS,
                **make_resource_meta("read:organization"),
            },
        ),
        (
            "gitea://version",
            get_version,
            "text/plain",
            {"wrapper", "server"},
            make_resource_meta(None),
        ),
        (
            "gitea://token/scopes",
            get_active_token_scopes,
            "application/json",
            {"wrapper", "server"},
            make_resource_meta("read:user"),
        ),
    ]

    if openapi_spec is not None:
        async def get_server_info() -> ResourceResult:
            """Get server metadata from OpenAPI info block."""
            return _build_server_info_markdown(openapi_spec)

        custom_resources.append(
            (
                "gitea://server/info",
                get_server_info,
                "text/markdown",
                {"wrapper", "server"},
                make_resource_meta(None),
            )
        )

    for uri_template, func, mime_type, tags, meta in custom_resources:
        kwargs: dict[str, Any] = {"mime_type": mime_type, "tags": tags}
        if meta is not None:
            kwargs["meta"] = meta
        wrapped_func = _inject_gitea_client_depends(func, gitea_client)
        mcp.resource(uri_template, **kwargs)(wrapped_func)
        registry.record(
            uri=uri_template,
            func=wrapped_func,
            mime_type=mime_type,
            tags=tags,
            meta=meta,
        )


__all__ = [
    "_inject_gitea_client_depends",
    "get_active_token_scopes",
    "get_current_user",
    "get_file",
    "get_org",
    "get_readme",
    "get_repository",
    "get_user",
    "get_version",
    "list_repo_issues",
    "list_repo_pulls",
    "list_repo_releases",
    "register_custom_resources",
    "resource_handler",
]
