"""Helpers for building registration records in tests.

Registration metadata lives under a single ``meta[REGISTRATION_KEY]`` record
(:mod:`gitea_mcp_server.registration`).  These factories keep tests terse while
preserving the pending/finalised distinction the contract depends on:

- *pending* (``injected=None``) — ``virtual_params`` is ``None``; the exposure
  seam (``_ToolWrappingTransform._wrap``) resolves it by injection.
- *finalised* (``injected`` given) — ``virtual_params`` is the explicit
  extraction allowlist; ``build_transform_fn`` accepts it directly.
"""

from __future__ import annotations

from typing import Any

from gitea_mcp_server.models import ToolCustomization, ViewHints
from gitea_mcp_server.registration import (
    REGISTRATION_KEY,
    ResourceRegistration,
    ToolRegistration,
)


def autogen_registration(
    customization: ToolCustomization | None = None,
    *,
    output_schema_raw: dict[str, Any] | None = None,
    response_type: str | None = None,
    view_hints: ViewHints | None = None,
    injected: set[str] | frozenset[str] | None = None,
) -> ToolRegistration:
    """Build an autogen record, finalised when *injected* is given."""
    record = ToolRegistration.for_autogen(
        customization=customization if customization is not None else ToolCustomization(),
        output_schema_raw=output_schema_raw,
        response_type=response_type,
        view_hints=view_hints,
    )
    return record if injected is None else record.with_injected(injected)


def synthetic_registration(
    executor_id: str,
    allowlist: set[str] | frozenset[str],
    *,
    injected: set[str] | frozenset[str] | None = None,
) -> ToolRegistration:
    """Build a synthetic record, finalised when *injected* is given."""
    record = ToolRegistration.for_synthetic(executor_id=executor_id, allowlist=allowlist)
    return record if injected is None else record.with_injected(injected)


def autogen_meta(
    customization: ToolCustomization | None = None,
    *,
    output_schema_raw: dict[str, Any] | None = None,
    response_type: str | None = None,
    view_hints: ViewHints | None = None,
    injected: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return a ``Tool.meta`` dict carrying an autogen registration record."""
    return {
        REGISTRATION_KEY: autogen_registration(
            customization,
            output_schema_raw=output_schema_raw,
            response_type=response_type,
            view_hints=view_hints,
            injected=injected,
        ).to_dict()
    }


def synthetic_meta(
    executor_id: str,
    allowlist: set[str] | frozenset[str],
    *,
    injected: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return a ``Tool.meta`` dict carrying a synthetic registration record."""
    return {
        REGISTRATION_KEY: synthetic_registration(
            executor_id, allowlist, injected=injected
        ).to_dict()
    }


def resource_meta(
    *,
    size_hint: str = "",
    default_detail: str = "",
    required_scopes: list[str] | None = None,
    optional_params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a resource ``meta`` dict carrying a registration record."""
    return {
        REGISTRATION_KEY: ResourceRegistration(
            size_hint=size_hint,
            default_detail=default_detail,
            required_scopes=required_scopes,
            optional_params=optional_params,
        ).to_dict()
    }


__all__ = [
    "autogen_meta",
    "autogen_registration",
    "resource_meta",
    "synthetic_meta",
    "synthetic_registration",
]
