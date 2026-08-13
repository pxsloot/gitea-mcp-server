"""Tests for gitea_mcp_server.context_utils — safe MCP context helpers."""

from unittest.mock import AsyncMock

import pytest

from gitea_mcp_server.context_utils import safe_ctx_info, safe_ctx_report_progress


class TestSafeCtxInfo:
    """Tests for safe_ctx_info."""

    @pytest.mark.asyncio
    async def test_happy_path_calls_ctx_info(self) -> None:
        """Calls ctx.info() when context and session are available."""
        ctx = AsyncMock()
        await safe_ctx_info(ctx, "test message", extra={"key": "val"})
        ctx.info.assert_awaited_once_with("test message", extra={"key": "val"})

    @pytest.mark.asyncio
    async def test_ctx_none_no_op(self) -> None:
        """Does nothing when ctx is None."""
        await safe_ctx_info(None, "test message")

    @pytest.mark.asyncio
    async def test_suppresses_runtime_error(self) -> None:
        """Suppresses RuntimeError from ctx.info() gracefully."""
        ctx = AsyncMock()
        ctx.info.side_effect = RuntimeError("session not available")

        # Should not raise
        await safe_ctx_info(ctx, "test message")
        ctx.info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_other_exceptions(self) -> None:
        """Only RuntimeError is suppressed; other exceptions propagate."""
        ctx = AsyncMock()
        ctx.info.side_effect = ValueError("unexpected error")

        with pytest.raises(ValueError, match="unexpected error"):
            await safe_ctx_info(ctx, "test message")
        ctx.info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_extra_kwargs(self) -> None:
        """Extra keyword arguments are forwarded to ctx.info()."""
        ctx = AsyncMock()
        await safe_ctx_info(ctx, "msg", a=1, b="two", extra={"nested": True})
        ctx.info.assert_awaited_once_with("msg", a=1, b="two", extra={"nested": True})


class TestSafeCtxReportProgress:
    """Tests for safe_ctx_report_progress."""

    @pytest.mark.asyncio
    async def test_happy_path_no_total(self) -> None:
        """Calls ctx.report_progress(progress) when total is None."""
        ctx = AsyncMock()
        await safe_ctx_report_progress(ctx, progress=0.5)
        ctx.report_progress.assert_awaited_once_with(progress=0.5)

    @pytest.mark.asyncio
    async def test_happy_path_with_total(self) -> None:
        """Calls ctx.report_progress(progress, total) when total is provided."""
        ctx = AsyncMock()
        await safe_ctx_report_progress(ctx, progress=0.75, total=1.0)
        ctx.report_progress.assert_awaited_once_with(progress=0.75, total=1.0)

    @pytest.mark.asyncio
    async def test_ctx_none_no_op(self) -> None:
        """Does nothing when ctx is None."""
        await safe_ctx_report_progress(None, progress=1.0)

    @pytest.mark.asyncio
    async def test_ctx_none_no_op_with_total(self) -> None:
        """Does nothing when ctx is None, even with total."""
        await safe_ctx_report_progress(None, progress=1.0, total=1.0)

    @pytest.mark.asyncio
    async def test_suppresses_runtime_error(self) -> None:
        """Suppresses RuntimeError from ctx.report_progress() gracefully."""
        ctx = AsyncMock()
        ctx.report_progress.side_effect = RuntimeError("session not available")

        # Should not raise
        await safe_ctx_report_progress(ctx, progress=0.5)
        ctx.report_progress.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_other_exceptions(self) -> None:
        """Only RuntimeError is suppressed; other exceptions propagate."""
        ctx = AsyncMock()
        ctx.report_progress.side_effect = ValueError("bad progress")

        with pytest.raises(ValueError, match="bad progress"):
            await safe_ctx_report_progress(ctx, progress=0.5)
        ctx.report_progress.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_suppresses_runtime_error_with_total(self) -> None:
        """Suppresses RuntimeError when total is provided too."""
        ctx = AsyncMock()
        ctx.report_progress.side_effect = RuntimeError("nope")

        # Should not raise
        await safe_ctx_report_progress(ctx, progress=0.5, total=1.0)
        ctx.report_progress.assert_awaited_once_with(progress=0.5, total=1.0)


class TestResolveCurrentContext:
    """Tests for resolve_current_context."""

    @pytest.mark.asyncio
    async def test_returns_none_outside_session(self) -> None:
        """Returns None when no MCP session is active (RuntimeError suppressed)."""
        from gitea_mcp_server.context_utils import resolve_current_context

        ctx = await resolve_current_context()
        assert ctx is None

    @pytest.mark.asyncio
    async def test_returns_context_inside_session(self) -> None:
        """Returns the Context object when a session is active."""
        from unittest.mock import patch

        from gitea_mcp_server import context_utils
        from gitea_mcp_server.context_utils import resolve_current_context

        mock_ctx = AsyncMock()

        class _MockCurrentContext:
            async def __aenter__(self) -> AsyncMock:
                return mock_ctx

            async def __aexit__(self, *args: object) -> None:
                pass

        with patch.object(context_utils, "CurrentContext", return_value=_MockCurrentContext()):
            resolved = await resolve_current_context()

        assert resolved is mock_ctx
