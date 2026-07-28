"""Mock tool and route factories for unit tests.

Provides ``make_mock_tool`` and ``make_mock_route``, the canonical way to
create ``MagicMock`` objects with ``OpenAPITool`` and route specs in unit
tests.  Using these factories ensures tools have the expected default
attributes and reduces boilerplate.

Usage::

    from tests.helpers.mock_tool import make_mock_tool, make_mock_route

    tool = make_mock_tool(name="issue_list_issues", tags={"issue"})
    tool.run = AsyncMock(return_value=ToolResult(structured_content={"result": []}))

    route = make_mock_route("/repos/{owner}/{repo}/issues", "GET")
"""

from unittest.mock import MagicMock

from fastmcp.server.providers.openapi import OpenAPITool
from mcp.types import ToolAnnotations


def make_mock_tool(
    name: str ="test_tool",
    tags=None,
    annotations=None,
    parameters=None,
    output_schema=None,
    description="",
    **kwargs,
) -> MagicMock:
    """Create a MagicMock with OpenAPITool spec for unit tests.

    Parameters
    ----------
    name:
        Tool name (default ``"test_tool"``).
    tags:
        Set of tag strings (default empty set).
    annotations:
        ``ToolAnnotations`` instance (default empty).
    parameters:
        Parameter schema dict (default ``{"properties": {}}``).
    output_schema:
        Output schema dict or ``None``.
    description:
        Tool description string.
    **kwargs:
        Additional attributes set on the mock.

    Returns
    -------
    MagicMock
        A mock object with ``spec=OpenAPITool``.
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


def make_mock_route(path="/test", method="GET", summary="Test", operation_id="test_op") -> MagicMock:
    """Create a MagicMock route for unit tests.

    Parameters
    ----------
    path:
        URL path template (default ``"/test"``).
    method:
        HTTP method (default ``"GET"``).
    summary:
        Route summary (default ``"Test"``).
    operation_id:
        OpenAPI operationId (default ``"test_op"``).

    Returns
    -------
    MagicMock
        A mock object with ``path``, ``method``, ``summary``, and
        ``operation_id`` attributes.
    """
    return MagicMock(
        path=path,
        method=method,
        summary=summary,
        operation_id=operation_id,
    )
