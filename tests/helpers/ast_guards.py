"""Reusable AST guards for structural invariants.

Shared by cross-cutting guard tests that assert a call may appear in only one
production module (e.g. the contract spine is the sole MCP-progress emitter,
#825; the spine is the sole ``run_validation`` caller, #790), and by the
underscore-private boundary guard (#830).  Keeping the scanners here means a
second guard is a thin caller, not a near-duplicate.

The scans are deliberately shallow: they match syntax, not dataflow.  Aliasing
(``rp = ctx.report_progress; rp(...)``), dynamic dispatch
(``getattr(ctx, "report_progress")(...)``), dynamic imports
(``importlib.import_module("pkg._x")``) and ``__import__`` are out of scope.
So is **direct attribute access** (``obj._x``): without an ownership model a
scan cannot tell a legitimate self-access from a cross-boundary reach, so that
residual is tracked separately from #830.  That is the right trade for a
reintroduction deny-list — the goal is to fail a copy-paste regression loudly,
not to perform taint analysis.
"""

from __future__ import annotations

import ast

from tests.helpers.import_graph import resolve_from_module


def _is_private(name: str) -> bool:
    """True for an underscore-private name; dunder names are not private.

    ``__future__``, ``__all__``, ``__version__`` are outside the rule, as is the
    bare ``_`` — the gettext translation alias, not a private symbol.
    """
    if name == "_":
        return False
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


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


def iter_private_imports(source: str, importer_package: str) -> list[tuple[int, str, str]]:
    """Return ``(lineno, target, private_name)`` for every private import.

    A private import couples *source* to an underscore-private name across a
    boundary: ``from pkg import _x``, ``import pkg._x``, or a private module
    component (``from pkg._mod import x``).  ``importer_package`` is the
    importing module's ``__package__``, used only to resolve relative imports
    (``from .sibling import _x``); it is *not* a scan scope.

    ``target`` is the absolute module path named by the import; ``private_name``
    is the offending component, so a caller can key an allowlist on it.  Dunder
    names are not private; ``from __future__ import annotations`` yields nothing.

    Results are sorted by ``(lineno, target, private_name)``.
    """
    matches: list[tuple[int, str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for part in alias.name.split("."):
                    if _is_private(part):
                        matches.append((node.lineno, alias.name, part))
        elif isinstance(node, ast.ImportFrom):
            module = resolve_from_module(node, importer_package)
            for part in module.split("."):
                if _is_private(part):
                    matches.append((node.lineno, module, part))
            for alias in node.names:
                if alias.name != "*" and _is_private(alias.name):
                    matches.append((node.lineno, module, alias.name))
    matches.sort()
    return matches


def iter_private_getattr(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, name)`` for every ``getattr(obj, "_name")`` literal.

    The attribute name is a string literal starting with a single underscore;
    dunder literals (``getattr(obj, "__x__")``) are not private.
    """
    matches: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "getattr" or len(node.args) < 2:
            continue
        attribute = node.args[1]
        if (
            isinstance(attribute, ast.Constant)
            and isinstance(attribute.value, str)
            and _is_private(attribute.value)
        ):
            matches.append((node.lineno, attribute.value))
    matches.sort()
    return matches


__all__ = ["iter_call_sites", "iter_private_getattr", "iter_private_imports"]
