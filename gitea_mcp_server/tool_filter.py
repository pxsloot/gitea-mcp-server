"""Tool permission filtering for Gitea MCP Server."""

import logging
from typing import Any, cast

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)


def _validate_user_data(data: Any) -> None:
    """Validate user data is a dict."""
    if not isinstance(data, dict):
        msg = f"Unexpected user data type: {type(data)}"
        raise TypeError(msg) from None


def _get_required_scope(tool: Any) -> str | None:
    """Get the required Gitea token scope from a tool's metadata.

    Args:
        tool: Tool object with meta containing 'required_scope'.

    Returns:
        Scope string (e.g. "read:repository", "sudo"), or None if no scope needed.
    """
    try:
        return cast("str | None", tool.meta["fastmcp"]["_internal"]["required_scope"])
    except (KeyError, TypeError, AttributeError):
        return None


def _has_sufficient_scope(required: str | None, available: set[str]) -> bool:
    """Check if available Gitea token scopes satisfy a required scope.

    Rules:
    - None required (no scope needed) always passes.
    - ``sudo`` in available grants everything.
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
    if required in available:
        return True
    if required.startswith("read:"):
        resource = required.split(":", 1)[1]
        if f"write:{resource}" in available:
            return True
    return False


def _match_active_token(tokens_data: list[dict], raw_token: str) -> set[str] | None:
    """Match the active token and return its scopes.

    Args:
        tokens_data: List of token dicts from the API.
        raw_token: The raw GITEA_TOKEN value from config.

    Returns:
        Set of scope strings for the matched token, or None if no match.
    """
    last_eight = raw_token[-8:]
    for token in tokens_data:
        logt = token.get("token_last_eight")
        logger.debug("Testing token match", extra={"token_last_eight": logt})
        if isinstance(token, dict) and token.get("token_last_eight") == last_eight:
            scopes = token.get("scopes")
            if scopes and isinstance(scopes, list):
                return set(scopes)
    logger.warning(
        "No token matched the active GITEA_TOKEN last 8 chars, keeping all tools",
        extra={"token_last_eight": last_eight},
    )
    return None


def _hide_tool(tool: Any) -> None:
    """Set a tool's visibility to False."""
    if tool.meta is None:
        tool.meta = {}
    if "fastmcp" not in tool.meta:
        tool.meta["fastmcp"] = {}
    if "_internal" not in tool.meta["fastmcp"]:
        tool.meta["fastmcp"]["_internal"] = {}
    tool.meta["fastmcp"]["_internal"]["visibility"] = False


def _hide_resource(resource: Any) -> None:
    """Set a resource's or template's visibility to False."""
    if resource.meta is None:
        resource.meta = {}
    if "fastmcp" not in resource.meta:
        resource.meta["fastmcp"] = {}
    if "_internal" not in resource.meta["fastmcp"]:
        resource.meta["fastmcp"]["_internal"] = {}
    resource.meta["fastmcp"]["_internal"]["visibility"] = False


async def _collect_provider_tools(mcp: FastMCP) -> list[Any]:
    """Gather all tools from all providers."""
    all_tools = []
    for provider in getattr(mcp, "providers", []):
        try:
            provider_tools = await provider.list_tools()
            all_tools.extend(provider_tools)
        except (AttributeError, TypeError) as e:
            logger.warning(
                "Failed to list tools from provider, skipping",
                extra={"provider": type(provider).__name__, "error": str(e)},
            )
    return all_tools


async def _collect_provider_resources(mcp: FastMCP) -> list[Any]:
    """Gather all resources and resource templates from all providers."""
    all_components: list[Any] = []
    for provider in getattr(mcp, "providers", []):
        try:
            provider_resources = await provider.list_resources()
            all_components.extend(provider_resources)
        except (AttributeError, TypeError) as e:
            logger.warning(
                "Failed to list resources from provider, skipping",
                extra={"provider": type(provider).__name__, "error": str(e)},
            )
        try:
            provider_templates = await provider.list_resource_templates()
            all_components.extend(provider_templates)
        except (AttributeError, TypeError) as e:
            logger.warning(
                "Failed to list resource templates from provider, skipping",
                extra={"provider": type(provider).__name__, "error": str(e)},
            )
    return all_components


async def filter_tools_by_permissions(
    mcp: FastMCP, gitea_client: GiteaClient, token: str | None = None
) -> None:
    """Filter tools based on the current user's Gitea token scopes.

    Removes tools that require a scope not present in the active token.
    The active token is identified by matching the last 8 chars of it
    against Gitea's ``token_last_eight`` field.
    This function should be called before any list_tools request to avoid
    caching of unfiltered tools.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for making API calls
        token: Raw GITEA_TOKEN value (defaults to gitea_client.config.token)
    """
    if token is None:
        token = gitea_client.config.token

    logger.info("Starting tool permission filtering")

    # Fetch current user info
    try:
        user_data = await gitea_client.request("GET", "/user")
        _validate_user_data(user_data)
        username = user_data.get("login", "unknown")
        logger.info("User info retrieved", extra={"username": username})
    except Exception:
        logger.exception("Failed to fetch user info for filtering, keeping all tools")
        return

    # Fetch user's token scopes
    try:
        tokens_data = await gitea_client.request("GET", f"/users/{username}/tokens")
        if not isinstance(tokens_data, list):
            logger.warning(
                "Unexpected tokens response type, keeping all tools",
                extra={"type": type(tokens_data).__name__},
            )
            return
    except Exception:
        logger.exception("Failed to fetch tokens for filtering, keeping all tools")
        return

    # Match the active token and get its scopes only
    available_scopes = _match_active_token(tokens_data, token)
    if available_scopes is None:
        return

    logger.info("Active token scopes retrieved", extra={"scopes": sorted(available_scopes)})

    all_tools = await _collect_provider_tools(mcp)
    if not all_tools:
        logger.warning("No tools found in providers to filter")
        return

    logger.debug(
        "Tools before filtering",
        extra={"total_tools": len(all_tools), "tools": [t.name for t in all_tools][:20]},
    )

    disabled_count = 0
    for tool in all_tools:
        required = _get_required_scope(tool)
        if not _has_sufficient_scope(required, available_scopes):
            try:
                _hide_tool(tool)
                disabled_count += 1
                logger.info("Disabled tool due to insufficient scope", extra={"tool": tool.name, "key": tool.key})
            except Exception as e:
                logger.exception("Failed to disable tool", extra={"tool": tool.name, "key": tool.key, "error": str(e)})

    logger.info(
        "Tool filtering completed",
        extra={
            "total_tools": len(all_tools),
            "disabled_tools": disabled_count,
            "remaining_tools": len(all_tools) - disabled_count,
        },
    )


async def filter_resources_by_permissions(
    mcp: FastMCP, gitea_client: GiteaClient, token: str | None = None
) -> None:
    """Filter resources based on the current user's Gitea token scopes.

    Hides resources and resource templates that require a scope not present
    in the active token. The active token is identified by matching the last
    8 chars of it against Gitea's ``token_last_eight`` field.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for making API calls
        token: Raw GITEA_TOKEN value (defaults to gitea_client.config.token)
    """
    if token is None:
        token = gitea_client.config.token

    logger.info("Starting resource permission filtering")

    # Fetch current user info
    try:
        user_data = await gitea_client.request("GET", "/user")
        _validate_user_data(user_data)
        username = user_data.get("login", "unknown")
        logger.info("User info retrieved", extra={"username": username})
    except Exception:
        logger.exception("Failed to fetch user info for filtering, keeping all resources")
        return

    # Fetch user's token scopes
    try:
        tokens_data = await gitea_client.request("GET", f"/users/{username}/tokens")
        if not isinstance(tokens_data, list):
            logger.warning(
                "Unexpected tokens response type, keeping all resources",
                extra={"type": type(tokens_data).__name__},
            )
            return
    except Exception:
        logger.exception("Failed to fetch tokens for filtering, keeping all resources")
        return

    # Match the active token and get its scopes only
    available_scopes = _match_active_token(tokens_data, token)
    if available_scopes is None:
        return

    logger.info("Active token scopes retrieved", extra={"scopes": sorted(available_scopes)})

    all_components = await _collect_provider_resources(mcp)
    if not all_components:
        logger.warning("No resources found in providers to filter")
        return

    logger.debug(
        "Resources before filtering",
        extra={
            "total_resources": len(all_components),
            "resources": [getattr(c, "name", str(c)) for c in all_components][:20],
        },
    )

    disabled_count = 0
    for component in all_components:
        required = _get_required_scope(component)
        if not _has_sufficient_scope(required, available_scopes):
            try:
                _hide_resource(component)
                disabled_count += 1
                name = getattr(component, "name", str(component))
                logger.info("Disabled resource due to insufficient scope", extra={"resource": name})
            except Exception as e:
                logger.exception(
                    "Failed to disable resource",
                    extra={"resource": name, "error": str(e)},
                )

    logger.info(
        "Resource filtering completed",
        extra={
            "total_resources": len(all_components),
            "disabled_resources": disabled_count,
            "remaining_resources": len(all_components) - disabled_count,
        },
    )
