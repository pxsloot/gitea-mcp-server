"""Hand-written MCP resource implementations.

Custom resources return raw data (JSON or text) with metadata describing the
response schema and a ``format_hint`` for the display layer.  No formatting is
done at the resource level -- that is the responsibility of the unified display
pipeline in ``mcp_tools.py`` and ``tools/display.py``.

**Phase 1 + 2 + 3 migration**: 10 resources have been moved to ``factory.py``
via ``make_api_resource()``, including issues/pulls with optional ``state``
parameters (Phase 2) and readme/files with ``handler_hook`` for base64 decoding
(Phase 3).  The remaining static resources (version, token/scopes, server/info)
still use the legacy ``@_register`` pattern and await migration in the next
architectural phase.
"""

import base64
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.resources import ResourceContent, ResourceResult

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import (
    CACHE_TTL_README,
    CACHE_TTL_RELEASES,
    CACHE_TTL_REPOSITORY,
    CACHE_TTL_USERS,
)
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.factory import make_api_resource
from gitea_mcp_server.resources.scope import has_sufficient_scope, scope_meta

logger = logging.getLogger(__name__)


async def _decode_base64_content(response: Any) -> str:
    """Decode base64 file/readme content from a Gitea ContentsResponse.

    Gitea's ``/repos/{owner}/{repo}/contents/{path}`` endpoint returns a JSON
    object with ``content`` (base64-encoded) and ``encoding`` ("base64") fields.
    This hook extracts and decodes the content for ``text/plain`` resources.

    Handles three response shapes:
    - ``str``: returned as-is (e.g., error messages from the API)
    - ``dict`` with ``encoding="base64"``: ``content`` is base64-decoded
    - ``dict`` without base64 encoding: ``content`` field returned as-is
    - Any other type: converted to ``str()``

    Args:
        response: Raw API response (str, dict, or other).

    Returns:
        Decoded text content.
    """
    if isinstance(response, str):
        return response
    if isinstance(response, dict) and response.get("encoding") == "base64":
        return base64.b64decode(response.get("content") or "").decode("utf-8")
    if isinstance(response, dict):
        return cast("str", response.get("content", ""))
    return str(response)


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

    **Phase 1 + 2 + 3**: 10 resources are registered via ``make_api_resource()``
    (factory pattern with auto schema derivation).  Phases 1-2 cover JSON
    resources; Phase 3 adds ``handler_hook`` support for text/plain resources
    (readme, files) with base64 decoding.  The remaining static resources
    (version, token/scopes, server/info) use the legacy ``@_register`` pattern.

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
    # FACTORY RESOURCES (Phase 3 — text/plain via handler_hook)
    # These use make_api_resource() with handler_hook for base64 decoding
    # of Gitea ContentsResponse JSON into plain text.
    # ======================================================================

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/readme",
        api_path="/repos/{owner}/{repo}/contents/README.md",
        method="GET",
        scope="read:repository",
        cache_ttl=CACHE_TTL_README,
        tags={"wrapper", "readme"},
        error_message="README not found for repository '{owner}/{repo}'.",
        handler_hook=_decode_base64_content,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/files/{path*}",
        api_path="/repos/{owner}/{repo}/contents/{path}",
        method="GET",
        scope="read:repository",
        tags={"wrapper", "files"},
        error_message="File '{path}' not found in repository '{owner}/{repo}'.",
        query_params=["ref"],
        available_scopes=available_scopes,
        handler_hook=_decode_base64_content,
    )

    # ======================================================================
    # STATIC RESOURCES (legacy @_register pattern)
    # These use pre-computed data (version, scopes, server info) and will
    # be migrated to the factory or static resource handling in a future
    # architectural phase.
    # ======================================================================

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
    "_decode_base64_content",
    "register_custom_resources",
]
