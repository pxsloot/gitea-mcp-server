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
    """MCP context stand-in recording progress and test markers in call order.

    ``calls`` is the ordered log.  ``progress`` extracts the progress
    notifications (integration tests assert on that); ``mark`` lets a unit test
    interleave its own events — e.g. "executor ran" — so it can pin the
    start-before / terminal-after ordering, not just the set of signals.

    ``progress_error`` makes ``report_progress`` record the attempt and then
    raise, standing in for a client/transport failure.  The spine's best-effort
    contract (#827) must absorb it: the call still completes.
    """

    def __init__(self, progress_error: BaseException | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._progress_error = progress_error

    async def report_progress(self, progress: float, total: float | None = None) -> None:
        self.calls.append(("progress", progress, total))
        if self._progress_error is not None:
            raise self._progress_error

    async def info(self, *args: Any, **kwargs: Any) -> None:
        pass

    def mark(self, label: str) -> None:
        """Append an arbitrary marker to the ordered call log (test-only)."""
        self.calls.append(("mark", label))

    @property
    def progress(self) -> list[tuple[float, float | None]]:
        """The progress notifications, in call order."""
        return [(call[1], call[2]) for call in self.calls if call[0] == "progress"]


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
