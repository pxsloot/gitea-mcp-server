"""Test helpers for gitea-mcp-server.

Each module covers a specific domain:

- ``mcp_results.py`` — Narrow FastMCP result union types (``TextContent | ...``,
  ``CallToolResult | ...``, ``dict | None``) to eliminate mypy ``union-attr`` errors.
- ``mock_tool.py`` — ``make_mock_tool``, ``make_mock_route`` for tool customization tests.
- ``tool_names.py`` — ``extract_tool_names`` for test assertions.
- ``spec_fixtures.py`` — ``base_spec``, ``minimal_spec`` for spec loading tests.
"""

from tests.helpers.mcp_results import (
    assert_call_success,
    assert_low_level_success,
    assert_resource_success,
    extract_low_level_text,
    extract_resource_text,
    extract_text_content,
    extract_text_from_content_items,
    get_low_level_structured,
    get_structured,
    parse_json_content,
)

__all__ = [
    "assert_call_success",
    "assert_low_level_success",
    "assert_resource_success",
    "extract_low_level_text",
    "extract_resource_text",
    "extract_text_content",
    "extract_text_from_content_items",
    "get_low_level_structured",
    "get_structured",
    "parse_json_content",
]
