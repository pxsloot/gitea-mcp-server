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
    assert isinstance(item, TextContent), f"Expected TextContent, got {type(item).__name__}"
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
        assert isinstance(item, TextContent), f"Expected TextContent, got {type(item).__name__}"
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
    assert hasattr(result, "isError"), f"Expected result with .isError, got {type(result).__name__}"
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
    assert isinstance(sc, dict), f"Expected dict, got {type(sc).__name__}"
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


def assert_dual_channel(result: object, *, fmt: str = "json") -> dict[str, Any]:
    """Assert the dual-channel contract: content present+valid, structured mirrors it.

    The MCP spec makes ``content`` the guaranteed channel of a tool result;
    ``structured_content`` is an optional mirror that, when present, duplicates
    ``content``.  This helper asserts that contract on a tool result:

    - ``content`` is present and valid: at least one ``TextContent`` item with
      non-empty text; for ``fmt`` in (``"json"``, ``"raw"``) the text must
      parse as JSON.
    - ``structured_content`` mirrors ``content``: for ``fmt="json"`` (and
      ``"raw"``) the parsed text equals ``structured_content`` exactly — the
      text is the serialized JSON of the structured payload.  For
      ``fmt="markdown"`` the mirror is weaker (content is a rendering), so the
      helper asserts ``result`` is present in ``structured_content``.

    Args:
        result: The tool result (``ToolResult`` or a duck-typed object with
            ``.content`` and ``.structured_content``).
        fmt: The output format the result was produced with — ``"json"``,
            ``"raw"``, or ``"markdown"``.

    Returns:
        The ``structured_content`` dict (or ``{}`` when absent) for further
        assertions.

    Raises:
        AssertionError: If any part of the contract is violated.
    """
    content = getattr(result, "content", None)
    assert content is not None, "Expected .content to be present"
    text = extract_text_content(list(content))
    assert text.strip(), "content must not be empty"

    sc = getattr(result, "structured_content", None)
    if fmt in ("json", "raw"):
        parsed = json.loads(text)
        if sc is not None:
            assert parsed == sc, "content JSON must mirror structured_content"
    elif sc is not None:
        # markdown: content is a rendering; structured carries the data
        assert "result" in sc, "structured_content must carry the result payload"
    return sc if isinstance(sc, dict) else {}


# --- Low-level MCP protocol helpers (integration tests) ---


def _safe_extract_error_text(root: object) -> str:
    """Extract text from a low-level root for error messages.

    Unlike ``extract_low_level_text``, this returns a safe default
    when content is missing or not ``TextContent``, so it can be used
    in assertion error messages without cascading assertion failures.
    """
    content = getattr(root, "content", None)
    if not content:
        return "no content"
    # Try to extract via the public helper; fall back if not TextContent
    try:
        return extract_text_content(list(content))
    except AssertionError:
        return str(content)


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
    assert not root.isError, f"Low-level call failed: {_safe_extract_error_text(root)}"


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
    assert isinstance(sc, dict), f"Expected dict, got {type(sc).__name__}"
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
    assert isinstance(content, str), f"Expected str content, got {type(content).__name__}"
    return content
