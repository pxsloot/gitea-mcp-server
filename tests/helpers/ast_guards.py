"""Reusable AST guards for structural invariants.

Shared by cross-cutting guard tests that assert a call may appear in only one
production module (e.g. the contract spine is the sole MCP-progress emitter,
#825; the spine is the sole ``run_validation`` caller, #790).  Keeping the
scanner here means a second guard is a thin caller, not a near-duplicate.

The scan is deliberately shallow: it matches the *callee syntax*, so aliasing
(``rp = ctx.report_progress; rp(...)``) and dynamic dispatch
(``getattr(ctx, "report_progress")(...)``) are out of scope.  That is the right
trade for a reintroduction deny-list — the goal is to fail a copy-paste
regression loudly, not to perform taint analysis.
"""

from __future__ import annotations

import ast


def iter_call_sites(
    source: str,
    *,
    names: frozenset[str] = frozenset(),
    attrs: frozenset[str] = frozenset(),
) -> list[tuple[int, str]]:
    """Return ``(lineno, called_name)`` for every matching ``Call`` in *source*.

    A call matches when its ``func`` is a bare :class:`ast.Name` whose ``id`` is
    in *names* (e.g. ``run_validation(...)``), or an :class:`ast.Attribute`
    whose ``attr`` is in *attrs* (e.g. ``ctx.report_progress(...)``).  The
    matched identifier is returned so callers can map it to a domain-specific
    kind.

    Results are sorted by ``(lineno, called_name)`` for deterministic output.
    """
    matches: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in names:
            matches.append((node.lineno, func.id))
        elif isinstance(func, ast.Attribute) and func.attr in attrs:
            matches.append((node.lineno, func.attr))
    matches.sort()
    return matches


__all__ = ["iter_call_sites"]
