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


async def _fetch_user_and_tokens(
    gitea_client: GiteaClient, token: str, context: str = "tools"
) -> set[str] | None:
    """Fetch user info and match active token scopes.

    Args:
        gitea_client: GiteaClient for making API calls.
        token: Raw GITEA_TOKEN value.
        context: Context string for log messages ("tools" or "resources").

    Returns:
        Set of scope strings if successful, None on failure.
    """
    try:
        user_data = await gitea_client.request("GET", "/user")
        _validate_user_data(user_data)
        username = user_data.get("login", "unknown")
        logger.info("User info retrieved", extra={"username": username})
    except Exception:
        logger.exception("Failed to fetch user info for filtering, keeping all %s", context)
        return None

    try:
        tokens_data = await gitea_client.request("GET", f"/users/{username}/tokens")
        if not isinstance(tokens_data, list):
            logger.warning(
                "Unexpected tokens response type, keeping all %s",
                context,
                extra={"type": type(tokens_data).__name__},
            )
            return None
    except Exception:
        logger.exception("Failed to fetch tokens for filtering, keeping all %s", context)
        return None

    available_scopes = _match_active_token(tokens_data, token)
    if available_scopes is None:
        return None

    logger.info("Active token scopes retrieved", extra={"scopes": sorted(available_scopes)})
    return available_scopes


def _make_tool_key(name: str, prefix: str = "") -> str:
    """Build a tool key suitable for mcp.disable(keys=...).

    Args:
        name: The raw (unprefixed) tool name from the provider.
        prefix: Optional prefix to prepend (e.g. "gitea_").

    Returns:
        Key string like "tool:gitea_issue_list@" or "tool:get_version@".
    """
    return f"tool:{prefix}{name}@"


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
    mcp: FastMCP, gitea_client: GiteaClient, token: str | None = None, prefix: str = ""
) -> None:
    """Filter tools based on the current user's Gitea token scopes.

    Disables tools that require a scope not present in the active token using
    FastMCP's ``mcp.disable()`` API.  The active token is identified by matching
    the last 8 chars of it against Gitea's ``token_last_eight`` field.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for making API calls
        token: Raw GITEA_TOKEN value (defaults to gitea_client.config.token)
        prefix: Tool name prefix (e.g. ``"gitea_"``) matching what
            ``GiteaNamespace`` will apply at query time, so that the
            ``Visibility`` transform matches the final tool names.
    """
    if token is None:
        token = gitea_client.config.token

    available_scopes = await _fetch_user_and_tokens(gitea_client, token, "tools")
    if available_scopes is None:
        return

    all_tools = await _collect_provider_tools(mcp)
    if not all_tools:
        logger.warning("No tools found in providers to filter")
        return

    logger.debug(
        "Tools before filtering",
        extra={"total_tools": len(all_tools), "tools": [t.name for t in all_tools][:20]},
    )

    keys_to_disable: set[str] = set()
    for tool in all_tools:
        required = _get_required_scope(tool)
        if not _has_sufficient_scope(required, available_scopes):
            key = _make_tool_key(tool.name, prefix)
            keys_to_disable.add(key)
            logger.info(
                "Tool requires scope not available",
                extra={"tool": tool.name, "key": key, "required": required},
            )

    if keys_to_disable:
        try:
            mcp.disable(keys=keys_to_disable)
            logger.info(
                "Tool filtering completed",
                extra={"disabled_tools": len(keys_to_disable)},
            )
        except Exception as e:
            logger.exception(
                "Failed to disable tools via mcp.disable()",
                extra={"error": str(e)},
            )
    else:
        logger.info("Tool filtering completed — no tools to disable")


async def filter_resources_by_permissions(
    mcp: FastMCP, gitea_client: GiteaClient, token: str | None = None
) -> None:
    """Filter resources based on the current user's Gitea token scopes.

    Disables resources and resource templates that require a scope not present
    in the active token using FastMCP's ``mcp.disable()`` API.  The active token
    is identified by matching the last 8 chars of it against Gitea's
    ``token_last_eight`` field.

    Args:
        mcp: The FastMCP server instance
        gitea_client: GiteaClient for making API calls
        token: Raw GITEA_TOKEN value (defaults to gitea_client.config.token)
    """
    if token is None:
        token = gitea_client.config.token

    available_scopes = await _fetch_user_and_tokens(gitea_client, token, "resources")
    if available_scopes is None:
        return

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

    keys_to_disable: set[str] = set()
    for component in all_components:
        required = _get_required_scope(component)
        if not _has_sufficient_scope(required, available_scopes):
            key = component.key
            keys_to_disable.add(key)
            name = getattr(component, "name", str(component))
            logger.info(
                "Resource requires scope not available",
                extra={"resource": name, "key": key, "required": required},
            )

    if keys_to_disable:
        try:
            mcp.disable(keys=keys_to_disable)
            logger.info(
                "Resource filtering completed",
                extra={"disabled_resources": len(keys_to_disable)},
            )
        except Exception as e:
            logger.exception(
                "Failed to disable resources via mcp.disable()",
                extra={"error": str(e)},
            )
    else:
        logger.info("Resource filtering completed — no resources to disable")
