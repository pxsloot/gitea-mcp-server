"""OpenAPI spec loading and conversion utilities."""

import json
import logging
from pathlib import Path
from typing import Any

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3
from gitea_mcp_server.server_setup.mcp_extensions import apply_mcp_extensions, load_mcp_extensions

logger = logging.getLogger(__name__)


def _raise_spec_error(message: str) -> None:
    """Raise SpecError with pre-computed message."""
    raise SpecError(message) from None


async def load_openapi_spec(gitea_client: GiteaClient | None = None) -> dict[str, Any]:
    """Load OpenAPI spec from Gitea instance or local file.

    Args:
        gitea_client: Optional client to use for fetching the spec. If not provided,
                     loads from local swagger.v1.json file.

    Returns:
        OpenAPI spec (Swagger 2.0 format) as dictionary

    Raises:
        SpecError: If spec cannot be loaded or parsed
    """
    if gitea_client is None:
        # Fallback to loading local spec file (for testing)
        logger.info("Loading OpenAPI spec from local swagger.v1.json")
        try:
            spec_path = Path("swagger.v1.json")
            if not spec_path.exists():
                _raise_spec_error("Local swagger.v1.json file not found")
            with open(spec_path) as f:
                local_spec: dict[str, Any] = json.load(f)
            logger.info(
                "Spec loaded",
                extra={
                    "spec_version": local_spec.get("swagger"),
                    "paths_count": len(local_spec.get("paths", {})),
                },
            )
            return local_spec
        except json.JSONDecodeError as e:
            msg = f"Invalid JSON in local swagger.v1.json: {e}"
            raise SpecError(msg) from e
        except Exception as e:
            msg = f"Failed to load local swagger.v1.json: {e}"
            raise SpecError(msg) from e

    # Construct URL: base_url without /api/v1 + /swagger.v1.json
    spec_url = f"{gitea_client._config.url}/swagger.v1.json"

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
        return remote_spec  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in spec from {spec_url}: {e}"
        raise SpecError(msg) from e
    except Exception as e:
        msg = f"Failed to fetch or parse spec from {spec_url}: {e}"
        raise SpecError(msg) from e


async def load_and_convert_spec(gitea_client: GiteaClient) -> dict[str, Any]:
    """Load Swagger spec and convert to OpenAPI v3 format.

    Args:
        gitea_client: GiteaClient for fetching the spec

    Returns:
        OpenAPI v3 spec as dictionary

    Raises:
        SpecError: If spec loading or conversion fails
    """
    try:
        spec = await load_openapi_spec(gitea_client)
    except SpecError:
        raise
    except Exception as e:
        msg = f"Failed to load OpenAPI spec: {e}"
        raise SpecError(msg) from e

    try:
        openapi_spec = convert_swagger_to_openapi_v3(spec)
    except Exception as e:
        msg = f"Failed to convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    try:
        extensions = load_mcp_extensions()
        if extensions:
            apply_mcp_extensions(openapi_spec, extensions)
    except (OSError, KeyError, ValueError) as e:
        logger.warning(
            "Failed to apply MCP extensions, proceeding without customizations",
            extra={"error": str(e)},
        )

    return openapi_spec


__all__ = ["convert_swagger_to_openapi_v3", "load_and_convert_spec", "load_openapi_spec"]
