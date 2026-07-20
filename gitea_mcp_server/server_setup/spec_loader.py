"""OpenAPI spec loading and conversion utilities.

``load_and_convert_spec`` is the single place where tool and resource
visibility is decided. It fetches token scopes and the exclusion config,
then computes both:

* ``filtered_tools_info`` — the ``x-mcp-filtered-tools`` prediction data used by
  the ``FilteredToolMiddleware`` and ``tool_info`` to give agents rich,
  actionable error messages, and by resource registration to skip filtered
  operations.
* ``excluded_routes`` — the set of ``(path, UPPER_METHOD)`` tuples that must
  never reach FastMCP.  This is passed to ``create_openapi_provider`` and
  applied via ``route_map_fn``, so filtered operations are excluded *before*
  FastMCP builds the tool.

Both are derived from the same ``compute_filtered_tools_info`` logic, so the
visibility decision and the error-message data can never diverge.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from gitea_mcp_server.constants import HTTP_METHODS_ALL
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3
from gitea_mcp_server.server_setup.mcp_extensions import apply_mcp_extensions, load_mcp_extensions
from gitea_mcp_server.tools.filter_info import compute_filtered_tools_info

if TYPE_CHECKING:
    from gitea_mcp_server.client import GiteaClient
    from gitea_mcp_server.config import Config
    from gitea_mcp_server.openapi_types import OpenAPISpec, SwaggerV2Spec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exclusion config loading
# ---------------------------------------------------------------------------


def load_exclusion_config(config_path: str | None) -> dict[str, list[str]]:
    """Load exclusion rules from a YAML config file.

    Args:
        config_path: Path to the YAML config file, or None to skip.

    Returns:
        Dict with ``exclude`` and ``include`` keys, each a list of pattern strings.
        Returns empty lists on any error or missing file.
    """
    if config_path is None:
        return {"exclude": [], "include": []}

    path = Path(config_path)
    if not path.exists():
        logger.info("Exclusion config not found: %s", config_path)
        return {"exclude": [], "include": []}

    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return {
            "exclude": data.get("exclude", []) or [],
            "include": data.get("include", []) or [],
        }
    except Exception:
        logger.exception("Failed to load exclusion config from %s", config_path)
        return {"exclude": [], "include": []}


# ---------------------------------------------------------------------------
# Token scope fetching
# ---------------------------------------------------------------------------


def _validate_user_data(data: Any) -> None:
    """Validate user data is a dict."""
    if not isinstance(data, dict):
        msg = f"Unexpected user data type: {type(data)}"
        raise TypeError(msg) from None


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
        Set of scope strings if successful, None on failure (fail-open).
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


# ---------------------------------------------------------------------------
# Spec loading / conversion
# ---------------------------------------------------------------------------


async def load_openapi_spec(gitea_client: GiteaClient, config: Config) -> dict[str, Any]:
    """Load OpenAPI spec from Gitea instance.

    Args:
        gitea_client: Client to use for fetching the spec
        config: Application configuration

    Returns:
        OpenAPI spec (Swagger 2.0 format) as dictionary

    Raises:
        SpecError: If spec cannot be loaded or parsed
    """
    # Construct URL: base_url without /api/v1 + /swagger.v1.json
    spec_url = f"{config.url}/swagger.v1.json"

    logger.info("Loading OpenAPI spec from %s", spec_url)

    try:
        remote_spec = await gitea_client.request("GET", spec_url)
        # If request returned a string (unlikely for JSON), parse it
        if isinstance(remote_spec, str):
            remote_spec = json.loads(remote_spec)
        logger.info(
            "Spec loaded",
            extra={
                "spec_version": remote_spec.get("swagger"),
                "paths_count": len(remote_spec.get("paths", {})),
            },
        )
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in spec from {spec_url}: {e}"
        raise SpecError(msg) from e
    except Exception as e:
        msg = f"Failed to fetch or parse spec from {spec_url}: {e}"
        raise SpecError(msg) from e
    else:
        return cast("dict[str, Any]", remote_spec)


async def load_and_convert_spec(
    gitea_client: GiteaClient, config: Config
) -> tuple[OpenAPISpec, dict[str, Any], dict[str, Any], set[tuple[str, str]]]:
    """Load Swagger spec, convert to OpenAPI v3, compute spec-level filtering.

    The raw spec is cast to ``SwaggerV2Spec`` before conversion, and the
    result is cast to ``OpenAPISpec`` after conversion.

    Tool visibility is decided here, once, and returned as ``excluded_routes``
    so the OpenAPI provider can drop filtered operations via ``route_map_fn``
    before FastMCP ever sees them.

    Args:
        gitea_client: GiteaClient for fetching the spec
        config: Application configuration

    Returns:
        Tuple of (openapi_v3_spec, extensions_dict, filtered_tools_info,
        excluded_routes).  ``extensions_dict`` is the raw YAML content (may be
        empty).  ``filtered_tools_info`` is the filter-prediction data used by
        synthetic tools for rich error messages (may be empty dict).
        ``excluded_routes`` is a set of ``(path, UPPER_METHOD)`` tuples that
        must be excluded from the provider.

    Raises:
        SpecError: If spec loading or conversion fails
    """
    try:
        spec = await load_openapi_spec(gitea_client, config)
    except SpecError:
        raise
    except Exception as e:
        msg = f"Failed to load OpenAPI spec: {e}"
        raise SpecError(msg) from e

    try:
        raw_spec = convert_swagger_to_openapi_v3(cast("SwaggerV2Spec", spec))
        openapi_spec: OpenAPISpec = cast("OpenAPISpec", raw_spec)
    except Exception as e:
        msg = f"Failed to convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    extensions: dict[str, Any] = {}
    try:
        extensions = load_mcp_extensions()
        if extensions:
            apply_mcp_extensions(openapi_spec, extensions)
    except (OSError, KeyError, ValueError, RuntimeError) as e:
        logger.warning(
            "Failed to apply MCP extensions, proceeding without customizations",
            extra={"error": str(e)},
        )

    # ── Compute spec-level filtering ───────────────────────────────────
    # Exclusion config is always honoured (it never required token scopes).
    # Token scopes are only fetched when scope filtering is enabled — this
    # avoids a network round-trip (and a hard dependency on the /user +
    # /users/{name}/tokens endpoints) when scope filtering is off, while
    # still applying deprecated-endpoint and config-exclusion filtering.
    # The result drives BOTH:
    #   * ``filtered_tools_info`` — rich error messages for synthetic tools
    #   * ``excluded_routes``     — operations dropped before provider creation
    # Both derive from the same ``compute_filtered_tools_info`` logic so the
    # visible tool set and the error messages can never disagree.
    try:
        exclusion_config = load_exclusion_config(getattr(config, "exclude_config_path", None))
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load exclusion config, proceeding without it")
        exclusion_config = {"exclude": [], "include": []}

    available_scopes = None
    if config.tool_filtering_enabled:
        try:
            available_scopes = await fetch_token_scopes(gitea_client, config.token)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to fetch token scopes for filtering info, proceeding without")
            available_scopes = None

    tool_prefix = config.tool_prefix or ""
    filtered_tools_info = compute_filtered_tools_info(
        openapi_spec,
        available_scopes=available_scopes,
        exclusion_config=exclusion_config,
        tool_prefix=tool_prefix,
    )

    excluded_routes = _compute_excluded_routes(
        openapi_spec,
        filtered_tools_info,
        tool_prefix=config.tool_prefix or "",
        scope_filtering_enabled=config.tool_filtering_enabled,
    )

    return (openapi_spec, extensions, filtered_tools_info, excluded_routes)


def _compute_excluded_routes(
    openapi_spec: OpenAPISpec,
    filtered_tools_info: dict[str, Any],
    tool_prefix: str = "",
    scope_filtering_enabled: bool = True,
) -> set[tuple[str, str]]:
    """Build the set of ``(path, UPPER_METHOD)`` tuples to exclude.

    Iterates the spec and marks every operation whose ``filtered_tools_info``
    entry reports a filter reason (deprecated, excluded, or scope).  The
    ``filtered_tools_info`` dict is keyed by operationId, so we map back to the
    concrete ``(path, method)`` pair while walking the spec.

    When ``scope_filtering_enabled`` is False, scope-reason exclusions are
    dropped (mirroring the old ``tool_filtering_enabled`` behaviour: that flag
    only gated scope filtering, while deprecated-endpoint and config-exclusion
    filtering always applied).

    Args:
        openapi_spec: The OpenAPI 3.1 spec (post-conversion, pre-provider).
        filtered_tools_info: The filter-prediction data (may be empty).
        tool_prefix: Namespace prefix (e.g. ``"gitea_"``) used to match
            prefixed operationIds in the filtered data.
        scope_filtering_enabled: When False, scope-reason routes are not
            excluded (deprecated + config-excluded still are).

    Returns:
        Set of ``(path, UPPER_METHOD)`` tuples to exclude.
    """
    excluded: set[tuple[str, str]] = set()
    filtered = filtered_tools_info.get("filtered", {})
    if not filtered:
        return excluded

    paths: dict[str, Any] = openapi_spec.get("paths", {}) or {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS_ALL or not isinstance(operation, dict):
                continue
            op_id: str = operation.get("operationId", "")
            if not op_id:
                continue
            # Match both prefixed and bare operationId (filtered_tools_info
            # stores the bare operationId, but be tolerant of either).
            reason = filtered.get(op_id) or (
                filtered.get(op_id[len(tool_prefix):]) if tool_prefix and op_id.startswith(tool_prefix) else None
            )
            if reason is None:
                continue
            if not scope_filtering_enabled and reason.get("reason") == "scope":
                continue
            excluded.add((path, method.upper()))

    if excluded:
        logger.info(
            "Computed %d excluded routes (spec-level filtering)",
            len(excluded),
            extra={"excluded_routes": sorted(excluded)},
        )
    return excluded


__all__ = [
    "convert_swagger_to_openapi_v3",
    "fetch_token_scopes",
    "load_and_convert_spec",
    "load_exclusion_config",
    "load_openapi_spec",
]
