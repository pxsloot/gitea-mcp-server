"""Virtual parameters - tool-level params handled before the API call.

Virtual parameters appear in the tool schema so agents know they exist,
but are extracted from arguments before the HTTP request is made.  After
the API call completes, a registered *post-hook* transforms the result.
A registered *pre-hook* runs between extraction and the HTTP call.

Lifecycle for every tool call::

    1. inject_into(tool.parameters)     ← adds to schema at startup
    2. extract_from(kwargs)             ← pops before HTTP call
    3. apply_pre_hooks(extracted)       ← runs pre-hooks after extraction
    4. _pipeline_with_context(...)      ← HTTP call, pagination metadata,
       │                                   then loop hooks (re-execution
       │                                   with ``execute_fn``)
       └─ _apply_loop_hooks(...)
    5. apply_to(result, extracted)      ← runs post-hooks after call

Adding a new virtual parameter is a single registry entry -
no other file changes needed.

.. note::

    The ``format`` parameter is **not** implemented as a virtual param.
    It is promoted to a first-class concept handled directly in
    :func:`~gitea_mcp_server.server_setup.mcp_builder._ToolWrappingTransform._wrap`
    and reads its default from :attr:`Config.response_format
    <gitea_mcp_server.config.Config.response_format>`.
    See ``gitea_mcp_server/format.py`` for the shared utility.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastmcp.tools.base import ToolResult

from gitea_mcp_server.constants import FETCH_ALL_MAX_PAGES

logger = logging.getLogger(__name__)

_ExecuteFn = Callable[[dict[str, Any]], Awaitable[ToolResult]]
"""Type alias for the re-execution callable passed to loop_hooks.

An async function that accepts tool kwargs (with updated ``page``)
and returns a ``ToolResult`` from a fresh HTTP call.
"""

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class VirtualParam:
    """A parameter that lives in the tool schema but is handled pre-call.

    Attributes:
        schema: JSON Schema fragment for the parameter (type, enum, etc.).
        default: Default value used when the agent omits the parameter.
        description: Description shown to agents in the tool schema.
        visible: Whether to include this param in tool schemas.
            Set to ``False`` at startup for scope-gated params when the
            active token lacks the required scope.
        required_scope: Optional Gitea API scope string (e.g. ``"sudo"``)
            required for this parameter to be visible.  ``None`` (default)
            means no scope restriction — the parameter is always visible.
            At startup, :func:`apply_scope_filter` checks the active
            token's scopes and sets ``visible`` accordingly.
        pre_hook: Optional ``(value) → None`` callback invoked **after**
            the parameter is extracted from kwargs but **before** the HTTP
            request is made.  Useful for storing the value in a context
            variable that downstream layers (e.g. HTTP client hooks) can
            read.
        post_hook: Optional ``(ToolResult, value) → ToolResult`` callback
            invoked after the API call with the extracted value.
        loop_hook: Optional ``(result, value, kwargs, execute_fn) → ToolResult``
            callback invoked inside the execution pipeline **after** the
            HTTP call and pagination metadata have been produced, but
            **before** ``post_hook`` runs.

            ``result`` is the current ``ToolResult`` (with ``has_more``
            already set in ``structured_content``).  ``value`` is the
            extracted param value.  ``kwargs`` is the mutable tool
            arguments dict (unchanged since extraction).  ``execute_fn``
            is an async ``(dict) → ToolResult`` callable that re-invokes
            the HTTP execution path with updated kwargs — useful for
            auto-pagination loops.

            A loop_hook returns a new ``ToolResult``, typically with
            merged data and ``has_more=False``.

            .. important::

                The hook is responsible for its own termination (e.g. stop
                when a page returns fewer items than ``limit``).  There is
                no built-in iteration limit — a buggy hook could loop
                indefinitely.  Future consumers should document their
                termination strategy.
    """

    schema: dict[str, Any]
    default: Any
    description: str
    visible: bool = True
    required_scope: str | None = None
    pre_hook: Callable[[Any], None] | None = None
    post_hook: Callable[[ToolResult, Any], ToolResult] | None = None
    loop_hook: (
        Callable[[ToolResult, Any, dict[str, Any], _ExecuteFn], Awaitable[ToolResult]]
        | None
    ) = None


# Single source of truth for every virtual parameter.
# To add one: append an entry here.  inject_into / extract_from / apply_to
# pick it up automatically.
_VIRTUAL_PARAMS: dict[str, VirtualParam] = {}


# ---------------------------------------------------------------------------
# sudo - impersonate a user via ?sudo= query parameter
# ---------------------------------------------------------------------------

sudo_context: ContextVar[str | None] = ContextVar("sudo_context", default=None)
"""Async context variable carrying the target username for sudo.

Set by the sudo pre-hook before each tool call; read by the httpx request
hook in ``client.py`` to inject ``?sudo=<username>`` into the request URL.
Cleared by the sudo post-hook after the response.
"""


def _sudo_pre_hook(value: Any) -> None:
    """Store sudo target in context before the HTTP request."""
    if value is not None:
        sudo_context.set(str(value))


def _sudo_post_hook(result: ToolResult, _value: Any) -> ToolResult:
    """Clear sudo target from context after the request completes."""
    sudo_context.set(None)
    return result


# Register the sudo virtual param so it appears in every tool's schema.
# ``required_scope="sudo"`` means this param is hidden unless the active
# token has the ``sudo`` scope (or the ``all``-access token type).
_VIRTUAL_PARAMS["sudo"] = VirtualParam(
    schema={"type": "string", "minLength": 1},
    default=None,
    description=(
        "Impersonate a user.  Requires an admin token.  "
        "When set to a valid username, the Gitea API executes "
        'the request as that user.  Example: "alice"'
    ),
    required_scope="sudo",
    pre_hook=_sudo_pre_hook,
    post_hook=_sudo_post_hook,
)

# ---------------------------------------------------------------------------
# fetch_all — auto-pagination for list/search tools
# ---------------------------------------------------------------------------


async def _fetch_all_loop(
    result: ToolResult,
    value: Any,
    kwargs: dict[str, Any],
    execute_fn: _ExecuteFn,
) -> ToolResult:
    """Loop hook for ``fetch_all``: automatically fetch all pages.

    Called by ``_pipeline_with_context`` after the initial page has been
    fetched and pagination metadata added.  When ``fetch_all=true``, reads
    ``has_more`` / ``next_offset`` / ``total_count`` from the result's
    ``structured_content`` and re-invokes ``execute_fn`` for subsequent
    pages, merging array results into a single list.

    Termination (first wins):

    1. ``has_more`` is ``false`` on the most recent page.
    2. The most recent page returned fewer items than the page size (heuristic
       when ``total_count`` is unknown).
    3. ``FETCH_ALL_MAX_PAGES`` pages have been fetched (safety cap).

    Args:
        result: ``ToolResult`` from the first page (already has pagination
            metadata in ``structured_content``).
        value: The extracted ``fetch_all`` value — ``True`` to auto-paginate,
            ``False`` to passthrough.
        kwargs: Tool arguments (mutable; ``page`` is updated in-place when
            re-invoking ``execute_fn``).
        execute_fn: Async ``(dict) → ToolResult`` that re-invokes the HTTP
            execution path with updated kwargs.

    Returns:
        A ``ToolResult`` with merged ``result`` array, ``has_more=False``,
        ``next_offset=None``, and the most recent ``total_count``.
    """
    # Passthrough when fetch_all is not enabled.
    if not value:
        return result

    structured = result.structured_content
    if not structured:
        return result

    all_data = structured.get("result")
    if not isinstance(all_data, list):
        return result

    # Clone the accumulator so the original ToolResult is unmodified.
    merged_data = list(all_data)
    total_count = structured.get("total_count")
    page_size = kwargs.get("per_page") or kwargs.get("limit", 50)

    # next_offset tells us the next page to fetch (set by add_pagination_metadata).
    page = structured.get("next_offset")
    if page is None:
        # Single page only — nothing to fetch.
        return result

    fetched = 1  # first page already counted
    while fetched < FETCH_ALL_MAX_PAGES:
        has_more = structured.get("has_more", False)
        if not has_more:
            break

        kwargs["page"] = page
        next_result = await execute_fn(kwargs)
        next_sc = next_result.structured_content or {}
        next_data = next_sc.get("result")

        if isinstance(next_data, list):
            merged_data.extend(next_data)

        # Carry forward the server's total count (last-known wins).
        sc_total = next_sc.get("total_count")
        if sc_total is not None:
            total_count = sc_total

        # Use the response's has_more; fall back to the heuristic when
        # total_count is unknown (page shorter than limit means last page).
        next_has_more = next_sc.get("has_more")
        if next_has_more is None and isinstance(next_data, list):
            next_has_more = len(next_data) >= page_size

        page = next_sc.get("next_offset")
        if page is None:
            break

        structured = next_sc
        fetched += 1

    # Build the final structured content with all data merged.
    final_structured = dict(structured)
    final_structured["result"] = merged_data
    final_structured["has_more"] = False
    final_structured["next_offset"] = None
    final_structured["total_count"] = total_count

    return ToolResult(
        content=result.content,  # replaced by format_result after the hook
        structured_content=final_structured,
        meta=result.meta,
    )


# Register the fetch_all virtual param so it appears in every tool's schema.
_VIRTUAL_PARAMS["fetch_all"] = VirtualParam(
    schema={"type": "boolean"},
    default=False,
    description=(
        "When true, automatically fetch all pages of paginated results. "
        "Merges results into a single response. "
        f"Capped at {FETCH_ALL_MAX_PAGES} pages to prevent abuse."
    ),
    loop_hook=_fetch_all_loop,
)


# ---------------------------------------------------------------------------
# Scope-based visibility control
# ---------------------------------------------------------------------------

def apply_scope_filter(available_scopes: set[str]) -> None:
    """Set visibility on every virtual param based on the active token's scopes.

    Params with ``required_scope=None`` are always visible (left untouched).
    Params with a ``required_scope`` are hidden unless the active token
    has that scope or the ``"all"``-access shorthand (which implies every
    scope at write level).

    Call once at startup after fetching the active token's scopes, before
    :func:`inject_into` runs.

    Future extension: ``required_scope`` overrides could be sourced from
    an ``mcp_extensions.yaml`` or ``mcp_filter.yaml`` config file, letting
    operators adjust scope gating without code changes.

    Args:
        available_scopes: Set of scope strings from the active token.
    """
    for name, vp in _VIRTUAL_PARAMS.items():
        if vp.required_scope is None:
            continue
        vp.visible = (
            vp.required_scope in available_scopes
            or "all" in available_scopes
        )
        logger.info(
            "Scope filter: param '%s' %s (required_scope=%s)",
            name,
            "visible" if vp.visible else "hidden",
            vp.required_scope,
        )


# ---------------------------------------------------------------------------
# Lifecycle functions
# ---------------------------------------------------------------------------


def inject_into(parameters: dict[str, Any]) -> None:
    """Add every virtual parameter to *parameters* (a tool's parameter schema).

    Idempotent - skips any parameter name that already exists, which also
    guards against shadowing a real API parameter.

    Scope-gated params (those with a ``required_scope`` set) are only
    injected when the active token has the required scope - see
    :func:`apply_scope_filter`.
    """
    props = parameters.setdefault("properties", {})
    for name, vp in _VIRTUAL_PARAMS.items():
        if name not in props:
            # Skip params whose scope is not available (e.g. ``sudo``
            # when the active token lacks the ``sudo`` or ``all`` scope).
            if not vp.visible:
                continue
            props[name] = {
                **vp.schema,
                "default": vp.default,
                "description": vp.description,
            }


def extract_from(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Pop every virtual parameter from *kwargs*.

    Returns a ``{name: value}`` dict suitable for passing to :func:`apply_to`.

    .. note::

        Mutates *kwargs* in place so the remaining dict contains only real
        API parameters.  Call this **before** passing kwargs to the HTTP
        execution path.
    """
    return {n: kwargs.pop(n) for n in list(kwargs) if n in _VIRTUAL_PARAMS}


def apply_pre_hooks(extracted: dict[str, Any]) -> None:
    """Run pre-hooks for every extracted virtual parameter.

    Called between :func:`extract_from` and the HTTP execution path.
    Each pre-hook receives the extracted value and may have side effects
    (e.g. setting a context variable).
    """
    for name, value in extracted.items():
        hook = _VIRTUAL_PARAMS[name].pre_hook
        if hook is not None:
            hook(value)


def apply_to(
    result: ToolResult,
    extracted: dict[str, Any],
) -> ToolResult:
    """Run registered post-hooks for every extracted virtual parameter.

    Hooks are called in registration order (the same order as
    ``_VIRTUAL_PARAMS``).  Each receives the result from the previous hook.
    """
    for name, value in extracted.items():
        hook = _VIRTUAL_PARAMS[name].post_hook
        if hook is not None:
            result = hook(result, value)
    return result


def get_loop_hooks(
    extracted: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    """Resolve loop hooks from extracted virtual param values.

    Returns a ``{param_name: (value, loop_hook_callable)}`` dict for every
    extracted virtual parameter that has a ``loop_hook`` registered.
    Used by the execution pipeline (:func:`_pipeline_with_context`) to
    invoke re-execution hooks after the initial HTTP call.

    Args:
        extracted: The dict returned by :func:`extract_from`.

    Returns:
        Dict mapping param names to ``(extracted_value, callable)`` for
        params with a registered ``loop_hook``.  Empty dict if none.
    """
    hooks: dict[str, tuple[Any, Any]] = {}
    for name, value in extracted.items():
        vp = _VIRTUAL_PARAMS.get(name)
        if vp is not None and vp.loop_hook is not None:
            hooks[name] = (value, vp.loop_hook)
    return hooks


__all__ = [
    "VirtualParam",
    "_fetch_all_loop",
    "apply_pre_hooks",
    "apply_scope_filter",
    "apply_to",
    "extract_from",
    "get_loop_hooks",
    "inject_into",
    "sudo_context",
]
