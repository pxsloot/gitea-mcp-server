"""Hand-written MCP resource implementations.

Custom resources return raw data (JSON or text) with metadata describing the
response schema and a ``format_hint`` for the display layer.  No formatting is
done at the resource level -- that is the responsibility of the unified display
pipeline in ``mcp_tools.py`` and ``tools/display.py``.

**Migration complete**: 10 resources are registered via the factory
(``make_api_resource()``).  The remaining 3 static resources (version,
token/scopes, server/info) use direct ``mcp.resource()`` calls with inline
scope guarding -- the legacy ``@_register`` decorator has been removed.
"""

import base64
import json
import logging
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
from gitea_mcp_server.resources.factory import ResourceParamConfig, make_api_resource
from gitea_mcp_server.resources.meta import ResourceMeta
from gitea_mcp_server.resources.scope import has_sufficient_scope

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

    **Factory + static**: 10 resources are registered via ``make_api_resource()``
    (factory pattern with auto schema derivation).  The remaining 3 static
    resources (version, token/scopes, server/info) are registered directly
    with ``mcp.resource()`` and inline scope guarding.

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

    # ======================================================================
    # FACTORY RESOURCES
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
        tags={"wrapper", "repository"},
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
        tags={"wrapper", "user"},
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
        tags={"wrapper", "user"},
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
        tags={"wrapper", "organization"},
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
        tags={"wrapper", "releases"},
        error_message="Repository '{owner}/{repo}' not found or has no releases.",
        param_config=ResourceParamConfig(
            query_params=["draft", "q"],
            optional_params=[
                {"name": "draft", "type": "boolean",
                 "description": "Filter (exclude/include) drafts"},
                {"name": "q", "type": "string",
                 "description": "Search string"},
            ],
        ),
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/labels",
        api_path="/repos/{owner}/{repo}/labels",
        method="GET",
        format_hint="labels",
        scope="read:issue",
        tags={"wrapper", "labels"},
        error_message="Labels not found for repository '{owner}/{repo}'.",
        param_config=ResourceParamConfig(
            context_meta_keys=["owner", "repo"],
        ),
        available_scopes=available_scopes,
    )

    # ======================================================================
    # FACTORY RESOURCES (with optional query and context params)
    # ======================================================================

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/issues{?state,type}",
        api_path="/repos/{owner}/{repo}/issues",
        method="GET",
        format_hint="issues",
        resource_type="issues",
        scope="read:repository",
        tags={"wrapper", "issues"},
        error_message="Repository '{owner}/{repo}' not found or has no issues.",
        param_config=ResourceParamConfig(
            query_params=["state", "type"],
            query_param_validators={"state": ["open", "closed"], "type": ["issues", "pulls"]},
            optional_params=[
                {"name": "state", "type": "string", "values": ["open", "closed"]},
                {"name": "type", "type": "string", "values": ["issues", "pulls"],
                 "description": "Filter by type (issues / pulls)"},
            ],
            context_meta_keys=["type"],
        ),
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
        tags={"wrapper", "pull_requests"},
        error_message="Repository '{owner}/{repo}' not found or has no pull requests.",
        param_config=ResourceParamConfig(
            query_params=["state"],
            query_param_validators={"state": ["open", "closed"]},
            optional_params=[{"name": "state", "type": "string", "values": ["open", "closed"]}],
        ),
        available_scopes=available_scopes,
    )

    # ======================================================================
    # FACTORY RESOURCES (text/plain via handler_hook)
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
        param_config=ResourceParamConfig(
            query_params=["ref"],
            optional_params=[{"name": "ref", "type": "string",
                              "description": "The name of the commit/branch/tag"}],
        ),
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
        param_config=ResourceParamConfig(
            query_params=["ref"],
            optional_params=[{"name": "ref", "type": "string",
                              "description": "The name of the commit/branch/tag"}],
        ),
        available_scopes=available_scopes,
        handler_hook=_decode_base64_content,
    )

    # ======================================================================
    # STATIC RESOURCES
    # These use pre-computed data (version, scopes, server info) and are
    # registered directly with mcp.resource() — no factory needed since
    # they don't call the Gitea API.
    # ======================================================================

    # ── version ─────────────────────────────────────────────────────────────

    async def get_version() -> ResourceResult:
        """Get server application version."""
        return ResourceResult(contents=[ResourceContent(content=version_str, mime_type="text/plain")])

    mcp.resource(
        "gitea://version", mime_type="text/plain", tags={"wrapper", "server"},
        meta=ResourceMeta(required_scope=None, size_hint="tiny", default_detail="full").to_dict(),
    )(get_version)

    # ── token scopes ────────────────────────────────────────────────────────

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

    _meta_scopes = ResourceMeta(required_scope="read:user", size_hint="tiny", default_detail="full").to_dict()
    if available_scopes is None or has_sufficient_scope("read:user", available_scopes):
        mcp.resource(
            "gitea://token/scopes", mime_type="application/json",
            tags={"wrapper", "server"}, meta=_meta_scopes,
        )(get_active_token_scopes)
    else:
        logger.debug("Skipping gitea://token/scopes: requires read:user")

    # ── server info (only when pre-built markdown is available) ───────────

    if server_info_md is not None:
        async def get_server_info() -> ResourceResult:
            """Get server metadata from OpenAPI info block."""
            return ResourceResult(contents=[ResourceContent(
                content=server_info_md,
                mime_type="text/markdown",
            )])

        mcp.resource(
            "gitea://server/info", mime_type="text/markdown",
            tags={"wrapper", "server"},
            meta=ResourceMeta(required_scope=None, size_hint="small", default_detail="full").to_dict(),
        )(get_server_info)


__all__ = [
    "_decode_base64_content",
    "register_custom_resources",
]
