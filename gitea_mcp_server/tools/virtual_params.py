"""Virtual parameters - tool-level params handled before the API call.

Virtual parameters appear in the tool schema so agents know they exist,
but are extracted from arguments before the HTTP request is made.  After
the API call completes, a registered *post-hook* transforms the result.
A registered *pre-hook* runs between extraction and the HTTP call and
may mutate the remaining kwargs.

Lifecycle for every tool call::

    1. inject_into(tool.parameters, tool=tool)  ← adds to schema at startup
    2. extract_from(kwargs)                     ← pops before HTTP call
    3. apply_pre_hooks(extracted, kwargs)       ← runs pre-hooks (may mutate kwargs)
    4. _pipeline_with_context(...)      ← HTTP call, pagination metadata,
       │                                   then loop hooks (re-execution
       │                                   with ``execute_fn``)
       └─ _apply_loop_hooks(...)
    5. apply_to(result, extracted)      ← runs post-hooks after call

Adding a new virtual parameter is a single registry entry -
no other file changes needed (unless the param is tool-gated via
``tool_predicate`` — then the injection call site must pass ``tool``)."""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastmcp.tools.base import ToolResult

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
        pre_hook: Optional ``(value, kwargs) → None`` callback invoked
            **after** the parameter is extracted from kwargs but **before**
            the HTTP request is made.  Receives the extracted value and
            the mutable kwargs dict — hooks may modify kwargs to transform
            arguments before they reach the API.  Useful for storing context
            vars (``sudo``) or encoding arguments (``content_type``).
        post_hook: Optional ``(result, value, all_extracted) → ToolResult``
            callback invoked after the API call.  Receives (1) the current
            ``ToolResult``, (2) the extracted value, and (3) the full
            extracted dict — hooks can read other virtual params (e.g.
            ``format`` reads ``detail`` from the dict).
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
    tool_predicate: Callable[[Any], bool] | None = None
    """When set, the param is only injected into tools where this returns True.

    Receives the :class:`~fastmcp.tools.base.Tool` object being wrapped.
    ``None`` (default) means the param is injected into every tool.
    """
    pre_hook: Callable[[Any, dict[str, Any]], None] | None = None
    post_hook: Callable[[ToolResult, Any, dict[str, Any]], ToolResult] | None = None
    """Optional ``(result, value, all_extracted) → ToolResult`` callback
        invoked after the API call with (1) the current ``ToolResult``,
        (2) the extracted value, and (3) the full extracted dict so hooks
        can read other virtual params (e.g. ``format`` reads ``detail``).
    """
    loop_hook: (
        Callable[[ToolResult, Any, dict[str, Any], _ExecuteFn], Awaitable[ToolResult]] | None
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


def _sudo_pre_hook(value: Any, _kwargs: dict[str, Any]) -> None:
    """Store sudo target in context before the HTTP request."""
    if value is not None:
        sudo_context.set(str(value))


def _sudo_post_hook(result: ToolResult, _value: Any, _all_extracted: dict[str, Any]) -> ToolResult:
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

    Thin wrapper around :class:`~gitea_mcp_server.pagination.PaginationRunner`.

    Called by ``_pipeline_with_context`` after the initial page has been
    fetched and pagination metadata added.  When ``fetch_all=true``, delegates
    to ``PaginationRunner`` which handles the loop, merge, and termination.

    Termination (via ``PaginationRunner``, first wins):

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

    from gitea_mcp_server.pagination import PaginationRunner  # noqa: PLC0415

    runner = PaginationRunner(execute_fn)
    return await runner.run(result, kwargs)


# Register the fetch_all virtual param so it appears in every tool's schema.
# The description is deliberately family-agnostic: for API tools it paginates
# through every page (capped at FETCH_ALL_MAX_PAGES — documented in
# agent_instructions.md), for synthetic tools it returns all in-memory
# results without a loop.  One shared text covers both.
_VIRTUAL_PARAMS["fetch_all"] = VirtualParam(
    schema={"type": "boolean"},
    default=False,
    description=(
        "When true, automatically fetch all matching results and merge "
        "them into a single response.  For API tools this paginates "
        "through every page of the endpoint."
    ),
    loop_hook=_fetch_all_loop,
)


# ---------------------------------------------------------------------------
# content_type — text/base64 encoding for file content tools
# ---------------------------------------------------------------------------


def _content_type_pre_hook(value: Any, kwargs: dict[str, Any]) -> None:
    """Base64-encode the ``content`` argument when ``content_type="text"``.

    Gitea's file create/update endpoints require base64-encoded content
    on the wire.  When ``content_type="text"``, the server encodes the
    plain-text ``content`` argument to base64 before the API call so
    agents can pass human-readable strings.
    """
    if value == "text" and "content" in kwargs:
        raw = kwargs.get("content")
        if isinstance(raw, str):
            kwargs["content"] = base64.b64encode(raw.encode()).decode()


# Register the content_type virtual param for file create/update tools.
# ``tool_predicate`` gates injection to tools that have a ``content`` body
# parameter — this prevents the param from appearing on all ~400 tools.
_VIRTUAL_PARAMS["content_type"] = VirtualParam(
    schema={"type": "string", "enum": ["base64", "text"]},
    default="base64",
    description=(
        "How the ``content`` parameter is interpreted.  "
        '"base64" (default) — content is already base64-encoded '
        "(Gitea API native).  "
        '"text" — content is plain text; the server encodes it '
        "to base64 before calling the Gitea API."
    ),
    tool_predicate=lambda t: "content" in (t.parameters.get("properties", {})),
    pre_hook=_content_type_pre_hook,
)


# ---------------------------------------------------------------------------
# format / detail — output rendering control
# ---------------------------------------------------------------------------


def _format_post_hook(
    result: ToolResult,
    value: str,
    all_extracted: dict[str, Any],
) -> ToolResult:
    """Apply response formatting (json/markdown/raw) with optional detail.

    ``value`` is the ``format`` value.  ``detail`` and ``_raw_schema``
    are read from ``all_extracted`` — ``detail`` is a companion
    VirtualParam, ``_raw_schema`` is pipeline metadata attached by
    the transform_fn before ``apply_to`` runs.

    Results already rendered by their executor (marked ``_formatted`` in
    ``result.meta``) pass through unchanged — synthetic executors render
    inline when the output shape is bespoke (markdown extras, custom
    formatters), so the shared post-hook must not re-render them.
    """
    if value == "raw":
        return result
    if (result.meta or {}).get("_formatted"):
        return result

    detail: str = all_extracted.get("detail", "full")
    raw_schema = all_extracted.get("_raw_schema")
    data = result.structured_content.get("result") if result.structured_content else None
    if data is None:
        return result

    from gitea_mcp_server.format import apply_format  # noqa: PLC0415

    formatted = apply_format(data, value, detail=detail, schema=raw_schema)
    # Preserve original structured_content (carries pagination metadata
    # and uncollapsed data for programmatic access).
    formatted.structured_content = result.structured_content
    formatted.meta = result.meta
    return formatted


# ``detail`` registered first so it's in the extracted dict before
# ``format``'s post_hook runs.  No hooks — it just needs to be present
# in ``all_extracted`` for ``_format_post_hook`` to read.
_VIRTUAL_PARAMS["detail"] = VirtualParam(
    schema={"type": "string", "enum": ["full", "concise"]},
    default="full",
    description=(
        'Output detail level.  "full" (default) — complete information. '
        '"concise" — nested objects collapsed to ``$ref:TypeName`` labels.'
    ),
)

_VIRTUAL_PARAMS["format"] = VirtualParam(
    schema={"type": "string", "enum": ["json", "markdown", "raw"]},
    default="markdown",
    description=(
        "Response format control.  "
        '"json" — raw JSON.  '
        '"markdown" — formatted tables for human/agent reading.  '
        '"raw" — unprocessed API response.'
    ),
    post_hook=_format_post_hook,
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
        vp.visible = vp.required_scope in available_scopes or "all" in available_scopes
        logger.info(
            "Scope filter: param '%s' %s (required_scope=%s)",
            name,
            "visible" if vp.visible else "hidden",
            vp.required_scope,
        )


# ---------------------------------------------------------------------------
# Lifecycle functions
# ---------------------------------------------------------------------------


def inject_into(
    parameters: dict[str, Any],
    tool: Any | None = None,
    default_overrides: dict[str, Any] | None = None,
    only: set[str] | None = None,
) -> None:
    """Add virtual parameters to *parameters* (a tool's parameter schema).

    Idempotent - skips any parameter name that already exists, which also
    guards against shadowing a real API parameter.

    Scope-gated params (those with a ``required_scope`` set) are only
    injected when the active token has the required scope - see
    :func:`apply_scope_filter`.

    Per-tool gating via ``tool_predicate``: when set, the param is only
    injected into tools where the predicate returns ``True``.  Pass the
    :class:`~fastmcp.tools.base.Tool` object as *tool* to enable this.

    Per-tool allowlist via *only*: when set, only the named params are
    considered.  Autogen tools pass ``None`` (inject every visible param,
    skipping names that already exist — never shadowing a real API
    parameter).  Synthetic tools stamp their allowlist in
    ``tool.meta["_virtual_params"]``; allowlisted names are **overwritten**
    with the registry's schema so the agent-facing description/enum/default
    come from the single registry source rather than hand-written signature
    annotations (e.g. ``read_doc`` opts into ``format`` only, and ``sudo``
    is opt-in).

    Args:
        parameters: Tool parameter schema dict (mutated in place).
        tool: The Tool being wrapped, for ``tool_predicate`` gating.
        default_overrides: Optional ``{param_name: value}`` dict of
            defaults to overwrite after injection.  Use for params whose
            default is dynamic (e.g. ``format``'s default comes from
            server config, not the registry).
        only: Optional allowlist of param names to inject/overwrite.
            ``None`` injects every visible param, skipping existing names
            (autogen behavior).
    """
    props = parameters.setdefault("properties", {})
    for name, vp in _VIRTUAL_PARAMS.items():
        if only is not None and name not in only:
            continue
        # Skip params whose scope is not available (e.g. ``sudo`` when the
        # active token lacks the ``sudo`` or ``all`` scope).
        if not vp.visible:
            continue
        if vp.tool_predicate and tool is not None and not vp.tool_predicate(tool):
            continue
        if name in props and only is None:
            # Autogen: never shadow a real API parameter.
            continue
        props[name] = {
            **vp.schema,
            "default": vp.default,
            "description": vp.description,
        }

    # Apply caller-specified default overrides (e.g. format's default
    # comes from server config, not the static registry default).
    if default_overrides:
        for name, value in default_overrides.items():
            if name in props:
                props[name]["default"] = value


def extract_from(
    kwargs: dict[str, Any],
    only: set[str] | None = None,
) -> dict[str, Any]:
    """Pop virtual parameters from *kwargs*.

    With *only* (a per-tool allowlist, e.g. synthetic tools' ``_virtual_params``
    from ``tool.meta``), only the named parameters are popped; other
    registry-name keys stay in *kwargs* so the validation layer rejects them
    as unknown (they are neither allowlisted nor declared in the tool's
    schema — the value would otherwise be silently dropped).  Without *only*
    (autogenerated tools, which inject every visible parameter), every
    virtual parameter is popped.

    Returns a ``{name: value}`` dict suitable for passing to :func:`apply_to`.

    .. note::

        Mutates *kwargs* in place so the remaining dict contains only real
        API parameters.  Call this **before** passing kwargs to the HTTP
        execution path.
    """
    if only is not None:
        return {n: kwargs.pop(n) for n in list(kwargs) if n in _VIRTUAL_PARAMS and n in only}
    return {n: kwargs.pop(n) for n in list(kwargs) if n in _VIRTUAL_PARAMS}


def apply_pre_hooks(extracted: dict[str, Any], kwargs: dict[str, Any] | None = None) -> None:
    """Run pre-hooks for every extracted virtual parameter.

    Called between :func:`extract_from` and the HTTP execution path.
    Each pre-hook receives ``(value, kwargs)`` — the extracted value and
    the mutable tool arguments dict.  Hooks may modify kwargs to transform
    arguments before they reach the API (e.g. ``content_type`` base64-encodes
    ``content``).

    When *kwargs* is ``None`` (backward compatibility with direct callers
    that don't need kwarg mutation), hooks receive an empty dict.
    """
    kw = kwargs or {}
    for name, value in extracted.items():
        hook = _VIRTUAL_PARAMS[name].pre_hook
        if hook is not None:
            hook(value, kw)


def apply_to(
    result: ToolResult,
    extracted: dict[str, Any],
) -> ToolResult:
    """Run registered post-hooks for every extracted virtual parameter.

    Hooks are called in registration order (the same order as
    ``_VIRTUAL_PARAMS``).  Each receives ``(result, value, all_extracted)``
    — the full extracted dict, which may contain non-VirtualParam pipeline
    metadata (e.g. ``_raw_schema``) for hooks to read.
    """
    for name, value in extracted.items():
        vp = _VIRTUAL_PARAMS.get(name)
        if vp is None or vp.post_hook is None:
            continue
        result = vp.post_hook(result, value, extracted)
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
    "apply_pre_hooks",
    "apply_scope_filter",
    "apply_to",
    "extract_from",
    "get_loop_hooks",
    "inject_into",
    "sudo_context",
]
