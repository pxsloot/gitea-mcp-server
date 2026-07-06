"""Unit tests for pagination header capture via event hooks."""

import httpx
import pytest

from gitea_mcp_server.pagination import (
    PAGINATION_HEADERS,
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
