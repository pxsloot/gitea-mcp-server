"""Unit tests for pagination header capture via event hooks and PaginationRunner."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp.tools.base import ToolResult

from gitea_mcp_server.constants import FETCH_ALL_MAX_PAGES
from gitea_mcp_server.pagination import (
    PAGINATION_HEADERS,
    PaginationRunner,
    add_pagination_metadata,
    capture_pagination_headers,
    pagination_ctx,
)


@pytest.fixture(autouse=True)
def reset_context():
    """Reset pagination context before each test."""
    pagination_ctx.set({})


def _make_response(
    status_code: int = 200,
    headers: dict | None = None,
    content_type: str = "application/json",
) -> httpx.Response:
    full_headers = {"content-type": content_type, **(headers or {})}
    return httpx.Response(
        status_code=status_code,
        headers=full_headers,
        content=b"[]",
    )


class TestCapturePaginationHeaders:
    """Tests for capture_pagination_headers event hook."""

    @pytest.mark.asyncio
    async def test_captures_x_total_count(self):
        """X-Total-Count header should be captured into context."""
        response = _make_response(headers={"X-Total-Count": "42"})
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {"total_count": 42}

    @pytest.mark.asyncio
    async def test_falls_back_to_x_total(self):
        """X-Total should be used when X-Total-Count is absent."""
        response = _make_response(headers={"X-Total": "99"})
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {"total_count": 99}

    @pytest.mark.asyncio
    async def test_x_total_count_takes_priority(self):
        """X-Total-Count takes priority over X-Total when both present."""
        response = _make_response(headers={"X-Total-Count": "50", "X-Total": "99"})
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {"total_count": 50}

    @pytest.mark.asyncio
    async def test_skips_error_responses(self):
        """Error responses (4xx/5xx) should not capture headers."""
        response = _make_response(status_code=404, headers={"X-Total-Count": "42"})
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {}

    @pytest.mark.asyncio
    async def test_skips_3xx_responses(self):
        """3xx redirect responses should not capture headers."""
        response = _make_response(status_code=304, headers={"X-Total-Count": "10"})
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {}

    @pytest.mark.asyncio
    async def test_ignores_non_integer_values(self):
        """Non-integer header values should be silently ignored."""
        response = _make_response(headers={"X-Total-Count": "not-a-number"})
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {}

    @pytest.mark.asyncio
    async def test_no_headers_no_change(self):
        """Context should remain default when no pagination headers present."""
        response = _make_response()
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {}

    @pytest.mark.asyncio
    async def test_context_isolation(self):
        """Each task should have its own context (smoke test for concurrency)."""
        pagination_ctx.set({"total_count": 1})
        response = _make_response(headers={"X-Total-Count": "99"})
        await capture_pagination_headers(response)
        assert pagination_ctx.get() == {"total_count": 99}


class TestPaginationHeadersConstant:
    """Tests for the PAGINATION_HEADERS constant."""

    def test_order(self):
        """X-Total-Count should be checked before X-Total."""
        assert PAGINATION_HEADERS == ("X-Total-Count", "X-Total")


class TestAddPaginationMetadata:
    """Tests for add_pagination_metadata helper."""

    def test_with_total_count_has_more_true(self):
        """When total_count is known and there are more pages, has_more=True."""
        result = add_pagination_metadata({"result": [1, 2, 3]}, page=1, limit=10, total_count=42)
        assert result["has_more"] is True
        assert result["next_offset"] == 2
        assert result["total_count"] == 42

    def test_with_total_count_has_more_false(self):
        """When total_count is known and we're past the last page, has_more=False."""
        result = add_pagination_metadata({"result": [1, 2]}, page=5, limit=10, total_count=42)
        assert result["has_more"] is False
        assert result["next_offset"] is None
        assert result["total_count"] == 42

    def test_with_total_count_exact_last_page(self):
        """When page*limit == total_count, has_more=False."""
        result = add_pagination_metadata({"result": [1, 2]}, page=5, limit=10, total_count=50)
        # page=5, limit=10 → page*limit = 50 which equals total_count → no more
        assert result["has_more"] is False
        assert result["next_offset"] is None
        assert result["total_count"] == 50

    def test_no_total_count_heuristic_true(self):
        """Without total_count, has_more=True when len(result) == limit."""
        result = add_pagination_metadata({"result": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}, page=1, limit=10)
        assert result["has_more"] is True
        assert result["next_offset"] == 2
        assert result["total_count"] is None

    def test_no_total_count_heuristic_false(self):
        """Without total_count, has_more=False when len(result) < limit."""
        result = add_pagination_metadata({"result": [1, 2, 3]}, page=1, limit=10)
        assert result["has_more"] is False
        assert result["next_offset"] is None
        assert result["total_count"] is None

    def test_non_list_result_no_total_count(self):
        """When result is not a list and total_count is None, has_more=False."""
        result = add_pagination_metadata({"result": {"id": 1}}, page=1, limit=10)
        assert result["has_more"] is False
        assert result["next_offset"] is None
        assert result["total_count"] is None

    def test_preserves_existing_keys(self):
        """Existing keys in structured_content should be preserved."""
        result = add_pagination_metadata({"result": [1], "foo": "bar"}, page=1, limit=10, total_count=1)
        assert result["foo"] == "bar"
        assert result["has_more"] is False

    def test_zero_total_count(self):
        """When total_count is 0, has_more=False and next_offset=None."""
        result = add_pagination_metadata({"result": []}, page=1, limit=10, total_count=0)
        assert result["has_more"] is False
        assert result["next_offset"] is None
        assert result["total_count"] == 0

    def test_missing_result_key(self):
        """When result key is missing, should still add pagination fields."""
        result = add_pagination_metadata({"foo": "bar"}, page=1, limit=10, total_count=5)
        # page=1, limit=10 → 1*10=10 > total_count=5 → has_more=False
        assert result["has_more"] is False
        assert result["next_offset"] is None
        assert result["total_count"] == 5
        assert result["foo"] == "bar"


# ============================================================================
# PaginationRunner tests
# ============================================================================


class TestPaginationRunner:
    """Tests for PaginationRunner (loop-based auto-pagination for API tools)."""

    @pytest.mark.asyncio
    async def test_fetch_all_false_passthrough(self):
        """When fetch_all=False, PaginationRunner is not used."""
        fetch_fn = AsyncMock()
        runner = PaginationRunner(fetch_fn)
        result = ToolResult(structured_content={"result": [{"id": 1}]})
        output = await runner.run(result, {"page": 1})
        assert output.structured_content["result"] == [{"id": 1}]
        fetch_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_page_has_more_false(self):
        """When has_more is False on the first page, no extra fetches."""
        fetch_fn = AsyncMock()
        runner = PaginationRunner(fetch_fn)
        result = ToolResult(
            structured_content={
                "result": [{"id": 1}, {"id": 2}],
                "has_more": False,
                "next_offset": None,
                "total_count": 2,
            },
        )
        output = await runner.run(result, {"page": 1, "limit": 10})
        assert output.structured_content["result"] == [{"id": 1}, {"id": 2}]
        assert output.structured_content["has_more"] is False
        fetch_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_pages_merged(self):
        """Two pages merged when has_more is True initially."""
        page_calls = []

        async def _fetch(kwargs):
            page_calls.append(kwargs["page"])
            return ToolResult(
                structured_content={
                    "result": [{"id": 3}, {"id": 4}],
                    "has_more": False,
                    "next_offset": None,
                    "total_count": 4,
                },
            )

        runner = PaginationRunner(_fetch)
        result = ToolResult(
            structured_content={
                "result": [{"id": 1}, {"id": 2}],
                "has_more": True,
                "next_offset": 2,
                "total_count": 4,
            },
        )
        output = await runner.run(result, {"page": 1, "limit": 2})
        assert output.structured_content["result"] == [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
        assert output.structured_content["has_more"] is False
        assert output.structured_content["total_count"] == 4
        assert page_calls == [2]

    @pytest.mark.asyncio
    async def test_three_pages_with_total_count(self):
        """Three pages merged with total_count preserved."""
        page_data = {
            1: [{"id": 1}, {"id": 2}],
            2: [{"id": 3}, {"id": 4}],
            3: [{"id": 5}],
        }

        async def _fetch(kwargs):
            p = kwargs["page"]
            items = page_data.get(p, [])
            return ToolResult(
                structured_content={
                    "result": items,
                    "has_more": p < 3,
                    "next_offset": p + 1 if p < 3 else None,
                    "total_count": 5,
                },
            )

        runner = PaginationRunner(_fetch)
        result = ToolResult(
            structured_content={
                "result": page_data[1],
                "has_more": True,
                "next_offset": 2,
                "total_count": 5,
            },
        )
        output = await runner.run(result, {"page": 1, "limit": 2})
        assert len(output.structured_content["result"]) == 5
        assert output.structured_content["has_more"] is False
        assert output.structured_content["total_count"] == 5

    @pytest.mark.asyncio
    async def test_max_pages_cap(self):
        """Stops after FETCH_ALL_MAX_PAGES pages (safety cap)."""
        many_items = [{"id": i} for i in range(10)]
        call_count = 0

        async def _never_ending(kwargs):
            nonlocal call_count
            call_count += 1
            return ToolResult(
                structured_content={
                    "result": many_items,
                    "has_more": True,
                    "next_offset": kwargs["page"] + 1,
                    "total_count": 9999,
                },
            )

        runner = PaginationRunner(_never_ending)
        result = ToolResult(
            structured_content={
                "result": many_items,
                "has_more": True,
                "next_offset": 2,
                "total_count": 9999,
            },
        )
        output = await runner.run(result, {"page": 1, "limit": 10})
        # First page + FETCH_ALL_MAX_PAGES - 1 additional calls
        assert call_count == FETCH_ALL_MAX_PAGES - 1
        total_items = 10 * FETCH_ALL_MAX_PAGES
        assert len(output.structured_content["result"]) == total_items
        assert output.structured_content["has_more"] is False
        assert output.structured_content["total_count"] == 9999

    @pytest.mark.asyncio
    async def test_heuristic_when_has_more_missing(self):
        """Fall back to heuristic when response has no has_more."""
        async def _short_page(_kwargs):
            return ToolResult(
                structured_content={
                    "result": [{"id": 3}],
                    "total_count": None,
                },
            )

        runner = PaginationRunner(_short_page)
        result = ToolResult(
            structured_content={
                "result": [{"id": 1}, {"id": 2}],
                "has_more": True,
                "next_offset": 2,
                "total_count": None,
            },
        )
        output = await runner.run(result, {"page": 1, "limit": 10})
        assert output.structured_content["result"] == [{"id": 1}, {"id": 2}, {"id": 3}]
        assert output.structured_content["has_more"] is False

    @pytest.mark.asyncio
    async def test_total_count_carried_forward(self):
        """total_count from server response is preserved in merged result."""
        async def _fetch(_kwargs):
            return ToolResult(
                structured_content={
                    "result": [{"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}],
                    "has_more": False,
                    "next_offset": None,
                    "total_count": 5,
                },
            )

        runner = PaginationRunner(_fetch)
        result = ToolResult(
            structured_content={
                "result": [{"id": 1}],
                "has_more": True,
                "next_offset": 2,
                "total_count": 5,
            },
        )
        output = await runner.run(result, {"page": 1, "limit": 10})
        assert output.structured_content["total_count"] == 5
        assert len(output.structured_content["result"]) == 5

    @pytest.mark.asyncio
    async def test_non_list_result_passthrough(self):
        """When result is not a list, PaginationRunner returns unchanged."""
        fetch_fn = AsyncMock()
        runner = PaginationRunner(fetch_fn)
        result = ToolResult(structured_content={"result": {"id": 1}})
        output = await runner.run(result, {"page": 1})
        assert output.structured_content["result"] == {"id": 1}
        fetch_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_structured_content_passthrough(self):
        """When structured_content is None, PaginationRunner returns unchanged."""
        fetch_fn = AsyncMock()
        runner = PaginationRunner(fetch_fn)
        result = ToolResult(content=[], structured_content=None)
        output = await runner.run(result, {"page": 1})
        assert output.structured_content is None
        fetch_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_preserves_meta(self):
        """PaginationRunner preserves meta from the original ToolResult."""
        async def _fetch(_kwargs):
            return ToolResult(structured_content={"result": [{"id": 2}], "has_more": False})

        runner = PaginationRunner(_fetch)
        result = ToolResult(
            content=[],
            structured_content={
                "result": [{"id": 1}],
                "has_more": True,
                "next_offset": 2,
            },
            meta={"custom": True},
        )
        output = await runner.run(result, {"page": 1, "limit": 10})
        assert output.meta == {"custom": True}
