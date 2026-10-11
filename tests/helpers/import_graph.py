"""Source-tree discovery and import-graph extraction for structural tests.

Shared by the module-surface guard (``test_architecture_doc``), the layer
contract (``test_layer_contract``), and the import-smoke tests
(``test_module_imports``).  Discovery is filesystem-driven, so the module
surface cannot drift from a hand-maintained list.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_PACKAGE = "gitea_mcp_server"
CLIENT_PACKAGE = "gitea_mcp_client"

# Both packages ship in the wheel, so structural guards scan both.
PACKAGES: tuple[str, ...] = (PROJECT_PACKAGE, CLIENT_PACKAGE)


def project_root() -> Path:
    """Return the repository root (the directory containing the packages)."""
    return Path(__file__).resolve().parents[2]


def source_dir(package: str = PROJECT_PACKAGE) -> Path:
    """Return the root of *package*."""
    return project_root() / package


def discover_modules(package: str = PROJECT_PACKAGE) -> dict[str, Path]:
    """Map every importable module in *package* to its source file.

    Keys are dotted module names.  Package ``__init__`` files map to the
    package name; ``__pycache__`` is skipped.
    """
    root = source_dir(package)
    modules: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        dotted = ".".join([package, *parts]) if parts else package
        modules[dotted] = path
    return modules


def production_modules(package: str = PROJECT_PACKAGE) -> dict[str, Path]:
    """Modules backed by a real source file (excludes package ``__init__``)."""
    return {
        name: path for name, path in discover_modules(package).items() if path.name != "__init__.py"
    }


def package_of(dotted: str, path: Path) -> str:
    """Return *dotted*'s ``__package__`` (itself for a package ``__init__``).

    This is the importer's package, the value relative imports resolve
    against — not a scan scope.
    """
    if path.name == "__init__.py":
        return dotted
    return dotted.rsplit(".", 1)[0]


def resolve_from_module(node: ast.ImportFrom, package: str) -> str:
    """Absolute module an ``ImportFrom`` imports *from* (no alias symbols).

    ``package`` is the importing module's ``__package__``.  Resolving
    ``node.level`` against it means ``from .sibling import x`` yields the
    absolute sibling module, not only absolute imports.
    """
    module = node.module or ""
    if node.level == 0:
        return module
    parts = package.split(".") if package else []
    base_parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
    base = ".".join(base_parts)
    return f"{base}.{module}" if module else base


def resolve_import_targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Absolute module targets of an import node, with relatives resolved.

    ``package`` is the importing module's ``__package__``.  Resolving
    ``node.level`` against it means ``from .openapi_converter import x`` and
    ``from . import y`` are caught, not only absolute imports.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level > 0 and node.module is None:
        base = resolve_from_module(node, package)
        return [f"{base}.{alias.name}" if base else alias.name for alias in node.names]
    module = resolve_from_module(node, package)
    if node.level == 0:
        targets = [module] if module else []
        targets.extend(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
        return targets
    return [module]


def _longest_known(target: str, known: set[str]) -> str | None:
    """Normalize *target* to the longest known module prefix, if any."""
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in known:
            return candidate
    return None


def _ancestor_packages(dotted: str) -> set[str]:
    """Strict ancestor packages of *dotted* (imported by Python on every import)."""
    parts = dotted.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts))}


def imported_project_modules(dotted: str, path: Path, known: set[str]) -> set[str]:
    """In-package modules imported by *dotted* (module-level and deferred).

    A target is normalized to the longest known module prefix, so
    ``from pkg import name`` resolves to ``pkg.name`` when ``name`` is a module
    and to ``pkg`` otherwise.  Ancestor packages of *dotted* are skipped: they
    are structural (Python imports them implicitly), not dependencies, so
    ``from gitea_mcp_server import models`` does not read as an import of the
    package ``__init__``.
    """
    package = package_of(dotted, path)
    ancestors = _ancestor_packages(dotted)
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target in resolve_import_targets(node, package):
            if target in ancestors:
                continue
            normalized = _longest_known(target, known)
            if normalized and normalized != dotted:
                found.add(normalized)
    return found


def build_import_graph() -> dict[str, set[str]]:
    """Map every in-package module to the in-package modules it imports."""
    modules = discover_modules()
    known = set(modules)
    return {name: imported_project_modules(name, path, known) for name, path in modules.items()}


def _import_module_paths(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Absolute module paths named by an import node (no alias symbols).

    ``ast.Import`` names its modules directly; ``ImportFrom`` names the module
    it imports *from*.  Symbols in ``from pkg import name`` are not modules and
    are excluded.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    module = resolve_from_module(node, package)
    return [module] if module else []


def third_party_import_surface() -> dict[str, set[str]]:
    """Map each third-party top-level import name to the module paths reached.

    Derived from the source of both shipped packages (:data:`PACKAGES`).
    First-party modules and the stdlib (``sys.stdlib_module_names``) are
    excluded, so the returned structure *is* the external dependency surface —
    there is no curated list to drift from the code.
    """
    first_party = set(PACKAGES)
    surface: dict[str, set[str]] = {}
    for package in PACKAGES:
        for dotted, path in discover_modules(package).items():
            importer_package = package_of(dotted, path)
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                for target in _import_module_paths(node, importer_package):
                    top = target.split(".")[0]
                    if not top or top in first_party or top in sys.stdlib_module_names:
                        continue
                    surface.setdefault(top, set()).add(target)
    return surface
