"""Safe MCP context helpers shared across the codebase.

Provides functions for calling ``ctx.info()`` and ``ctx.report_progress()``
that silently degrade when no active MCP session is available, and for
resolving the current MCP ``Context`` itself.  This is necessary because
``ctx.session`` raises ``RuntimeError`` when called outside an active
request scope (e.g. in unit tests via ``mcp.call_tool()`` or in-memory
``FastMCP`` usage), and ``CurrentContext()`` does the same when entered
outside a session.

These helpers are the single source of truth for safe context operations —
no module should implement its own ``RuntimeError`` guard around
``ctx.info()``, ``ctx.report_progress()``, or ``CurrentContext()``.

Both side-channel helpers are best-effort: **a progress or log failure never
aborts the call**.  ``RuntimeError`` (the framework's expected no-session
signal) degrades silently; any other ``Exception`` from the client/transport
side channel is traced at ``DEBUG`` and swallowed.  ``BaseException``
(cancellation, interrupt) is deliberately not caught, so a cancelled call stays
cancellable.

``safe_ctx_report_progress`` is consumed by the contract spine
(``tools/contract.py``) only: the spine owns the MCP progress lifecycle and
executors never report progress (#825).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastmcp.dependencies import CurrentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _best_effort(action: str) -> AsyncIterator[None]:
    """Run a best-effort context side channel; never fail the caller.

    ``RuntimeError`` is the framework's expected no-session signal (no active
    context, or a session that is gone) and degrades silently.  Any other
    exception is inconsequential to the call outcome, so it is traced at
    ``DEBUG`` and swallowed.  ``BaseException`` (cancellation, interrupt) is
    deliberately not caught.

    Args:
        action: The context operation, used in the ``DEBUG`` trace.
    """
    try:
        yield
    except RuntimeError:
        pass
    except Exception:  # noqa: BLE001 -- side channel: never fail the call
        logger.debug("%s failed; progress/logging is best-effort", action, exc_info=True)


async def resolve_current_context() -> Any | None:
    """Resolve the current MCP Context if inside a request scope.

    ``CurrentContext()`` raises ``RuntimeError`` when called outside an
    active MCP session (e.g. in unit tests or in-memory ``mcp.call_tool()``).
    This helper catches that and returns ``None``, matching the ``ctx=None``
    contract of the tool-execution pipeline: progress reporting and
    structured logging degrade gracefully when no session is active.

    Returns:
        The MCP ``Context`` object, or ``None`` if no session is active.
    """
    try:
        async with CurrentContext() as ctx:
            return ctx
    except RuntimeError:
        return None


async def safe_ctx_info(ctx: Any | None, message: str, **extra: Any) -> None:
    """Call ``ctx.info()`` if the MCP context and session are available.

    When called inside an in-memory ``mcp.call_tool()``, FastMCP provides
    a Context object whose ``session`` property raises ``RuntimeError``.
    Best-effort: every ``Exception`` from the call is swallowed (only
    ``BaseException`` propagates), so a log failure never aborts the call.

    Args:
        ctx: The MCP ``Context`` object, or ``None`` if no session is active.
        message: The log message (passed to ``ctx.info()``).
        **extra: Extra keyword arguments passed as ``extra`` to ``ctx.info()``.
    """
    if ctx is None:
        return
    async with _best_effort("ctx.info"):
        await ctx.info(message, **extra)


async def safe_ctx_report_progress(
    ctx: Any | None,
    progress: float,
    total: float | None = None,
) -> None:
    """Call ``ctx.report_progress()`` if the MCP context and session are available.

    Same best-effort contract as :func:`safe_ctx_info`: every ``Exception``
    from ``ctx.report_progress()`` is swallowed, so a progress failure never
    aborts the tool call; only ``BaseException`` (cancellation) propagates.
    The contract spine is the sole consumer.

    Args:
        ctx: The MCP ``Context`` object, or ``None`` if no session is active.
        progress: Progress value between 0.0 and 1.0.
        total: Optional total value for multi-step progress reporting.
    """
    if ctx is None:
        return
    async with _best_effort("ctx.report_progress"):
        if total is not None:
            await ctx.report_progress(progress=progress, total=total)
        else:
            await ctx.report_progress(progress=progress)


__all__ = [
    "resolve_current_context",
    "safe_ctx_info",
    "safe_ctx_report_progress",
]
