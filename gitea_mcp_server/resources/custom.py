"""Hand-written MCP resource implementations.

Custom resources return raw data (JSON or text) with metadata describing the
response schema and a ``format_hint`` for the display layer.  No formatting is
done at the resource level -- that is the responsibility of the unified display
pipeline in ``tools/mcp_tools.py`` and ``tools/display.py``.

**Migration complete**: 10 resources are registered via the factory
(``make_api_resource()``).  The remaining 3 static resources (version,
token/scopes, server/info) use direct ``mcp.resource()`` calls with inline
scope guarding -- the legacy ``@_register`` decorator has been removed.

**Naming**: every resource name is snake_case.  Factory resources derive
their name from the spec operationId (or declare an explicit name when the
``api_path`` does not match a spec path); static resources declare explicit
snake_case names.  No resource may surface FastMCP's function-name fallback
(``"handler"``).
"""

import json
import logging

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
from gitea_mcp_server.resources.factory import (
    ResourceParamConfig,
    make_api_resource,
)
from gitea_mcp_server.resources.meta import ResourceMeta
from gitea_mcp_server.resources.scope import has_sufficient_scope

logger = logging.getLogger(__name__)


def register_custom_resources(  # noqa: PLR0913 -- mcp + client + spec + scopes + pre-computed static data are all independent registration axes
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec | None = None,
    available_scopes: set[str] | None = None,
    version_str: str = "Unknown",
    server_info_md: str | None = None,
) -> set[str]:
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

    Returns:
        The set of URIs registered via ``make_api_resource()``.
        The caller passes this as ``skip_uris`` to
        ``register_auto_generated_resources()`` so it skips URIs already
        handled by custom resources.
    """

    # Track factory-registered URIs for auto-generation skip.
    registered_uris: set[str] = set()

    # ======================================================================
    # FACTORY RESOURCES
    # These use ``make_api_resource()`` which auto-derives the response
    # schema and handles str/JSON branching automatically.
    # ======================================================================

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://repos/{owner}/{repo}",
        api_path="/repos/{owner}/{repo}",
        method="GET",
        format_hint="repository",
        scope="read:repository",
        cache_ttl=CACHE_TTL_REPOSITORY,
        tags={"wrapper", "repository"},
        error_message="Repository '{owner}/{repo}' not found.",
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://users/{username}",
        api_path="/users/{username}",
        method="GET",
        format_hint="user",
        scope="read:user",
        cache_ttl=CACHE_TTL_USERS,
        tags={"wrapper", "user"},
        error_message="User '{username}' not found.",
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://user",
        api_path="/user",
        method="GET",
        format_hint="user",
        scope="read:user",
        cache_ttl=CACHE_TTL_USERS,
        tags={"wrapper", "user"},
        error_message="Current user not found or not authenticated.",
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://orgs/{orgname}",
        api_path="/orgs/{orgname}",
        method="GET",
        name="org_get",
        description="Get an organization by name",
        format_hint="user",
        scope="read:organization",
        cache_ttl=CACHE_TTL_USERS,
        size_hint="medium",
        tags={"wrapper", "organization"},
        error_message="Organization '{orgname}' not found.",
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
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
                {
                    "name": "draft",
                    "type": "boolean",
                    "description": "Filter (exclude/include) drafts",
                },
                {"name": "q", "type": "string", "description": "Search string"},
            ],
        ),
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
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
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    # ======================================================================
    # FACTORY RESOURCES (with optional query and context params)
    # ======================================================================

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://repos/{owner}/{repo}/issues{?state,type}",
        api_path="/repos/{owner}/{repo}/issues",
        method="GET",
        format_hint="issues",
        resource_type="issues",
        scope="read:issue",
        tags={"wrapper", "issues"},
        error_message="Repository '{owner}/{repo}' not found.",
        param_config=ResourceParamConfig(
            query_params=["state", "type"],
            query_param_validators={"state": ["open", "closed"], "type": ["issues", "pulls"]},
            optional_params=[
                {"name": "state", "type": "string", "values": ["open", "closed"]},
                {
                    "name": "type",
                    "type": "string",
                    "values": ["issues", "pulls"],
                    "description": "Filter by type (issues / pulls)",
                },
            ],
            context_meta_keys=["type"],
        ),
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://repos/{owner}/{repo}/pulls{?state}",
        api_path="/repos/{owner}/{repo}/pulls",
        method="GET",
        format_hint="pull_requests",
        resource_type="pulls",
        scope="read:issue",
        tags={"wrapper", "pull_requests"},
        error_message="Repository '{owner}/{repo}' not found or has no pull requests.",
        param_config=ResourceParamConfig(
            query_params=["state"],
            query_param_validators={"state": ["open", "closed"]},
            optional_params=[{"name": "state", "type": "string", "values": ["open", "closed"]}],
        ),
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    # ======================================================================
    # FACTORY RESOURCES
    # Base64 decoding of ContentsResponse JSON is handled by the
    # read_resource tool (mcp_tools.py:_read_resource_tool), not at
    # resource level.  Resources return raw API data; the tool layer
    # decodes, formats, and presents.
    # ======================================================================

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://repos/{owner}/{repo}/readme",
        api_path="/repos/{owner}/{repo}/contents/README.md",
        method="GET",
        name="repo_get_readme",
        description="Get a repository's README",
        scope="read:repository",
        cache_ttl=CACHE_TTL_README,
        size_hint="small",
        tags={"wrapper", "readme"},
        error_message="README not found for repository '{owner}/{repo}'.",
        param_config=ResourceParamConfig(
            query_params=["ref"],
            optional_params=[
                {
                    "name": "ref",
                    "type": "string",
                    "description": "The name of the commit/branch/tag",
                }
            ],
        ),
        tracking_set=registered_uris,
        available_scopes=available_scopes,
    )

    make_api_resource(
        mcp,
        gitea_client,
        openapi_spec,
        uri="gitea://repos/{owner}/{repo}/files/{path*}",
        api_path="/repos/{owner}/{repo}/contents/{path}",
        method="GET",
        name="repo_get_contents",
        description="Get a file from a repository",
        scope="read:repository",
        size_hint="small",
        tags={"wrapper", "files"},
        error_message="File '{path}' not found in repository '{owner}/{repo}'.",
        param_config=ResourceParamConfig(
            query_params=["ref"],
            optional_params=[
                {
                    "name": "ref",
                    "type": "string",
                    "description": "The name of the commit/branch/tag",
                }
            ],
        ),
        tracking_set=registered_uris,
        available_scopes=available_scopes,
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
        return ResourceResult(
            contents=[ResourceContent(content=version_str, mime_type="text/plain")]
        )

    mcp.resource(
        "gitea://version",
        name="version",
        mime_type="text/plain",
        tags={"wrapper", "server"},
        meta=ResourceMeta(required_scope=None, size_hint="tiny", default_detail="full").to_dict(),
    )(get_version)

    # ── token scopes ────────────────────────────────────────────────────────

    async def get_active_token_scopes() -> ResourceResult:
        """Get the scopes of the active Gitea token.

        Scopes are pre-computed at startup from the same data used for
        scope-based tool filtering -- no API calls are made on read.
        """
        scopes: list[str] | None = sorted(available_scopes) if available_scopes else None
        return ResourceResult(
            contents=[
                ResourceContent(
                    content=json.dumps({"scopes": scopes}),
                    mime_type="application/json",
                )
            ]
        )

    _meta_scopes = ResourceMeta(
        required_scope="read:user", size_hint="tiny", default_detail="full"
    ).to_dict()
    if available_scopes is None or has_sufficient_scope("read:user", available_scopes):
        mcp.resource(
            "gitea://token/scopes",
            name="token_scopes",
            mime_type="application/json",
            tags={"wrapper", "server"},
            meta=_meta_scopes,
        )(get_active_token_scopes)
    else:
        logger.debug("Skipping gitea://token/scopes: requires read:user")

    # ── server info (only when pre-built markdown is available) ───────────

    if server_info_md is not None:

        async def get_server_info() -> ResourceResult:
            """Get server metadata from OpenAPI info block."""
            return ResourceResult(
                contents=[
                    ResourceContent(
                        content=server_info_md,
                        mime_type="text/markdown",
                    )
                ]
            )

        mcp.resource(
            "gitea://server/info",
            name="server_info",
            mime_type="text/markdown",
            tags={"wrapper", "server"},
            meta=ResourceMeta(
                required_scope=None, size_hint="small", default_detail="full"
            ).to_dict(),
        )(get_server_info)

    # Return the URIs registered via make_api_resource()
    # so the orchestrator can skip them during auto-generation.
    return registered_uris


__all__ = [
    "register_custom_resources",
]
