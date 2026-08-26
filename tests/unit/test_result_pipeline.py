"""Tests for the single result pipeline (tools/result_pipeline.py).

The pipeline is the single display path for every tool: executors return raw
data (``ExecutionResult``) and :func:`render` applies shape → paginate →
format → ``ToolResult``.  These tests lock the dual-channel contract (content
mirrors structured_content), the envelope-in-text behaviour, deterministic
raw, the empty-json shape, and the shape variants.
"""

from __future__ import annotations

from typing import Any

import pytest

from gitea_mcp_server.tools.result_pipeline import ExecutionResult, render
from tests.helpers.mcp_results import (
    assert_dual_channel,
    extract_text_content,
    get_structured,
    parse_json_content,
)


def _items(n: int) -> list[dict[str, int]]:
    return [{"id": i} for i in range(n)]


class TestListShape:
    """The list shape: pipeline slices by page/limit, envelopes, fetch_all."""

    def test_slices_page(self) -> None:
        result = render(
            ExecutionResult(data=_items(25), total_count=25, shape="list", paginated=True),
            fmt="json",
            page=2,
            limit=10,
        )
        sc = get_structured(result)
        assert len(sc["result"]) == 10
        assert sc["result"][0]["id"] == 10
        assert sc["has_more"] is True
        assert sc["next_offset"] == 3
        assert sc["total_count"] == 25

    def test_last_page(self) -> None:
        result = render(
            ExecutionResult(data=_items(25), total_count=25, shape="list", paginated=True),
            fmt="json",
            page=3,
            limit=10,
        )
        sc = get_structured(result)
        assert len(sc["result"]) == 5
        assert sc["has_more"] is False
        assert sc["next_offset"] is None

    def test_fetch_all_returns_all_items(self) -> None:
        result = render(
            ExecutionResult(data=_items(50), total_count=50, shape="list", paginated=True),
            fmt="json",
            page=1,
            limit=10,
            fetch_all=True,
        )
        sc = get_structured(result)
        assert len(sc["result"]) == 50
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 50

    def test_out_of_range_page_emits_empty_envelope(self) -> None:
        result = render(
            ExecutionResult(data=_items(25), total_count=25, shape="list", paginated=True),
            fmt="json",
            page=5,
            limit=10,
        )
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["has_more"] is False
        assert sc["next_offset"] is None
        assert sc["total_count"] == 25
        assert "out of range" in sc["message"]

    def test_empty_total_emits_message(self) -> None:
        result = render(
            ExecutionResult(
                data=[],
                total_count=0,
                shape="list",
                paginated=True,
                message="No results found for 'x'.",
            ),
            fmt="json",
            page=1,
            limit=10,
        )
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["message"] == "No results found for 'x'."
        assert sc["has_more"] is False
        assert sc["total_count"] == 0

    def test_unknown_total_uses_full_page_heuristic(self) -> None:
        """When total_count is unknown, has_more follows the full-page heuristic."""
        result = render(
            ExecutionResult(data=_items(10), shape="list", paginated=True),
            fmt="json",
            page=1,
            limit=10,
        )
        sc = get_structured(result)
        # A full page (len == limit) with unknown total implies more pages.
        assert sc["total_count"] is None
        assert sc["has_more"] is True
        assert sc["next_offset"] == 2


class TestObjectShape:
    """The object shape: unpaginated, or paginated with executor total."""

    def test_unpaginated_object_no_envelope(self) -> None:
        result = render(
            ExecutionResult(data={"name": "alpha"}, shape="object"),
            fmt="json",
        )
        sc = get_structured(result)
        assert sc == {"result": {"name": "alpha"}}

    def test_paginated_object_emits_envelope(self) -> None:
        result = render(
            ExecutionResult(
                data={"name": "alpha", "output_schema": {"properties": {"a": {}, "b": {}}}},
                total_count=2,
                shape="object",
                paginated=True,
            ),
            fmt="json",
            page=1,
            limit=10,
        )
        sc = get_structured(result)
        assert sc["result"]["name"] == "alpha"
        assert sc["has_more"] is False
        assert sc["total_count"] == 2

    def test_paginated_object_unknown_total(self) -> None:
        result = render(
            ExecutionResult(data={"name": "alpha"}, shape="object", paginated=True),
            fmt="json",
            page=1,
            limit=10,
        )
        sc = get_structured(result)
        assert sc["has_more"] is False
        assert sc["total_count"] is None


class TestTextShape:
    """The text shape: wrapped in {"result": text}, no envelope."""

    def test_markdown_returns_text(self) -> None:
        result = render(
            ExecutionResult(data="diff --git a/x b/x", shape="text"),
            fmt="markdown",
        )
        assert extract_text_content(result.content) == "diff --git a/x b/x"
        assert get_structured(result) == {"result": "diff --git a/x b/x"}

    def test_json_wraps_in_result(self) -> None:
        result = render(
            ExecutionResult(data="diff --git a/x b/x", shape="text"),
            fmt="json",
        )
        assert parse_json_content(result) == {"result": "diff --git a/x b/x"}


class TestEmptyShape:
    """The empty shape: paginated (empty page) and non-paginated (204/205)."""

    def test_paginated_empty_json_shape(self) -> None:
        """Empty page format=json carries result/message/envelope as JSON text."""
        result = render(
            ExecutionResult(
                data=[],
                total_count=0,
                shape="empty",
                paginated=True,
                message="No results found for 'x'.",
            ),
            fmt="json",
            page=1,
            limit=10,
        )
        assert_dual_channel(result, fmt="json")
        parsed = parse_json_content(result)
        assert parsed == {
            "result": [],
            "message": "No results found for 'x'.",
            "has_more": False,
            "next_offset": None,
            "total_count": 0,
        }

    def test_paginated_empty_markdown_returns_message(self) -> None:
        result = render(
            ExecutionResult(
                data=[],
                total_count=0,
                shape="empty",
                paginated=True,
                message="No results found for 'x'.",
            ),
            fmt="markdown",
            page=1,
            limit=10,
        )
        assert extract_text_content(result.content) == "No results found for 'x'."
        sc = get_structured(result)
        assert sc["result"] == []
        assert sc["message"] == "No results found for 'x'."

    def test_non_paginated_empty_no_content(self) -> None:
        """204/205: result=None, message used for markdown text only."""
        result = render(
            ExecutionResult(
                data=None,
                shape="empty",
                message="Operation completed successfully.",
            ),
            fmt="markdown",
        )
        assert extract_text_content(result.content) == "Operation completed successfully."
        assert get_structured(result) == {"result": None}

    def test_non_paginated_empty_json(self) -> None:
        result = render(
            ExecutionResult(data=None, shape="empty"),
            fmt="json",
        )
        assert parse_json_content(result) == {"result": None}

    def test_paginated_empty_markdown_uses_defaulted_message(self) -> None:
        """Empty page without an executor message: the markdown text matches
        the envelope's defaulted message instead of rendering the empty data."""
        result = render(
            ExecutionResult(data=[], total_count=0, shape="empty", paginated=True),
            fmt="markdown",
            page=1,
            limit=10,
        )
        assert extract_text_content(result.content) == "No results found."
        sc = get_structured(result)
        assert sc["message"] == "No results found."


class TestBinaryShape:
    """The binary shape: content_info metadata instead of bytes."""

    def test_binary_envelope(self) -> None:
        info = {"type": "binary", "size": 42}
        result = render(
            ExecutionResult(
                data=info,
                shape="binary",
                message="Binary content (42 bytes). Use format='raw' to access directly.",
            ),
            fmt="json",
        )
        sc = get_structured(result)
        assert sc["result"] is None
        assert sc["content_info"] == info

    def test_binary_markdown_returns_guidance(self) -> None:
        result = render(
            ExecutionResult(
                data={"type": "binary", "size": 42},
                shape="binary",
                message="Binary content (42 bytes). Use format='raw' to access directly.",
            ),
            fmt="markdown",
        )
        assert "Binary content (42 bytes)" in extract_text_content(result.content)


class TestFormats:
    """json/raw/markdown produce dual-channel results."""

    def test_json_dual_channel(self) -> None:
        result = render(
            ExecutionResult(data=_items(25), total_count=25, shape="list", paginated=True),
            fmt="json",
            page=1,
            limit=10,
        )
        assert_dual_channel(result, fmt="json")

    def test_raw_dual_channel(self) -> None:
        """format=raw is deterministic JSON text mirroring structured_content."""
        result = render(
            ExecutionResult(data=_items(25), total_count=25, shape="list", paginated=True),
            fmt="raw",
            page=1,
            limit=10,
        )
        assert_dual_channel(result, fmt="raw")

    def test_markdown_dual_channel(self) -> None:
        result = render(
            ExecutionResult(data=_items(1), total_count=1, shape="list", paginated=True),
            fmt="markdown",
            page=1,
            limit=10,
        )
        sc = assert_dual_channel(result, fmt="markdown")
        assert sc["total_count"] == 1

    def test_markdown_extras_appended(self) -> None:
        result = render(
            ExecutionResult(
                data=_items(1),
                total_count=1,
                shape="list",
                paginated=True,
                markdown_extras=["**Cross-linking hints:**\n- For API tools: `search_tools`"],
            ),
            fmt="markdown",
            page=1,
            limit=10,
        )
        text = extract_text_content(result.content)
        assert "Cross-linking hints" in text

    def test_markdown_formatter_used(self) -> None:
        result = render(
            ExecutionResult(
                data={"content": "guide text"},
                shape="object",
                markdown_formatter=lambda d: d["content"],
            ),
            fmt="markdown",
        )
        assert extract_text_content(result.content) == "guide text"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported format"):
            render(ExecutionResult(data={}), fmt="xml")


class TestDetailConcise:
    """detail=concise collapses $ref-backed objects via the schema."""

    def test_json_concise_collapses(self) -> None:
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {"owner": {"$ref": "#/components/schemas/User"}},
        }
        result = render(
            ExecutionResult(data=data, shape="object"),
            fmt="json",
            detail="concise",
            schema=schema,
        )
        parsed = parse_json_content(result)
        assert parsed["result"]["owner"] == "$ref:User"

    def test_json_full_no_collapse(self) -> None:
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {"owner": {"$ref": "#/components/schemas/User"}},
        }
        result = render(
            ExecutionResult(data=data, shape="object"),
            fmt="json",
            detail="full",
            schema=schema,
        )
        parsed = parse_json_content(result)
        assert parsed["result"]["owner"]["login"] == "user1"


class TestErrorRecovery:
    """Formatting errors recover with a readable fallback."""

    def test_non_serializable_data_recovers(self) -> None:
        """Circular data: json.dumps fails, fallback wraps the stringified data."""
        data: dict[str, Any] = {}
        data["self"] = data
        result = render(
            ExecutionResult(data=data, shape="object"),
            fmt="json",
        )
        # The fallback wraps the stringified data in {"result": ...}.
        parsed = parse_json_content(result)
        assert "result" in parsed

    def test_markdown_recovers_with_code_fence(self) -> None:
        """Non-string dict key: the markdown formatter raises, fallback kicks in."""
        result = render(
            ExecutionResult(data={1: "x"}, shape="object"),
            fmt="markdown",
        )
        text = extract_text_content(result.content)
        assert "```json" in text
        assert "formatting failed" in text

    def test_raw_recovers_with_valid_json(self) -> None:
        """format=raw error recovery still emits valid JSON text content."""
        data: dict[str, Any] = {}
        data["self"] = data
        result = render(
            ExecutionResult(data=data, shape="object"),
            fmt="raw",
        )
        parsed = parse_json_content(result)
        assert "result" in parsed


class TestDualChannelContract:
    """The pipeline is the single writer of both channels."""

    def test_paginated_json_envelope_in_text(self) -> None:
        """Paginated format=json carries the envelope beside result in the text."""
        result = render(
            ExecutionResult(data=_items(25), total_count=25, shape="list", paginated=True),
            fmt="json",
            page=1,
            limit=10,
        )
        assert_dual_channel(result, fmt="json")

    def test_non_paginated_json_mirrors_structured(self) -> None:
        """Non-paginated format=json text mirrors structured_content."""
        result = render(
            ExecutionResult(data={"id": 1}, shape="object"),
            fmt="json",
        )
        assert_dual_channel(result, fmt="json")

    def test_raw_returns_valid_json_text(self) -> None:
        """format=raw returns valid JSON text content everywhere."""
        result = render(
            ExecutionResult(data={"id": 1}, shape="object"),
            fmt="raw",
        )
        assert_dual_channel(result, fmt="raw")
