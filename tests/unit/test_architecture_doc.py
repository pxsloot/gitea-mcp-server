"""The architecture doc's Module Map must match the real module surface.

The map is the first thing an agent reads to orient in this codebase, and a
stale entry sends the next investigation down the wrong path.  These tests make
the map executable: every file it names must exist, and every production module
must be named.
"""

from __future__ import annotations

import re

from tests.helpers.import_graph import PROJECT_PACKAGE, production_modules, project_root, source_dir

_DOC = project_root() / "docs" / "ARCHITECTURE.md"
_MODULE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")
_PACKAGE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_]+/)`")


def _module_map_section() -> str:
    text = _DOC.read_text()
    start = text.index("## Module Map")
    end = text.index("## Key Design Decisions")
    return text[start:end]


def _module_tokens() -> set[str]:
    return set(_MODULE_TOKEN_RE.findall(_module_map_section()))


def _production_rel_paths() -> dict[str, str]:
    """Map each production module's package-relative path to its basename."""
    return {
        "/".join(path.relative_to(source_dir()).parts): path.name
        for path in production_modules().values()
    }


def test_named_modules_exist() -> None:
    """Every module named in the Module Map resolves to a real file."""
    rel_paths = set(_production_rel_paths())
    basenames = set(_production_rel_paths().values())
    dangling = [
        token
        for token in sorted(_module_tokens())
        if token not in rel_paths and token not in basenames
    ]
    assert not dangling, f"Module Map names files that do not exist: {dangling}"


def test_every_production_module_is_named() -> None:
    """Every production module appears in the Module Map."""
    rel_to_base = _production_rel_paths()
    tokens = _module_tokens()
    base_counts: dict[str, int] = {}
    for base in rel_to_base.values():
        base_counts[base] = base_counts.get(base, 0) + 1
    missing = [
        rel
        for rel, base in sorted(rel_to_base.items())
        if rel not in tokens and not (base in tokens and base_counts[base] == 1)
    ]
    assert not missing, f"Production modules absent from the Module Map: {missing}"


def test_named_packages_exist() -> None:
    """Every package directory named in the Module Map exists."""
    packages = set(_PACKAGE_TOKEN_RE.findall(_module_map_section()))
    root = source_dir()
    dangling = [p for p in sorted(packages) if not (root / p / "__init__.py").exists()]
    assert not dangling, f"Module Map names packages that do not exist: {dangling}"


def test_project_package_is_the_expected_name() -> None:
    """Guard against a silent rename of the package the map is written against."""
    assert (project_root() / PROJECT_PACKAGE).is_dir()
