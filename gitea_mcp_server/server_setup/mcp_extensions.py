"""OpenAPI MCP extensions processor.

This module provides functions to load and apply MCP-specific customizations
from a local YAML configuration file to the OpenAPI spec before tool generation.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Default filename for extensions configuration
EXTENSIONS_FILE = "mcp_extensions.yaml"


def _find_project_root() -> Path:
    """Find the project root directory (containing pyproject.toml).

    Starts from this file's location and walks up the directory tree.

    Returns:
        Path to project root directory

    Raises:
        RuntimeError: If project root cannot be determined
    """
    # Start from this file's directory and walk up
    current = Path(__file__).resolve()
    for parent in [current, *list(current.parents)]:
        if (parent / "pyproject.toml").exists():
            return parent

    msg = "Could not find project root (directory containing pyproject.toml). "
    msg += "Set MCP_EXTENSIONS_PATH environment variable to override."
    raise RuntimeError(msg) from None


def load_mcp_extensions(config_path: Path | None = None) -> dict[str, Any]:
    """Load MCP extensions configuration from YAML file.

    Args:
        config_path: Optional explicit path to extensions file.
                    If None, the config file is located using this order:
                    1. MCP_EXTENSIONS_PATH environment variable (if set)
                    2. mcp_extensions.yaml in project root (directory with pyproject.toml)
                    3. mcp_extensions.yaml in current working directory (fallback)

    Returns:
        Dictionary with extension configuration, or empty dict if no file exists.

    Raises:
        yaml.YAMLError: If the file exists but contains invalid YAML
        OSError: If the file exists but cannot be read
    """
    if config_path is None:
        # 1. Check environment variable
        env_path = os.getenv("MCP_EXTENSIONS_PATH")
        if env_path:
            config_path = Path(env_path)
        else:
            # 2. Try project root (where pyproject.toml lives)
            try:
                project_root = _find_project_root()
                config_path = project_root / EXTENSIONS_FILE
            except RuntimeError:
                # 3. Fallback to cwd
                config_path = Path.cwd() / EXTENSIONS_FILE

    if not config_path.exists():
        logger.debug("MCP extensions file not found: %s", config_path)
        return {}

    try:
        with config_path.open() as f:
            extensions = yaml.safe_load(f)
            if extensions is None:
                logger.info("MCP extensions file is empty: %s", config_path)
                return {}
            logger.info(
                "Loaded MCP extensions",
                extra={
                    "path": str(config_path),
                    "tools": len(extensions.get("tool_names", {})),
                },
            )
            return extensions  # type: ignore[no-any-return]
    except yaml.YAMLError:
        logger.exception("Invalid YAML in MCP extensions file %s", config_path)
        raise
    except OSError:
        logger.exception("Cannot read MCP extensions file %s", config_path)
        raise


def _apply_parameter_extensions(operation: dict[str, Any], param_extensions: list[Any]) -> None:
    """Apply parameter customizations to an operation."""
    param_map = {p["name"]: p for p in operation.get("parameters", []) if isinstance(p, dict)}
    for param_ext in param_extensions:
        param_name = param_ext.get("name")
        if not param_name or param_name not in param_map:
            continue
        param = param_map[param_name]
        if "description" in param_ext:
            param["description"] = param_ext["description"]
        if "examples" in param_ext:
            param["examples"] = param_ext["examples"]


def _apply_operation_extension(
    operation: dict[str, Any], path: str, method: str, extensions: dict[str, Any]
) -> None:
    """Apply extensions to a single operation."""
    op_id = operation.get("operationId")
    if not op_id or op_id not in extensions:
        return

    ext = extensions[op_id]
    logger.debug(
        "Applying extensions for operation",
        extra={"operation_id": op_id, "path": path, "method": method},
    )

    if "title" in ext:
        operation["summary"] = ext["title"]
    if "description" in ext:
        operation["description"] = ext["description"]
    if "parameters" in ext and isinstance(ext["parameters"], list):
        _apply_parameter_extensions(operation, ext["parameters"])

    operation.pop("x-mcp", None)


def apply_mcp_extensions(openapi_spec: dict[str, Any], extensions: dict[str, Any]) -> None:
    """Apply MCP customizations from extensions to the OpenAPI spec.

    This function mutates the openapi_spec in-place by:
    - Overriding operation summary (title) and description from extensions
    - Updating parameter descriptions and examples
    - Removing any x-mcp fields after processing

    Args:
        openapi_spec: The OpenAPI specification dictionary (will be modified in-place)
        extensions: Extensions configuration dictionary from load_mcp_extensions()
    """
    tool_names = extensions.get("tool_names", {})
    if not tool_names:
        logger.debug("No operation ID extensions to apply")
        return

    logger.info(
        "Applying MCP extensions",
        extra={"tools_count": len(tool_names)},
    )

    valid_methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}
    for path, path_item in openapi_spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in valid_methods or not isinstance(operation, dict):
                continue
            _apply_operation_extension(operation, path, method, tool_names)

    logger.info("MCP extensions applied successfully")


__all__ = ["apply_mcp_extensions", "load_mcp_extensions"]
