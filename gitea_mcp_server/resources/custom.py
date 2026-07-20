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
from fastmcp.exceptions import ResourceError

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    CACHE_TTL_README,
    CACHE_TTL_RELEASES,
    CACHE_TTL_REPOSITORY,
    CACHE_TTL_USERS,
)
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.format import (
    ResourceResult,
    _build_server_info_markdown,
    _format_issues_markdown,
    _format_labels_markdown,
    _format_pulls_markdown,
    _format_release_markdown,
    _format_repo_markdown,
    _format_user_markdown,
    _handle_not_found,
)
from gitea_mcp_server.resources.scope import has_sufficient_scope, scope_meta

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


def _find_matching_token_scopes(tokens_data: list, raw_token: str) -> list[str] | None:
    """Match token by last eight characters and return sorted scopes, or None."""
    last_eight = raw_token[-8:]
    for token in tokens_data:
        if isinstance(token, dict) and token.get("token_last_eight") == last_eight:
            scopes = token.get("scopes")
            if scopes and isinstance(scopes, list):
                return sorted(scopes)
    return None


def register_custom_resources(  # noqa: PLR0915
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec | None = None,
    available_scopes: set[str] | None = None,
) -> None:
    """Register custom-formatted and custom resources.

    Each resource function is defined as a closure that naturally
    captures ``gitea_client`` (and ``openapi_spec`` where needed),
    so function signatures expose only URI-relevant parameters.
    Uses FastMCP's last-registration-wins ordering.

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: Optional OpenAPI spec for server info resource.
        available_scopes: Set of scopes the token has, or None (no filtering).
            Resources whose ``required_scope`` is not satisfied are skipped.
    """

    def _register(
        uri: str, mime_type: str, tags: set[str], meta: dict[str, Any] | None
    ) -> Callable[
        [Callable[..., Awaitable[ResourceResult]]], Callable[..., Awaitable[ResourceResult]]
    ]:
        # Check scope before registering: skip if the token lacks the
        # required scope for this resource.
        required_scope = (meta or {}).get("required_scope")
        if (
            required_scope is not None
            and available_scopes is not None
            and not has_sufficient_scope(required_scope, available_scopes)
        ):
            logger.debug(
                "Skipping custom resource %s: requires %s",
                uri,
                required_scope,
            )

            def passthrough(
                func: Callable[..., Awaitable[ResourceResult]],
            ) -> Callable[..., Awaitable[ResourceResult]]:
                return func

            return passthrough

        def deco(
            func: Callable[..., Awaitable[ResourceResult]],
        ) -> Callable[..., Awaitable[ResourceResult]]:
            mcp.resource(uri, mime_type=mime_type, tags=tags, meta=meta)(func)
            return func

        return deco

    # ── repository ──────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_REPOSITORY, **scope_meta("read:repository")}

    @_register(
        "gitea://repos/{owner}/{repo}",
        mime_type="text/markdown",
        tags={"wrapper", "repository"},
        meta=_meta,
    )
    @resource_handler("repository", "{owner}/{repo}", "Repository '{owner}/{repo}' not found.")
    async def get_repository(owner: str, repo: str) -> ResourceResult:
        """Get full repository metadata with nice Markdown formatting."""
        data = await gitea_client.request("GET", f"/repos/{owner}/{repo}")
        if isinstance(data, str):
            return data
        return _format_repo_markdown(data)

    # ── readme ──────────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_README, **scope_meta("read:repository")}

    @_register(
        "gitea://repos/{owner}/{repo}/readme",
        mime_type="text/plain",
        tags={"wrapper", "readme"},
        meta=_meta,
    )
    @resource_handler(
        "readme", "{owner}/{repo}", "README not found for repository '{owner}/{repo}'."
    )
    async def get_readme(owner: str, repo: str) -> ResourceResult:
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

    # ── issues ──────────────────────────────────────────────────────────────

    _meta = scope_meta("read:repository")

    @_register(
        "gitea://repos/{owner}/{repo}/issues{?state}",
        mime_type="text/markdown",
        tags={"wrapper", "issues"},
        meta=_meta,
    )
    @resource_handler(
        "issues", "{owner}/{repo}", "Repository '{owner}/{repo}' not found or has no issues."
    )
    async def list_repo_issues(owner: str, repo: str, state: str | None = None) -> ResourceResult:
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

    # ── pulls ───────────────────────────────────────────────────────────────

    _meta = scope_meta("read:repository")

    @_register(
        "gitea://repos/{owner}/{repo}/pulls{?state}",
        mime_type="text/markdown",
        tags={"wrapper", "pull_requests"},
        meta=_meta,
    )
    @resource_handler(
        "pulls", "{owner}/{repo}", "Repository '{owner}/{repo}' not found or has no pull requests."
    )
    async def list_repo_pulls(owner: str, repo: str, state: str | None = None) -> ResourceResult:
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

    # ── file ────────────────────────────────────────────────────────────────

    _meta = scope_meta("read:repository")

    @_register(
        "gitea://repos/{owner}/{repo}/files/{path*}",
        mime_type="text/plain",
        tags={"wrapper", "files"},
        meta=_meta,
    )
    @resource_handler(
        "file", "{owner}/{repo}/{path}", "File '{path}' not found in repository '{owner}/{repo}'."
    )
    async def get_file(owner: str, repo: str, path: str, ref: str | None = None) -> ResourceResult:
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

    # ── releases ────────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_RELEASES, **scope_meta("read:repository")}

    @_register(
        "gitea://repos/{owner}/{repo}/releases",
        mime_type="text/markdown",
        tags={"wrapper", "releases"},
        meta=_meta,
    )
    @resource_handler(
        "releases", "{owner}/{repo}", "Repository '{owner}/{repo}' not found or has no releases."
    )
    async def list_repo_releases(owner: str, repo: str) -> ResourceResult:
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

    # ── labels ──────────────────────────────────────────────────────────────

    _meta = scope_meta("read:issue")

    @_register(
        "gitea://repos/{owner}/{repo}/labels",
        mime_type="text/markdown",
        tags={"wrapper", "labels"},
        meta=_meta,
    )
    @resource_handler(
        "labels", "{owner}/{repo}", "Labels not found for repository '{owner}/{repo}'."
    )
    async def list_repo_labels(owner: str, repo: str) -> ResourceResult:
        """List labels for a repository with format and validation hints."""
        labels = await gitea_client.request("GET", f"/repos/{owner}/{repo}/labels")
        if isinstance(labels, str):
            return labels
        return _format_labels_markdown(labels, owner, repo)

    # ── user ────────────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_USERS, **scope_meta("read:user")}

    @_register(
        "gitea://users/{username}", mime_type="text/markdown", tags={"wrapper", "user"}, meta=_meta
    )
    @resource_handler("user", "{username}", "User '{username}' not found.")
    async def get_user(username: str) -> ResourceResult:
        """Get user profile information."""
        user = await gitea_client.request("GET", f"/users/{username}")
        if isinstance(user, str):
            return user
        return _format_user_markdown(user)

    # ── current user ────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_USERS, **scope_meta("read:user")}

    @_register("gitea://user", mime_type="text/markdown", tags={"wrapper", "user"}, meta=_meta)
    @resource_handler("user", "current user", "Current user not found or not authenticated.")
    async def get_current_user() -> ResourceResult:
        """Get current authenticated user profile information."""
        user = await gitea_client.request("GET", "/user")
        if isinstance(user, str):
            return user
        return _format_user_markdown(user)

    # ── organization ────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_USERS, **scope_meta("read:organization")}

    @_register(
        "gitea://orgs/{orgname}",
        mime_type="text/markdown",
        tags={"wrapper", "organization"},
        meta=_meta,
    )
    @resource_handler("organization", "{orgname}", "Organization '{orgname}' not found.")
    async def get_org(orgname: str) -> ResourceResult:
        """Get organization profile information."""
        org = await gitea_client.request("GET", f"/orgs/{orgname}")
        if isinstance(org, str):
            return org
        return _format_user_markdown(org)

    # ── version ─────────────────────────────────────────────────────────────

    _meta = scope_meta(None)

    @_register("gitea://version", mime_type="text/plain", tags={"wrapper", "server"}, meta=_meta)
    @resource_handler("version", "server", "Version information not available.")
    async def get_version() -> ResourceResult:
        """Get server application version."""
        data = await gitea_client.request("GET", "/version")
        if isinstance(data, str):
            return data
        return str(data.get("version", "Unknown"))

    # ── token scopes ────────────────────────────────────────────────────────

    _meta = scope_meta("read:user")

    @_register(
        "gitea://token/scopes", mime_type="application/json", tags={"wrapper", "server"}, meta=_meta
    )
    async def get_active_token_scopes() -> ResourceResult:
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

            scopes = _find_matching_token_scopes(tokens_data, gitea_client.config.token)
            return json.dumps({"scopes": scopes})
        except Exception:
            logger.exception("Failed to retrieve active token scopes")
            return json.dumps({"scopes": None})

    # ── server info (only when openapi_spec is available) ───────────────────

    if openapi_spec is not None:
        _meta = scope_meta(None)

        @_register(
            "gitea://server/info", mime_type="text/markdown", tags={"wrapper", "server"}, meta=_meta
        )
        async def get_server_info() -> ResourceResult:
            """Get server metadata from OpenAPI info block."""
            return _build_server_info_markdown(openapi_spec)


__all__ = [
    "_find_matching_token_scopes",
    "register_custom_resources",
    "resource_handler",
]
