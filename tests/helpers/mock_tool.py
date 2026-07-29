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

Mock helpers
~~~~~~~~~~~~

``make_async_mock`` and ``make_magic_mock`` create mocks with ``spec=``
while preserving mock attribute access (``.return_value``, ``.side_effect``,
``assert_called_once_with``, etc.).  Use these instead of bare
``AsyncMock(spec=X)`` or ``MagicMock(spec=X)`` to avoid mypy narrowing the
mock to the spec's type.  See ``test_label_transform.py`` and
``test_tool_labels.py`` for usage.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastmcp.server.providers.openapi import OpenAPITool
from mcp.types import ToolAnnotations


def make_async_mock(spec: type[Any] | None = None) -> AsyncMock:
    """Create an ``AsyncMock`` with optional ``spec``, preserving mock attributes.

    Unlike ``AsyncMock(spec=X)`` (which mypy narrows to type ``X``, losing
    access to ``.return_value``, ``.assert_called_once_with``, etc.), this
    helper is typed as ``-> AsyncMock``, so callers see the mock type, not
    the narrowed spec type::

        svc = make_async_mock(LabelService)
        svc.validate_and_convert.return_value = [1, 2]  # no attr-defined error
    """
    return AsyncMock(spec=spec)


def make_magic_mock(spec: Any | None = None) -> MagicMock:
    """Create a ``MagicMock`` with optional ``spec``, preserving mock attributes.

    Same idea as ``make_async_mock`` but for synchronous ``MagicMock``::

        mock_fn = make_magic_mock(resolve_label_names)
        mock_fn.return_value = [1, 2]  # no attr-defined error
    """
    return MagicMock(spec=spec)


def make_mock_tool(
    name: str = "test_tool",
    tags: Any = None,
    annotations: Any = None,
    parameters: Any = None,
    output_schema: Any = None,
    description: str = "",
    **kwargs: Any,
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


def make_mock_route(path: str = "/test", method: str = "GET", summary: str = "Test", operation_id: str = "test_op") -> MagicMock:
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
