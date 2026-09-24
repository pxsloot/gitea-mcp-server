"""Source-tree discovery and import-graph extraction for structural tests.

Shared by the module-surface guard (``test_architecture_doc``), the layer
contract (``test_layer_contract``), and the import-smoke tests
(``test_module_imports``).  Discovery is filesystem-driven, so the module
surface cannot drift from a hand-maintained list.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_PACKAGE = "gitea_mcp_server"


def project_root() -> Path:
    """Return the repository root (the directory containing the package)."""
    return Path(__file__).resolve().parents[2]


def source_dir() -> Path:
    """Return the root of the ``gitea_mcp_server`` package."""
    return project_root() / PROJECT_PACKAGE


def discover_modules() -> dict[str, Path]:
    """Map every importable module in the package to its source file.

    Keys are dotted module names.  Package ``__init__`` files map to the
    package name; ``__pycache__`` is skipped.
    """
    root = source_dir()
    modules: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        dotted = ".".join([PROJECT_PACKAGE, *parts]) if parts else PROJECT_PACKAGE
        modules[dotted] = path
    return modules


def production_modules() -> dict[str, Path]:
    """Modules backed by a real source file (excludes package ``__init__``)."""
    return {name: path for name, path in discover_modules().items() if path.name != "__init__.py"}


def _package_of(dotted: str, path: Path) -> str:
    if path.name == "__init__.py":
        return dotted
    return dotted.rsplit(".", 1)[0]


def resolve_import_targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Absolute module targets of an import node, with relatives resolved.

    ``package`` is the importing module's ``__package__``.  Resolving
    ``node.level`` against it means ``from .openapi_converter import x`` and
    ``from . import y`` are caught, not only absolute imports.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    module = node.module or ""
    if node.level == 0:
        targets = [module] if module else []
        targets.extend(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
        return targets
    parts = package.split(".") if package else []
    base_parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
    base = ".".join(base_parts)
    if module:
        return [f"{base}.{module}" if base else module]
    return [f"{base}.{alias.name}" if base else alias.name for alias in node.names]


def _longest_known(target: str, known: set[str]) -> str | None:
    """Normalize *target* to the longest known module prefix, if any."""
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in known:
            return candidate
    return None


def imported_project_modules(dotted: str, path: Path, known: set[str]) -> set[str]:
    """In-package modules imported by *dotted* (module-level and deferred).

    A target is normalized to the longest known module prefix, so
    ``from pkg import name`` resolves to ``pkg.name`` when ``name`` is a module
    and to ``pkg`` otherwise.
    """
    package = _package_of(dotted, path)
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target in resolve_import_targets(node, package):
            normalized = _longest_known(target, known)
            if normalized and normalized != dotted:
                found.add(normalized)
    return found


def build_import_graph() -> dict[str, set[str]]:
    """Map every in-package module to the in-package modules it imports."""
    modules = discover_modules()
    known = set(modules)
    return {name: imported_project_modules(name, path, known) for name, path in modules.items()}
