"""Virtual parameters — tool-level params handled before the API call.

Virtual parameters appear in the tool schema so agents know they exist,
but are extracted from arguments before the HTTP request is made.  After
the API call completes, a registered *post-hook* transforms the result.
A registered *pre-hook* runs between extraction and the HTTP call.

Lifecycle for every tool call::

    1. inject_into(tool.parameters)   ← adds to schema at startup
    2. extract_from(kwargs)           ← pops before HTTP call
    3. apply_pre_hooks(extracted)     ← runs pre-hooks after extraction  (NEW)
    4. apply_to(result, extracted)    ← runs post-hooks after call

Adding a new virtual parameter is a single registry entry —
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

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
            Set to ``False`` at startup for scope-gated params like
            ``sudo`` when the active token lacks the required scope.
        pre_hook: Optional ``(value) → None`` callback invoked **after**
            the parameter is extracted from kwargs but **before** the HTTP
            request is made.  Useful for storing the value in a context
            variable that downstream layers (e.g. HTTP client hooks) can
            read.
        post_hook: Optional ``(ToolResult, value) → ToolResult`` callback
            invoked after the API call with the extracted value.
    """

    schema: dict[str, Any]
    default: Any
    description: str
    visible: bool = True
    pre_hook: Callable[[Any], None] | None = None
    post_hook: Callable[[ToolResult, Any], ToolResult] | None = None


# Single source of truth for every virtual parameter.
# To add one: append an entry here.  inject_into / extract_from / apply_to
# pick it up automatically.
_VIRTUAL_PARAMS: dict[str, VirtualParam] = {}


# ---------------------------------------------------------------------------
# sudo — impersonate a user via ?sudo= query parameter
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
_VIRTUAL_PARAMS["sudo"] = VirtualParam(
    schema={"type": "string", "minLength": 1},
    default=None,
    description=(
        "Impersonate a user.  Requires an admin token.  "
        "When set to a valid username, the Gitea API executes "
        'the request as that user.  Example: "alice"'
    ),
    pre_hook=_sudo_pre_hook,
    post_hook=_sudo_post_hook,
)

# ---------------------------------------------------------------------------
# Scope-based visibility control
# ---------------------------------------------------------------------------

def set_sudo_visible(visible: bool) -> None:
    """Set whether the ``sudo`` parameter appears in tool schemas.

    Call once at startup after determining the active token's scopes.
    When set to ``False``, ``inject_into`` will skip adding the ``sudo``
    virtual param to every tool's schema, so agents never discover it.

    Args:
        visible: ``True`` to show sudo (default), ``False`` to hide it.
    """
    _VIRTUAL_PARAMS["sudo"].visible = visible


# ---------------------------------------------------------------------------
# Lifecycle functions
# ---------------------------------------------------------------------------


def inject_into(parameters: dict[str, Any]) -> None:
    """Add every virtual parameter to *parameters* (a tool's parameter schema).

    Idempotent — skips any parameter name that already exists, which also
    guards against shadowing a real API parameter.

    Scope-gated params (like ``sudo``) are only injected when the active
    token has the required scope — see :func:`set_sudo_visible`.
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


__all__ = [
    "VirtualParam",
    "apply_pre_hooks",
    "apply_to",
    "extract_from",
    "inject_into",
    "set_sudo_visible",
    "sudo_context",
]
