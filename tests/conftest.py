"""Pytest configuration and fixtures."""

import asyncio
import json
import logging
from pathlib import Path

import pytest

# Configure logging for tests
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def extract_tool_names(tools):
    """Extract tool names from mcp.get_tools() return value.

    Args:
        tools: The result from mcp.get_tools(), which can be a dict, list, or other structure.

    Returns:
        List of tool names as strings.
    """
    if isinstance(tools, dict):
        return list(tools.keys())
    if isinstance(tools, list):
        tool_names = []
        for tool in tools:
            if hasattr(tool, "name"):
                tool_names.append(tool.name)
            elif isinstance(tool, str):
                tool_names.append(tool)
            else:
                try:
                    if hasattr(tool, "get"):
                        name = tool.get("name")
                        if name:
                            tool_names.append(name)
                except Exception:
                    pass
        return tool_names
    return []


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace with sample files."""
    return tmp_path


@pytest.fixture
def swagger_spec_fixture():
    """Load the swagger spec for tests."""
    spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
    if not spec_path.exists():
        pytest.skip("swagger.v1.json not found")

    with spec_path.open() as f:
        return json.load(f)
