"""Hand-written MCP resource implementations.

Custom resources return raw data (JSON or text) with metadata describing the
response schema and a ``format_hint`` for the display layer.  No formatting is
done at the resource level -- that is the responsibility of the unified display
pipeline in ``mcp_tools.py`` and ``tools/display.py``.

**Phase 1 + 2 migration**: 8 resources have been moved to ``factory.py`` via
``make_api_resource()``, including issues and pulls with optional ``state``
parameters via ``query_params`` support.  The remaining resources (readme,
files) still use the legacy ``@_register`` pattern and will be migrated in
Phase 3.
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
from gitea_mcp_server.resources.factory import make_api_resource
from gitea_mcp_server.resources.scope import has_sufficient_scope, scope_meta

logger = logging.getLogger(__name__)


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


def register_custom_resources(  # noqa: PLR0913 -- mcp + client + spec + scopes + pre-computed static data are all independent registration axes
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec | None = None,
    available_scopes: set[str] | None = None,
    version_str: str = "Unknown",
    server_info_md: str | None = None,
) -> None:
    """Register custom-formatted and custom resources.

    Each resource function is defined as a closure that naturally
    captures the pre-computed data it needs, so function signatures
    expose only URI-relevant parameters.
    Uses FastMCP's last-registration-wins ordering.

    The ``version_str``, ``available_scopes``, and ``server_info_md``
    parameters are pre-computed at startup -- the handlers return them
    directly without making API calls on read.

    **Phase 1 + 2**: 8 resources are registered via ``make_api_resource()``
    (factory pattern with auto schema derivation).  The remaining
    ``@_register`` resources (readme, files, static) will be migrated in
    Phase 3.

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: Optional OpenAPI spec for schema derivation.
        available_scopes: Set of scopes the token has, or None (no filtering).
            Resources whose ``required_scope`` is not satisfied are skipped.
            Also used to serve ``gitea://token/scopes`` content.
        version_str: Pre-fetched server version string.
        server_info_md: Pre-built server info markdown, or None.
    """

    def _register(
        uri: str, mime_type: str, tags: set[str], meta: dict[str, Any] | None
    ) -> Callable[
        [Callable[..., Awaitable[ResourceResult]]], Callable[..., Awaitable[ResourceResult]]
    ]:
        # Check scope before registering: skip if the token lacks the
        # required scope for this resource.
        #
        # Note: ``available_scopes`` is derived from the token at startup
        # and is effectively immutable within a server session -- changing
        # scopes requires a new token and a full server restart.  The
        # captured closure is therefore stable for the process lifetime.
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
            # means the decorated function is returned unmodified -- it
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

    # ======================================================================
    # FACTORY RESOURCES (Phase 1)
    # These use ``make_api_resource()`` which auto-derives the response
    # schema and handles str/JSON branching automatically.
    # ======================================================================

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}",
        api_path="/repos/{owner}/{repo}",
        method="GET",
        format_hint="repository",
        scope="read:repository",
        cache_ttl=CACHE_TTL_REPOSITORY,
        tags={"repository"},
        error_message="Repository '{owner}/{repo}' not found.",
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://users/{username}",
        api_path="/users/{username}",
        method="GET",
        format_hint="user",
        scope="read:user",
        cache_ttl=CACHE_TTL_USERS,
        tags={"user"},
        error_message="User '{username}' not found.",
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://user",
        api_path="/user",
        method="GET",
        format_hint="user",
        scope="read:user",
        cache_ttl=CACHE_TTL_USERS,
        tags={"user"},
        error_message="Current user not found or not authenticated.",
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://orgs/{orgname}",
        api_path="/orgs/{orgname}",
        method="GET",
        format_hint="user",
        scope="read:organization",
        cache_ttl=CACHE_TTL_USERS,
        tags={"organization"},
        error_message="Organization '{orgname}' not found.",
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/releases{?draft,q}",
        api_path="/repos/{owner}/{repo}/releases",
        method="GET",
        format_hint="release",
        scope="read:repository",
        cache_ttl=CACHE_TTL_RELEASES,
        tags={"releases"},
        error_message="Repository '{owner}/{repo}' not found or has no releases.",
        query_params=["draft", "q"],
        optional_params=[
            {"name": "draft", "type": "boolean",
             "description": "Filter (exclude/include) drafts"},
            {"name": "q", "type": "string",
             "description": "Search string"},
        ],
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/labels",
        api_path="/repos/{owner}/{repo}/labels",
        method="GET",
        format_hint="labels",
        scope="read:issue",
        tags={"labels"},
        error_message="Labels not found for repository '{owner}/{repo}'.",
        available_scopes=available_scopes,
    )

    # ======================================================================
    # FACTORY RESOURCES (Phase 2 — issues and pulls with optional params)
    # ======================================================================

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/issues{?state}",
        api_path="/repos/{owner}/{repo}/issues",
        method="GET",
        format_hint="issues",
        resource_type="issues",
        scope="read:repository",
        tags={"issues"},
        error_message="Repository '{owner}/{repo}' not found or has no issues.",
        query_params=["state"],
        query_param_validators={"state": ["open", "closed"]},
        optional_params=[{"name": "state", "type": "string", "values": ["open", "closed"]}],
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/pulls{?state}",
        api_path="/repos/{owner}/{repo}/pulls",
        method="GET",
        format_hint="pull_requests",
        resource_type="pulls",
        scope="read:repository",
        tags={"pull_requests"},
        error_message="Repository '{owner}/{repo}' not found or has no pull requests.",
        query_params=["state"],
        query_param_validators={"state": ["open", "closed"]},
        optional_params=[{"name": "state", "type": "string", "values": ["open", "closed"]}],
        available_scopes=available_scopes,
    )

    # ======================================================================
    # NON-MIGRATED RESOURCES (legacy @_register pattern)
    # These will be migrated to the factory in Phase 3.
    # ======================================================================

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

    # ── version ─────────────────────────────────────────────────────────────

    _meta = scope_meta(None)

    @_register("gitea://version", mime_type="text/plain", tags={"wrapper", "server"}, meta=_meta)
    async def get_version() -> ResourceResult:
        """Get server application version."""
        return ResourceResult(contents=[ResourceContent(content=version_str, mime_type="text/plain")])

    # ── token scopes ────────────────────────────────────────────────────────

    _meta = scope_meta("read:user")

    @_register(
        "gitea://token/scopes", mime_type="application/json", tags={"wrapper", "server"}, meta=_meta
    )
    async def get_active_token_scopes() -> ResourceResult:
        """Get the scopes of the active Gitea token.

        Scopes are pre-computed at startup from the same data used for
        scope-based tool filtering -- no API calls are made on read.
        """
        scopes: list[str] | None = sorted(available_scopes) if available_scopes else None
        return ResourceResult(contents=[ResourceContent(
            content=json.dumps({"scopes": scopes}),
            mime_type="application/json",
        )])

    # ── server info (only when pre-built markdown is available) ───────────

    if server_info_md is not None:
        _meta = scope_meta(None)

        @_register(
            "gitea://server/info", mime_type="text/markdown", tags={"wrapper", "server"}, meta=_meta
        )
        async def get_server_info() -> ResourceResult:
            """Get server metadata from OpenAPI info block."""
            return ResourceResult(contents=[ResourceContent(
                content=server_info_md,
                mime_type="text/markdown",
            )])


__all__ = [
    "_handle_not_found",
    "register_custom_resources",
    "resource_handler",
]
