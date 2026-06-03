"""Unit tests for pagination header capture via event hooks."""

import httpx
import pytest

from gitea_mcp_server.pagination import (
    PAGINATION_HEADERS,
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
