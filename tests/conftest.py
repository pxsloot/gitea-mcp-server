"""Pytest configuration and fixtures."""

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.tool import ToolAnnotations


class SimpleConfig:
    """Canonical test config stub, mirrors essential Config behavior.

    All test files should import this from conftest rather than defining
    their own copy. Test-specific defaults can use ``functools.partial``
    or pass keyword arguments at instantiation.
    """

    def __init__(
        self,
        url="https://git.example.com",
        token="test_token",
        *,
        verify_ssl=False,
        ssl_cert_file=None,
        log_level="ERROR",
        log_format="text",
        tool_filtering_enabled=False,
        enable_lazy_loading=False,
        tool_prefix="gitea_",
        transport_type="stdio",
        http_host="127.0.0.1",
        http_port=8080,
        http_path="/mcp",
        http_cors=None,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.ssl_cert_file = ssl_cert_file
        self.log_level = log_level
        self.log_format = log_format
        self.tool_filtering_enabled = tool_filtering_enabled
        self.enable_lazy_loading = enable_lazy_loading
        self.tool_prefix = tool_prefix
        self.transport_type = transport_type
        self.http_host = http_host
        self.http_port = http_port
        self.http_path = http_path
        self.http_cors = http_cors

    @property
    def base_url(self) -> str:
        """Get the API base URL."""
        return f"{self.url}/api/v1"

# Configure logging for tests
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def make_mock_tool(name="test_tool", tags=None, annotations=None, parameters=None,
                    output_schema=None, description="", **kwargs):
    """Create a MagicMock with OpenAPITool spec for unit tests.

    Usage::

        tool = make_mock_tool(name="issue_list_issues", tags={"issue"})
        tool.run = AsyncMock(return_value=ToolResult(structured_content={"result": []}))
    """
    tool = MagicMock(spec=OpenAPITool)
    tool.name = name
    tool.annotations = annotations if annotations is not None else ToolAnnotations()
    tool.tags = tags or set()
    tool.parameters = parameters or {"properties": {}}
    tool.output_schema = output_schema
    tool.description = description
    tool.version = "1"
    tool.auth = None
    tool.serializer = None
    tool.meta = {}
    for k, v in kwargs.items():
        setattr(tool, k, v)
    return tool


def make_mock_route(path="/test", method="GET", summary="Test", operation_id="test_op"):
    """Create a MagicMock route for unit tests.

    Usage::

        route = make_mock_route("/repos/{owner}/{repo}/issues", "GET")
    """
    return MagicMock(
        path=path,
        method=method,
        summary=summary,
        operation_id=operation_id,
    )


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
