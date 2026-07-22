"""Hand-written MCP resource implementations.

Custom resources return raw data (JSON or text) with metadata describing the
response schema and a ``format_hint`` for the display layer.  No formatting is
done at the resource level — that is the responsibility of the unified display
pipeline in ``mcp_tools.py`` and ``tools/display.py``.
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
from fastmcp.resources import ResourceContent, ResourceResult

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    CACHE_TTL_README,
    CACHE_TTL_RELEASES,
    CACHE_TTL_REPOSITORY,
    CACHE_TTL_USERS,
    HTTP_STATUS_NOT_FOUND,
)
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.scope import has_sufficient_scope, scope_meta
from gitea_mcp_server.tools.display import _build_server_info_markdown
from gitea_mcp_server.tools.schemas import _get_success_schema

logger = logging.getLogger(__name__)


def _build_resource_meta(
    *,
    response_schema: dict[str, Any] | None = None,
    format_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the content metadata dict for a JSON resource response.

    Args:
        response_schema: Unresolved response schema for ``$ref``-aware collapse.
        format_hint: Name of the registered formatter to use for markdown rendering.
        extra: Additional metadata keys (e.g. ``{"owner": ..., "repo": ...}``).

    Returns:
        Metadata dict, or ``None`` if empty.
    """
    meta: dict[str, Any] = {}
    if response_schema is not None:
        meta["response_schema"] = response_schema
    if format_hint is not None:
        meta["format_hint"] = format_hint
    if extra:
        meta.update(extra)
    return meta if meta else None


def resource_handler(
    resource_type: str,
    id_format: str,
    error_message: str,
) -> Callable[..., Callable[..., Awaitable[ResourceResult]]]:
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

    def decorator(
        func: Callable[..., Awaitable[ResourceResult]],
    ) -> Callable[..., Awaitable[ResourceResult]]:
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

            # Return a no-op passthrough instead of registering with
            # mcp.resource().  Since _register is used as a decorator
            # (outermost on each resource handler), returning passthrough
            # means the decorated function is returned unmodified — it
            # simply never gets wired into FastMCP.  The inner decorator
            # (resource_handler) is still applied for error handling, but
            # without an mcp.resource() call, the URI template is never
            # exposed to clients.
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

    # Pre-derive response schemas for the display layer.
    # Each maps to the Gitea API endpoint the handler calls internally.
    _repo_schema = _get_success_schema(openapi_spec, "/repos/{owner}/{repo}", "get", resolve=False) if openapi_spec else None
    _issues_schema = _get_success_schema(openapi_spec, "/repos/{owner}/{repo}/issues", "get", resolve=False) if openapi_spec else None
    _pulls_schema = _get_success_schema(openapi_spec, "/repos/{owner}/{repo}/pulls", "get", resolve=False) if openapi_spec else None
    _releases_schema = _get_success_schema(openapi_spec, "/repos/{owner}/{repo}/releases", "get", resolve=False) if openapi_spec else None
    _labels_schema = _get_success_schema(openapi_spec, "/repos/{owner}/{repo}/labels", "get", resolve=False) if openapi_spec else None
    _user_schema = _get_success_schema(openapi_spec, "/users/{username}", "get", resolve=False) if openapi_spec else None

    # ── repository ──────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_REPOSITORY, **scope_meta("read:repository")}

    @_register(
        "gitea://repos/{owner}/{repo}",
        mime_type="application/json",
        tags={"wrapper", "repository"},
        meta=_meta,
    )
    @resource_handler("repository", "{owner}/{repo}", "Repository '{owner}/{repo}' not found.")
    async def get_repository(owner: str, repo: str) -> ResourceResult:
        """Get full repository metadata."""
        data = await gitea_client.request("GET", f"/repos/{owner}/{repo}")
        if isinstance(data, str):
            return ResourceResult(contents=[ResourceContent(content=data, mime_type="text/plain")])
        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(data),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_repo_schema,
                    format_hint="repository",
                ),
            )]
        )

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
            return ResourceResult(contents=[ResourceContent(content=response, mime_type="text/plain")])
        if not isinstance(response, dict):
            return ResourceResult(contents=[ResourceContent(content=str(response), mime_type="text/plain")])
        if response.get("encoding") == "base64":
            raw: str = base64.b64decode(response.get("content", "")).decode("utf-8")
            return ResourceResult(contents=[ResourceContent(content=raw, mime_type="text/plain")])
        content = cast("str", response.get("content", ""))
        return ResourceResult(contents=[ResourceContent(content=content, mime_type="text/plain")])

    # ── issues ──────────────────────────────────────────────────────────────

    _meta = scope_meta("read:repository")

    @_register(
        "gitea://repos/{owner}/{repo}/issues{?state}",
        mime_type="application/json",
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
            return ResourceResult(contents=[ResourceContent(content=issues, mime_type="text/plain")])

        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(issues),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_issues_schema,
                    format_hint="issues",
                ),
            )]
        )

    # ── pulls ───────────────────────────────────────────────────────────────

    _meta = scope_meta("read:repository")

    @_register(
        "gitea://repos/{owner}/{repo}/pulls{?state}",
        mime_type="application/json",
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
            return ResourceResult(contents=[ResourceContent(content=pulls, mime_type="text/plain")])

        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(pulls),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_pulls_schema,
                    format_hint="pull_requests",
                ),
            )]
        )

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
            return ResourceResult(contents=[ResourceContent(content=response, mime_type="text/plain")])

        if not isinstance(response, dict):
            return ResourceResult(contents=[ResourceContent(content=str(response), mime_type="text/plain")])

        if response.get("encoding") == "base64":
            raw: str = base64.b64decode(response.get("content", "")).decode("utf-8")
            return ResourceResult(contents=[ResourceContent(content=raw, mime_type="text/plain")])
        content = cast("str", response.get("content", ""))
        return ResourceResult(contents=[ResourceContent(content=content, mime_type="text/plain")])

    # ── releases ─────────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_RELEASES, **scope_meta("read:repository")}

    @_register(
        "gitea://repos/{owner}/{repo}/releases",
        mime_type="application/json",
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
            return ResourceResult(contents=[ResourceContent(content=releases, mime_type="text/plain")])

        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(releases),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_releases_schema,
                    format_hint="release",
                ),
            )]
        )

    # ── labels ──────────────────────────────────────────────────────────────

    _meta = scope_meta("read:issue")

    @_register(
        "gitea://repos/{owner}/{repo}/labels",
        mime_type="application/json",
        tags={"wrapper", "labels"},
        meta=_meta,
    )
    @resource_handler(
        "labels", "{owner}/{repo}", "Labels not found for repository '{owner}/{repo}'."
    )
    async def list_repo_labels(owner: str, repo: str) -> ResourceResult:
        """List labels for a repository."""
        labels = await gitea_client.request("GET", f"/repos/{owner}/{repo}/labels")
        if isinstance(labels, str):
            return ResourceResult(contents=[ResourceContent(content=labels, mime_type="text/plain")])

        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(labels),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_labels_schema,
                    format_hint="labels",
                    extra={"owner": owner, "repo": repo},
                ),
            )]
        )

    # ── user ────────────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_USERS, **scope_meta("read:user")}

    @_register(
        "gitea://users/{username}", mime_type="application/json", tags={"wrapper", "user"}, meta=_meta
    )
    @resource_handler("user", "{username}", "User '{username}' not found.")
    async def get_user(username: str) -> ResourceResult:
        """Get user profile information."""
        user = await gitea_client.request("GET", f"/users/{username}")
        if isinstance(user, str):
            return ResourceResult(contents=[ResourceContent(content=user, mime_type="text/plain")])
        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(user),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_user_schema,
                    format_hint="user",
                ),
            )]
        )

    # ── current user ────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_USERS, **scope_meta("read:user")}

    @_register("gitea://user", mime_type="application/json", tags={"wrapper", "user"}, meta=_meta)
    @resource_handler("user", "current user", "Current user not found or not authenticated.")
    async def get_current_user() -> ResourceResult:
        """Get current authenticated user profile information."""
        user = await gitea_client.request("GET", "/user")
        if isinstance(user, str):
            return ResourceResult(contents=[ResourceContent(content=user, mime_type="text/plain")])
        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(user),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_user_schema,
                    format_hint="user",
                ),
            )]
        )

    # ── organization ────────────────────────────────────────────────────────

    _meta = {"cache_ttl": CACHE_TTL_USERS, **scope_meta("read:organization")}

    @_register(
        "gitea://orgs/{orgname}",
        mime_type="application/json",
        tags={"wrapper", "organization"},
        meta=_meta,
    )
    @resource_handler("organization", "{orgname}", "Organization '{orgname}' not found.")
    async def get_org(orgname: str) -> ResourceResult:
        """Get organization profile information."""
        org = await gitea_client.request("GET", f"/orgs/{orgname}")
        if isinstance(org, str):
            return ResourceResult(contents=[ResourceContent(content=org, mime_type="text/plain")])
        return ResourceResult(
            contents=[ResourceContent(
                content=json.dumps(org),
                mime_type="application/json",
                meta=_build_resource_meta(
                    response_schema=_user_schema,
                    format_hint="user",
                ),
            )]
        )

    # ── version ─────────────────────────────────────────────────────────────

    _meta = scope_meta(None)

    @_register("gitea://version", mime_type="text/plain", tags={"wrapper", "server"}, meta=_meta)
    @resource_handler("version", "server", "Version information not available.")
    async def get_version() -> ResourceResult:
        """Get server application version."""
        data = await gitea_client.request("GET", "/version")
        if isinstance(data, str):
            return ResourceResult(contents=[ResourceContent(content=data, mime_type="text/plain")])
        content = str(data.get("version", "Unknown")) if isinstance(data, dict) else str(data)
        return ResourceResult(contents=[ResourceContent(content=content, mime_type="text/plain")])

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
                return ResourceResult(contents=[ResourceContent(content=json.dumps({"scopes": None}), mime_type="application/json")])
            username = user_data.get("login")
            if not username:
                return ResourceResult(contents=[ResourceContent(content=json.dumps({"scopes": None}), mime_type="application/json")])

            tokens_data = await gitea_client.request("GET", f"/users/{username}/tokens")
            if not isinstance(tokens_data, list):
                return ResourceResult(contents=[ResourceContent(content=json.dumps({"scopes": None}), mime_type="application/json")])

            scopes = _find_matching_token_scopes(tokens_data, gitea_client.config.token)
            return ResourceResult(contents=[ResourceContent(content=json.dumps({"scopes": scopes}), mime_type="application/json")])
        except Exception:
            logger.exception("Failed to retrieve active token scopes")
            return ResourceResult(contents=[ResourceContent(content=json.dumps({"scopes": None}), mime_type="application/json")])

    # ── server info (only when openapi_spec is available) ───────────────────

    if openapi_spec is not None:
        _meta = scope_meta(None)

        @_register(
            "gitea://server/info", mime_type="text/markdown", tags={"wrapper", "server"}, meta=_meta
        )
        async def get_server_info() -> ResourceResult:
            """Get server metadata from OpenAPI info block."""
            return ResourceResult(contents=[ResourceContent(
                content=_build_server_info_markdown(openapi_spec),
                mime_type="text/markdown",
            )])


__all__ = [
    "_find_matching_token_scopes",
    "_handle_not_found",
    "register_custom_resources",
    "resource_handler",
]
