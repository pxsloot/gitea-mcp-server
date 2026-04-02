"""Gitea MCP Server implementation."""

import asyncio
import contextlib
import json
import logging
import sys
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.server.openapi import OpenAPITool
from fastmcp.tools.tool import ToolAnnotations

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.config import Config
from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.logging_config import setup_logging
from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3
from gitea_mcp_server.tool_filter import filter_tools_by_permissions

logger = logging.getLogger(__name__)

# Constants for title truncation
_TITLE_TRUNCATE_LIMIT = 50


def _categorize_tool(path: str) -> str:  # noqa: PLR0911
    """Categorize a tool based on its OpenAPI path.

    Args:
        path: The OpenAPI path pattern (e.g., "/repos/{owner}/{repo}/issues")

    Returns:
        Category string: "repository", "issue", "pull_request", "user", "organization", "admin", or "misc"
    """
    # Admin paths
    if path.startswith("/admin"):
        return "admin"

    # Organization paths
    if path.startswith(("/orgs", "/org/")):
        return "organization"

    # User paths
    if path.startswith(("/user", "/users/")):
        return "user"

    # Issue paths
    if "/issues" in path or path.startswith("/issues"):
        return "issue"

    # Pull request paths
    if "/pulls" in path or path.startswith("/pulls"):
        return "pull_request"

    # Repository paths (most common)
    if path.startswith("/repos"):
        return "repository"

    # Everything else
    return "misc"


def _generate_tool_title(route: Any) -> str:
    """Generate a human-readable title for a tool from its OpenAPI route metadata.

    Args:
        route: FastMCP route object with summary and operation_id attributes

    Returns:
        Title string (max 50 chars, truncated with "..." if needed)
    """
    summary = getattr(route, "summary", None)
    operation_id = getattr(route, "operation_id", None)

    title: str

    # Prefer summary if available and non-empty
    if summary and summary.strip():
        title = str(summary).strip()
    elif operation_id:
        # Convert snake_case to Title Case
        words = str(operation_id).replace("_", " ").title()
        title = words
    else:
        return "Unnamed Tool"

    # Truncate to _TITLE_TRUNCATE_LIMIT characters
    if len(title) > _TITLE_TRUNCATE_LIMIT:
        title = title[: _TITLE_TRUNCATE_LIMIT - 3] + "..."

    return title


def _add_inferred_hints(route: Any, annotations: ToolAnnotations) -> None:
    """Infer and add annotation hints from HTTP route properties.

    Hints are based on HTTP method semantics:
    - readOnlyHint: True for safe methods (GET, HEAD, OPTIONS)
    - destructiveHint: True for DELETE (and any method that destroys data)
    - idempotentHint: True for idempotent methods (GET, PUT, DELETE, HEAD, OPTIONS)
    - openWorldHint: Always True for Gitea tools (they interact with external server)

    Existing annotation values are preserved; only None values are set.

    Args:
        route: HTTPRoute object with method attribute
        annotations: ToolAnnotations instance to update
    """
    method = getattr(route, "method", None)

    # Define method sets based on HTTP semantics
    safe_methods = {"GET", "HEAD", "OPTIONS"}
    destructive_methods = {"DELETE"}
    idempotent_methods = {"GET", "PUT", "DELETE", "HEAD", "OPTIONS"}

    # Only add hints if they are currently None (preserve existing manual settings)
    if annotations.readOnlyHint is None:
        annotations.readOnlyHint = method in safe_methods

    if annotations.destructiveHint is None:
        annotations.destructiveHint = method in destructive_methods

    if annotations.idempotentHint is None:
        annotations.idempotentHint = method in idempotent_methods

    if annotations.openWorldHint is None:
        # All Gitea MCP tools interact with external Gitea server
        annotations.openWorldHint = True


def _customize_component(route: Any, component: Any) -> None:
    """Customize FastMCP components with tool annotations.

    This function is called by FastMCP's from_openapi for each generated component.
    It adds title and category annotations to tools and tags for categorization.

    Args:
        route: The OpenAPI route object
        component: The generated FastMCP component (tool, resource, etc.)
    """
    # Only customize OpenAPITool instances
    if not isinstance(component, OpenAPITool):
        return

    # Generate and set title annotation
    title = _generate_tool_title(route)
    category = _categorize_tool(route.path)

    # Create or update annotations
    if component.annotations is None:
        component.annotations = ToolAnnotations()
    elif isinstance(component.annotations, dict):  # type: ignore
        # Convert dict to ToolAnnotations while preserving existing fields
        existing = component.annotations  # type: ignore
        component.annotations = ToolAnnotations(**existing)

    # Set title
    component.annotations.title = title

    # Add inferred annotation hints based on HTTP method
    _add_inferred_hints(route, component.annotations)

    # Add category to tags (used for grouping in MCP clients)
    if component.tags is None:
        component.tags = set()  # type: ignore[unreachable]
    component.tags.add(category)


async def load_swagger_spec(gitea_client: GiteaClient | None = None) -> dict[str, Any]:
    """Load Swagger spec from Gitea instance or local file.

    Args:
        gitea_client: Optional client to use for fetching the spec. If not provided,
                     loads from local swagger.v1.json file.

    Returns:
        Swagger spec as dictionary

    Raises:
        SpecError: If spec cannot be loaded or parsed
    """
    if gitea_client is None:
        # Fallback to loading local spec file (for testing)
        logger.info("Loading OpenAPI spec from local swagger.v1.json")
        try:
            spec_path = Path("swagger.v1.json")
            if not spec_path.exists():
                raise SpecError("Local swagger.v1.json file not found")
            with open(spec_path) as f:
                spec = json.load(f)
            logger.info(
                "Spec loaded",
                extra={
                    "spec_version": spec.get("swagger"),
                    "paths_count": len(spec.get("paths", {})),
                },
            )
            return spec
        except json.JSONDecodeError as e:
            raise SpecError(f"Invalid JSON in local swagger.v1.json: {e}") from e
        except Exception as e:
            raise SpecError(f"Failed to load local swagger.v1.json: {e}") from e

    # Construct URL: base_url without /api/v1 + /swagger.v1.json
    spec_url = f"{gitea_client._config.url}/swagger.v1.json"

    logger.info("Loading OpenAPI spec from %s", spec_url)

    try:
        response = await gitea_client.request("GET", spec_url)
        spec = response.json()
        logger.info(
            "Spec loaded",
            extra={
                "spec_version": spec.get("swagger"),
                "paths_count": len(spec.get("paths", {})),
            },
        )
        return cast("dict[str, Any]", spec)
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in spec from {spec_url}: {e}"
        raise SpecError(msg) from e
    except Exception as e:
        msg = f"Failed to fetch spec from {spec_url}: {e}"
        raise SpecError(msg) from e


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
        msg = f"Failed to load OpenAPI spec: {e}"
        raise SpecError(msg) from e

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
        msg = f"Failed to convert OpenAPI spec: {e}"
        raise SpecError(msg) from e

    logger.info("Creating FastMCP server...")
    mcp = FastMCP.from_openapi(
        openapi_spec=openapi_spec,
        client=gitea_client.client,
        name="Gitea MCP Server",
        mcp_component_fn=_customize_component,
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
    except Exception as e:  # noqa: BLE001
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    gitea_client = GiteaClient(config)

    try:
        mcp = await create_mcp_server(gitea_client)
    except Exception:
        logger.exception("Failed to initialize server")
        await gitea_client.close()
        sys.exit(1)

    try:
        logger.info("Starting MCP server (stdio transport)")
        await mcp.run_stdio_async()
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
        # Exit normally, finally will close resources
    except Exception:
        logger.exception("Server crashed")
        sys.exit(1)
    finally:
        # Always close client first
        with contextlib.suppress(Exception):
            await gitea_client.close()
        # Then shutdown logging to avoid writing to closed streams
        logging.shutdown()
        logging.shutdown()


def main() -> None:
    """Synchronous entry point that runs the async main."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
