"""Virtual parameters - tool-level params handled before the API call.

Virtual parameters appear in the tool schema so agents know they exist,
but are extracted from arguments before the HTTP request is made.  After
the API call completes, a registered *post-hook* transforms the result.
A registered *pre-hook* runs between extraction and the HTTP call and
may mutate the remaining kwargs.

The registry serves two roles:

- **Pre-request virtual params** (``sudo``, ``content_type``) — params with
  hooks that run before/after the HTTP call (context-var setup, argument
  encoding).
- **Pipeline options** (``format``, ``detail``, ``fetch_all``) — passive
  schema + extraction entries with no hooks.  They are display concerns;
  the single result pipeline (``tools/result_pipeline.py``) reads them from
  the extracted dict and renders the executor's raw ``ExecutionResult``.
  No display logic lives in this registry.

Lifecycle for every tool call::

    1. inject_into(tool.parameters, tool=tool)  ← adds to schema at startup;
       returns the injected set, which the caller stamps into
       ``tool.meta["_virtual_params"]`` so extraction matches injection
    2. extract_from(kwargs, only=tool.meta["_virtual_params"])  ← pops before
       HTTP call; params not injected (e.g. ``fetch_all`` on autogen tools)
       stay in kwargs and are rejected as unknown by validation
    3. apply_pre_hooks(extracted, kwargs)       ← runs pre-hooks (may mutate kwargs)
    4. executor(kwargs, extracted, ctx)         ← backend execution (HTTP or local)
    5. apply_to(result, extracted)      ← runs post-hooks (sudo cleanup only)

Adding a new virtual parameter is a single registry entry -
no other file changes needed (unless the param is tool-gated via
``tool_predicate`` — then the injection call site must pass ``tool``)."""

from __future__ import annotations

import base64
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp.tools.base import ToolResult

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
# fetch_all — in-memory skip-slice for synthetic list/search tools
# ---------------------------------------------------------------------------
# Synthetic-only: the API (autogen) loop machinery was removed (#724); the
# executor-internal loop is a post-milestone follow-up.  The param is
# injected only into synthetic tools (tool_predicate on the ``_synthetic``
# meta marker) — autogen tools no longer expose it.  For synthetic tools it
# is an in-memory skip-slice: ``format_paginated_result`` returns all items
# without page slicing.
_VIRTUAL_PARAMS["fetch_all"] = VirtualParam(
    schema={"type": "boolean"},
    default=False,
    description=(
        "When true, return all matching results without page slicing "
        "(in-memory; no HTTP loop).  Default false — single page only."
    ),
    tool_predicate=lambda t: bool((t.meta or {}).get("_synthetic")),
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
# format / detail / fetch_all — pipeline options (no hooks)
# ---------------------------------------------------------------------------
# Display concerns, not pre-request behaviour: the single result pipeline
# (tools/result_pipeline.py) reads these from the extracted dict and renders
# the executor's raw ExecutionResult.  They stay in the registry so the
# schema injection + kwarg extraction machinery is shared — but they carry
# no hooks, and no display logic lives here.

# ``detail`` registered before ``format`` so both are present in the
# extracted dict in a stable order.  No hooks — the pipeline reads them.
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
) -> set[str]:
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

    Returns:
        The set of param names actually written to *parameters* — the
        params that passed every gate (scope visibility, ``tool_predicate``,
        no shadowing).  Callers stamp this into ``tool.meta["_virtual_params"]``
        so extraction (:func:`extract_from`) matches injection exactly:
        a registry param that was *not* injected (e.g. ``fetch_all`` on an
        autogen tool) stays in kwargs and is rejected as unknown rather than
        silently dropped.

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
    injected: set[str] = set()
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
        injected.add(name)

    # Apply caller-specified default overrides (e.g. format's default
    # comes from server config, not the static registry default).
    if default_overrides:
        for name, value in default_overrides.items():
            if name in props:
                props[name]["default"] = value

    return injected


def extract_from(
    kwargs: dict[str, Any],
    only: set[str] | None = None,
) -> dict[str, Any]:
    """Pop virtual parameters from *kwargs*.

    With *only* (a per-tool allowlist from ``tool.meta["_virtual_params"]``),
    only the named parameters are popped; other registry-name keys stay in
    *kwargs* so the validation layer rejects them as unknown (they are
    neither allowlisted nor declared in the tool's schema — the value would
    otherwise be silently dropped).  Both tool families carry this allowlist:
    synthetic tools stamp it at registration, autogen tools have it stamped
    by :func:`inject_into`'s caller with the actually-injected set — so
    extraction matches injection exactly (a predicate-gated param such as
    ``fetch_all`` on an autogen tool is not popped and is rejected as
    unknown).  Without *only* (no allowlist stamped — the fallback), every
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


__all__ = [
    "VirtualParam",
    "apply_pre_hooks",
    "apply_scope_filter",
    "apply_to",
    "extract_from",
    "inject_into",
    "sudo_context",
]
