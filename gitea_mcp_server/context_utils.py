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
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from fastmcp.dependencies import CurrentContext


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
    This helper silently degrades so observability is best-effort.

    Args:
        ctx: The MCP ``Context`` object, or ``None`` if no session is active.
        message: The log message (passed to ``ctx.info()``).
        **extra: Extra keyword arguments passed as ``extra`` to ``ctx.info()``.
    """
    if ctx is None:
        return
    with suppress(RuntimeError):
        await ctx.info(message, **extra)


async def safe_ctx_report_progress(
    ctx: Any | None,
    progress: float,
    total: float | None = None,
) -> None:
    """Call ``ctx.report_progress()`` if the MCP context and session are available.

    Same degradation pattern as :func:`safe_ctx_info` — progress reporting
    is best-effort, not guaranteed.

    Args:
        ctx: The MCP ``Context`` object, or ``None`` if no session is active.
        progress: Progress value between 0.0 and 1.0.
        total: Optional total value for multi-step progress reporting.
    """
    if ctx is None:
        return
    with suppress(RuntimeError):
        if total is not None:
            await ctx.report_progress(progress=progress, total=total)
        else:
            await ctx.report_progress(progress=progress)


__all__ = [
    "resolve_current_context",
    "safe_ctx_info",
    "safe_ctx_report_progress",
]
