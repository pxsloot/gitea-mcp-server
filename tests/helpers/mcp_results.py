"""Helpers for narrowing FastMCP result union types in tests.

FastMCP returns union types from tool/resource calls that mypy correctly
flags as ``union-attr`` when accessing ``.text``, ``.isError``, ``.content``,
or ``.structured_content``.  These helpers use ``isinstance`` assertions to
narrow the unions at runtime and satisfy mypy.

Usage:

    from tests.helpers.mcp_results import (
        assert_call_success,
        extract_text_content,
        get_structured,
        parse_json_content,
        extract_text_from_content_items,
    )

    result = await server.call_tool("gitea_...", {...})

    # Extract text from the first content item
    text = extract_text_content(result.content)

    # Assert the call succeeded
    assert_call_success(result)

    # Parse content as JSON
    data = parse_json_content(result)

    # Get structured_content (narrowing dict[str, Any] | None)
    sc = get_structured(result)
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import TextContent


def extract_text_content(content: list[Any]) -> str:
    """Extract text from the first content item.

    Asserts the first item is ``TextContent`` to narrow the union type
    (``TextContent | ImageContent | AudioContent | ...``).

    Args:
        content: The ``.content`` list from a tool result.

    Returns:
        The ``.text`` value of the first content item.
    """
    assert len(content) > 0, "Expected at least one content item"
    item = content[0]
    assert isinstance(item, TextContent), (
        f"Expected TextContent, got {type(item).__name__}"
    )
    return item.text


def extract_text_from_content_items(content: list[Any]) -> str:
    """Join all text from all ``TextContent`` items in a content list.

    Useful for patterns like ``''.join(c.text for c in result.content)``.

    Args:
        content: The ``.content`` list from a tool result.

    Returns:
        Concatenated text from all ``TextContent`` items.
    """
    texts: list[str] = []
    for item in content:
        assert isinstance(item, TextContent), (
            f"Expected TextContent, got {type(item).__name__}"
        )
        texts.append(item.text)
    return "".join(texts)


def assert_call_success(result: object) -> None:
    """Assert a tool call result indicates success (no error).

    Narrows the result union (``EmptyResult | InitializeResult | ... |
    CallToolResult``) by accessing ``.isError`` and ``.content``.

    Args:
        result: The tool call result (typically ``ToolResult`` or
            a low-level MCP result with ``.isError`` and ``.content``).

    Raises:
        AssertionError: If ``isError`` is truthy, with the error content.
    """
    assert hasattr(result, "isError"), (
        f"Expected result with .isError, got {type(result).__name__}"
    )
    is_error = getattr(result, "isError", False)
    content = getattr(result, "content", [])
    assert not is_error, f"Tool call failed: {content}"


def get_structured(result: object) -> dict[str, Any]:
    """Get ``structured_content``, asserting it is not ``None``.

    Narrows ``dict[str, Any] | None`` to ``dict[str, Any]``.

    Args:
        result: The tool call result.

    Returns:
        The ``structured_content`` dict.

    Raises:
        AssertionError: If ``structured_content`` is ``None``.
    """
    sc = getattr(result, "structured_content", None)
    assert sc is not None, "Expected structured_content to be present"
    assert isinstance(sc, dict), (
        f"Expected dict, got {type(sc).__name__}"
    )
    return sc


def parse_json_content(result: object) -> Any:
    """Parse the first content item as JSON.

    Combines ``extract_text_content`` and ``json.loads`` for the common
    ``json.loads(result.content[0].text)`` pattern.

    Args:
        result: The tool call result.

    Returns:
        The parsed JSON value.
    """
    content = getattr(result, "content", None)
    assert content is not None, "Expected .content to be present"
    text = extract_text_content(list(content))
    return json.loads(text)


# --- Low-level MCP protocol helpers (integration tests) ---


def assert_low_level_success(result: object) -> None:
    """Assert a low-level MCP call result succeeded.

    Low-level results wrap the actual result in ``.root`` (the JSON-RPC
    response envelope).  This helper inspects ``result.root.isError``.

    Args:
        result: The low-level MCP response (has ``.root`` with
            ``.isError`` and ``.content``).

    Raises:
        AssertionError: If the call failed.
    """
    root = getattr(result, "root", None)
    assert root is not None, f"Expected result with .root, got {type(result).__name__}"
    assert not root.isError, (
        f"Low-level call failed: "
        f"{root.content[0].text if root.content else 'no content'}"
    )


def extract_low_level_text(result: object) -> str:
    """Extract text from a low-level MCP result's first content item.

    Args:
        result: The low-level MCP response (has ``.root.content``).

    Returns:
        The text from the first ``TextContent`` item.
    """
    root = getattr(result, "root", None)
    assert root is not None, f"Expected result with .root, got {type(result).__name__}"
    assert root.content is not None, "Expected .root.content to be present"
    return extract_text_content(list(root.content))


def get_low_level_structured(result: object) -> dict[str, Any]:
    """Get ``structuredContent`` from a low-level MCP result.

    Args:
        result: The low-level MCP response (has ``.root.structuredContent``).

    Returns:
        The structured content dict.
    """
    root = getattr(result, "root", None)
    assert root is not None, f"Expected result with .root, got {type(result).__name__}"
    sc = getattr(root, "structuredContent", None)
    assert sc is not None, "Expected .root.structuredContent to be present"
    assert isinstance(sc, dict), (
        f"Expected dict, got {type(sc).__name__}"
    )
    return sc


# --- ReadResourceResult helpers (server.read_resource) ---


def assert_resource_success(result: object) -> None:
    """Assert a ``read_resource`` call returned content.

    Args:
        result: The ``ReadResourceResult`` (has ``.contents``).

    Raises:
        AssertionError: If no contents present.
    """
    contents = getattr(result, "contents", None)
    assert contents is not None, f"Expected result with .contents, got {type(result).__name__}"
    assert len(contents) > 0, "Expected at least one resource content"


def extract_resource_text(result: object) -> str:
    """Extract text from the first resource content item.

    Args:
        result: The ``ReadResourceResult`` (``.contents[0].content``).

    Returns:
        The text content of the first resource.
    """
    contents = getattr(result, "contents", None)
    assert contents is not None, f"Expected result with .contents, got {type(result).__name__}"
    assert len(contents) > 0, "Expected at least one resource content"
    content = contents[0].content
    assert isinstance(content, str), (
        f"Expected str content, got {type(content).__name__}"
    )
    return content
