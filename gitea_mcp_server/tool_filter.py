"""Tool and resource permission filtering via Transform.

Converts the old ``filter_tools_by_permissions()`` / ``filter_resources_by_permissions()``
post-hoc pattern (which used ``mcp.disable()``) into a proper FastMCP ``Transform``
that filters at query time, consistent with the other transforms (exclusion, namespace, etc.).

Usage in ``server.py``::

    from gitea_mcp_server.tool_filter import PermissionFilterTransform, fetch_token_scopes

    available_scopes = await fetch_token_scopes(gitea_client, config.token)
    if available_scopes is not None:
        mcp.add_transform(PermissionFilterTransform(available_scopes, prefix=config.tool_prefix))
"""

import logging
from collections.abc import Sequence
from typing import Any, cast

from fastmcp.resources import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.transforms import (
    GetResourceNext,
    GetResourceTemplateNext,
    GetToolNext,
    Transform,
)
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec

from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)


def _validate_user_data(data: Any) -> None:
    """Validate user data is a dict."""
    if not isinstance(data, dict):
        msg = f"Unexpected user data type: {type(data)}"
        raise TypeError(msg) from None


def _get_required_scope(item: Any) -> str | None:
    """Get the required Gitea token scope from a tool/resource's metadata.

    Args:
        item: Tool or Resource object with meta containing 'required_scope'.

    Returns:
        Scope string (e.g. "read:repository", "sudo"), or None if no scope needed.
    """
    try:
        return cast("str | None", item.meta["required_scope"])
    except (KeyError, TypeError, AttributeError):
        return None


def _has_sufficient_scope(required: str | None, available: set[str]) -> bool:
    """Check if available Gitea token scopes satisfy a required scope.

    Rules:
    - None required (no scope needed) always passes.
    - ``sudo`` in available grants everything.
    - ``all`` in available grants everything (Gitea's "full access" shortcut,
      returned by the API as the literal scope ``"all"``; the UI displays it as
      ``[all]``).
    - Exact match passes.
    - ``write:xxx`` implies ``read:xxx``.

    Args:
        required: Required scope string or None.
        available: Set of scope strings the user's token possesses.

    Returns:
        True if the required scope is covered by available scopes.
    """
    if required is None:
        return True
    if "sudo" in available:
        return True
    if "all" in available:
        return True
    if required in available:
        return True
    if required.startswith("read:"):
        resource = required.split(":", 1)[1]
        if f"write:{resource}" in available:
            return True
    return False


def _match_active_token(tokens_data: list[Any], raw_token: str) -> set[str] | None:
    """Match the active token and return its scopes.

    Args:
        tokens_data: List of token entries from the API. Entries are expected
            to be dicts but may be malformed (non-dict); those are skipped.
        raw_token: The raw GITEA_TOKEN value from config.

    Returns:
        Set of scope strings for the matched token, or None if no match.
    """
    last_eight = raw_token[-8:]
    for token in tokens_data:
        if not isinstance(token, dict):
            logger.debug(
                "Skipping non-dict token entry", extra={"type": type(token).__name__}
            )
            continue
        logt = token.get("token_last_eight")
        logger.debug("Testing token match", extra={"token_last_eight": logt})
        if token.get("token_last_eight") == last_eight:
            scopes = token.get("scopes")
            if scopes and isinstance(scopes, list):
                return set(scopes)
    logger.warning(
        "No token matched the active GITEA_TOKEN last 8 chars, keeping all tools",
        extra={"token_last_eight": last_eight},
    )
    return None


async def fetch_token_scopes(gitea_client: GiteaClient, token: str) -> set[str] | None:
    """Fetch user info and match active token scopes.

    Args:
        gitea_client: GiteaClient for making API calls.
        token: Raw GITEA_TOKEN value.

    Returns:
        Set of scope strings if successful, None on failure.
    """
    try:
        user_data = await gitea_client.request("GET", "/user")
        _validate_user_data(user_data)
        username = user_data.get("login", "unknown")
        logger.info("User info retrieved", extra={"username": username})
    except Exception:
        logger.exception("Failed to fetch user info for filtering, keeping all tools/resources")
        return None

    try:
        tokens_data = await gitea_client.request("GET", f"/users/{username}/tokens")
        if not isinstance(tokens_data, list):
            logger.warning(
                "Unexpected tokens response type, keeping all tools/resources",
                extra={"type": type(tokens_data).__name__},
            )
            return None
    except Exception:
        logger.exception("Failed to fetch tokens for filtering, keeping all tools/resources")
        return None

    available_scopes = _match_active_token(tokens_data, token)
    if available_scopes is None:
        return None

    logger.info("Active token scopes retrieved", extra={"scopes": sorted(available_scopes)})
    return available_scopes


class PermissionFilterTransform(Transform):
    """FastMCP Transform that filters tools/resources by Gitea token scopes.

    Replaces the old ``mcp.disable()`` post-hoc pattern with a proper Transform
    that intercepts ``list_tools`` / ``get_tool`` / ``list_resources`` and filters
    out items whose required scope is not present in the user's token.

    The ``available_scopes`` set must be fetched **before** constructing this
    transform (via ``fetch_token_scopes()``).  The transform itself is stateless
    once constructed.
    """

    def __init__(self, available_scopes: set[str]) -> None:
        """Initialise the transform.

        Args:
            available_scopes: Set of Gitea scope strings the user's token possesses.
        """
        super().__init__()
        self._available = available_scopes

    def _is_allowed(self, item: Any) -> bool:
        """Check whether an item (tool or resource) is allowed by the token scope."""
        required = _get_required_scope(item)
        allowed = _has_sufficient_scope(required, self._available)
        if not allowed:
            name = getattr(item, "name", str(item))
            logger.info(
                "Item requires scope not available, filtering out",
                extra={"item": name, "required": required, "available": sorted(self._available)},
            )
        return allowed

    # ── tools ──────────────────────────────────────────────────────────

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Filter out tools whose required scope is not available."""
        return [t for t in tools if self._is_allowed(t)]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        """Return the tool only if its required scope is available."""
        tool = await call_next(name, version=version)
        if tool is not None and not self._is_allowed(tool):
            return None
        return tool

    # ── resources ──────────────────────────────────────────────────────

    async def list_resources(self, resources: Sequence[Resource]) -> Sequence[Resource]:
        """Filter out resources whose required scope is not available."""
        return [r for r in resources if self._is_allowed(r)]

    async def list_resource_templates(
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        """Filter out resource templates whose required scope is not available."""
        return [t for t in templates if self._is_allowed(t)]

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        """Return the resource only if its required scope is available."""
        resource = await call_next(uri, version=version)
        if resource is not None and not self._is_allowed(resource):
            return None
        return resource

    async def get_resource_template(
        self,
        uri: str,
        call_next: GetResourceTemplateNext,
        *,
        version: VersionSpec | None = None,
    ) -> ResourceTemplate | None:
        """Return the resource template only if its required scope is available."""
        template = await call_next(uri, version=version)
        if template is not None and not self._is_allowed(template):
            return None
        return template
