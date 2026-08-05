"""Safe MCP context helpers shared across the codebase.

Provides functions for calling ``ctx.info()`` and ``ctx.report_progress()``
that silently degrade when no active MCP session is available.  This is
necessary because ``ctx.session`` raises ``RuntimeError`` when called
outside an active request scope (e.g. in unit tests via ``mcp.call_tool()``
or in-memory ``FastMCP`` usage).

These helpers are the single source of truth for safe context operations —
no module should implement its own ``RuntimeError`` guard around
``ctx.info()`` or ``ctx.report_progress()``.

Consumers:
    - ``server_setup/mcp_builder.py`` — ``_ToolWrappingTransform``
    - ``label_service.py`` — ``LabelService._log_ctx_info``
    - ``tools/type_info.py`` — ``resolve_type`` tool + resource
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)


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
