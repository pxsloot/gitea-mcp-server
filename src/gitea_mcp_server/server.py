"""Gitea MCP Server implementation."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.config import Config
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.logging_config import setup_logging
from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3
from gitea_mcp_server.tool_filter import filter_tools_by_permissions

logger = logging.getLogger(__name__)


async def load_swagger_spec(gitea_client: GiteaClient) -> dict[str, Any]:
    """Load Swagger spec from Gitea instance.

    Args:
        gitea_client: Client to use for fetching the spec

    Returns:
        Swagger spec as dictionary

    Raises:
        SpecError: If spec cannot be loaded or parsed
    """
    # Construct URL: base_url without /api/v1 + /swagger.v1.json
    base_url = gitea_client._config.url.rstrip("/")
    if base_url.endswith("/api/v1"):
        base_url = base_url[:-7]  # Remove "/api/v1"
    spec_url = f"{base_url}/swagger.v1.json"

    logger.info(f"Loading OpenAPI spec from {spec_url}")

    try:
        import json

        response = await gitea_client.request("GET", spec_url)
        spec = response.json()
        logger.info(
            "Spec loaded",
            extra={
                "spec_version": spec.get("swagger"),
                "paths_count": len(spec.get("paths", {})),
            },
        )
        return spec
    except json.JSONDecodeError as e:
        raise SpecError(f"Invalid JSON in spec from {spec_url}: {e}") from e
    except Exception as e:
        raise SpecError(f"Failed to fetch spec from {spec_url}: {e}") from e


async def create_mcp_server(gitea_client: GiteaClient) -> FastMCP:
    """Create the Gitea MCP server from OpenAPI spec.

    Args:
        gitea_client: Initialized GiteaClient to use for API calls

    Returns:
        Configured FastMCP server instance

    Raises:
        SpecError: If spec loading or conversion fails
    """
    config = gitea_client._config  # Access config for logging

    # Setup logging as early as possible
    setup_logging(level=config.log_level, log_format=config.log_format)

    logger.info("Starting Gitea MCP Server initialization")

    try:
        spec = await load_swagger_spec(gitea_client)
    except SpecError:
        raise
    except Exception as e:
        raise SpecError(f"Failed to load OpenAPI spec: {e}") from e

    logger.info("Converting OpenAPI v2 to v3...")
    try:
        openapi_spec = convert_swagger_to_openapi_v3(spec)
        logger.info(
            "Conversion completed",
            extra={
                "openapi_version": openapi_spec.get("openapi"),
                "paths": len(openapi_spec.get("paths", {})),
            },
        )
    except Exception as e:
        raise SpecError(f"Failed to convert OpenAPI spec: {e}") from e

    logger.info("Creating FastMCP server...")
    mcp = FastMCP.from_openapi(
        openapi_spec=openapi_spec,
        client=gitea_client.client,
        name="Gitea MCP Server",
    )

    # Apply tool filtering based on user permissions if enabled
    if config.tool_filtering_enabled:
        try:
            logger.info("Applying tool permission filtering")
            await filter_tools_by_permissions(mcp, gitea_client)
        except Exception as e:
            logger.exception(
                "Tool filtering failed, proceeding without filtering",
                extra={"error": str(e)},
            )
    else:
        logger.info("Tool filtering is disabled")

    logger.info("Gitea MCP Server initialized successfully")
    return mcp


async def main_async() -> None:
    """Async main entry point."""
    try:
        config = Config.get()
        setup_logging(level=config.log_level, log_format=config.log_format)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    gitea_client = GiteaClient(config)

    try:
        mcp = await create_mcp_server(gitea_client)
    except Exception as e:
        logger.error(f"Failed to initialize server: {e}", exc_info=True)
        await gitea_client.close()
        sys.exit(1)

    try:
        logger.info("Starting MCP server (stdio transport)")
        await mcp.run_stdio_async()
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
        # Exit normally, finally will close resources
    except Exception:
        logger.error("Server crashed", exc_info=True)
        sys.exit(1)
    finally:
        # Always close client first
        try:
            await gitea_client.close()
        except Exception:
            pass  # Ignore close errors during shutdown
        # Then shutdown logging to avoid writing to closed streams
        logging.shutdown()
        logging.shutdown()


def main() -> None:
    """Synchronous entry point that runs the async main."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
