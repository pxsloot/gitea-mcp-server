"""Virtual parameters — tool-level params handled before the API call.

Virtual parameters appear in the tool schema so agents know they exist,
but are extracted from arguments before the HTTP request is made.  After
the API call completes, a registered *post-hook* transforms the result.

Lifecycle for every tool call::

    1. inject_into(tool.parameters)   ← adds to schema at startup
    2. extract_from(kwargs)           ← pops before HTTP call
    3. apply_to(result, extracted)    ← runs post-hooks after call

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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp.tools.base import ToolResult

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VirtualParam:
    """A parameter that lives in the tool schema but is handled pre-call.

    Attributes:
        schema: JSON Schema fragment for the parameter (type, enum, etc.).
        default: Default value used when the agent omits the parameter.
        description: Description shown to agents in the tool schema.
        post_hook: Optional ``(ToolResult, value) → ToolResult`` callback
            invoked after the API call with the extracted value.
    """

    schema: dict[str, Any]
    default: Any
    description: str
    post_hook: Callable[[ToolResult, Any], ToolResult] | None = None


# Single source of truth for every virtual parameter.
# To add one: append an entry here.  inject_into / extract_from / apply_to
# pick it up automatically.
_VIRTUAL_PARAMS: dict[str, VirtualParam] = {}


# ---------------------------------------------------------------------------
# Lifecycle functions
# ---------------------------------------------------------------------------


def inject_into(parameters: dict[str, Any]) -> None:
    """Add every virtual parameter to *parameters* (a tool's parameter schema).

    Idempotent — skips any parameter name that already exists, which also
    guards against shadowing a real API parameter.
    """
    props = parameters.setdefault("properties", {})
    for name, vp in _VIRTUAL_PARAMS.items():
        if name not in props:
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
    "apply_to",
    "extract_from",
    "inject_into",
]
