"""Helpers for observing the MCP progress lifecycle in tests (#825).

The contract spine emits a start and a terminal progress notification on every
wrapped tool call.  Integration tests patch the MCP context so the spine's
``context_utils.resolve_current_context`` resolves a recording stand-in, then
assert the observed lifecycle.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Iterator


class RecordingContext:
    """MCP context stand-in that records ``report_progress`` calls in order."""

    def __init__(self) -> None:
        self.progress: list[tuple[float, float | None]] = []

    async def report_progress(self, progress: float, total: float | None = None) -> None:
        self.progress.append((progress, total))

    async def info(self, *args: Any, **kwargs: Any) -> None:
        pass


@contextmanager
def current_context(ctx: RecordingContext) -> Iterator[None]:
    """Patch ``context_utils.CurrentContext`` so the spine resolves *ctx*.

    ``resolve_current_context`` enters ``CurrentContext()`` and returns it, so
    the patched factory yields *ctx* on every entry (once per tool call).
    """

    class _CtxManager:
        async def __aenter__(self) -> RecordingContext:
            return ctx

        async def __aexit__(self, *args: Any) -> None:
            pass

    with patch("gitea_mcp_server.context_utils.CurrentContext", return_value=_CtxManager()):
        yield


__all__ = ["RecordingContext", "current_context"]
