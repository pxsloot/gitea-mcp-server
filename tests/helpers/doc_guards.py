"""Reusable scanners for the executable documentation maps.

Shared by the guards that make a documentation map match reality: the
``ARCHITECTURE.md`` Module Map (``test_architecture_doc``) and the capability
view's canonical homes (``test_capabilities_doc``).  Both assert that the
module and package paths named in a doc resolve to real production files.

Resolution is filesystem-driven (``import_graph``), so a named home cannot
drift from the real surface, and a second map guard is a thin caller rather
than a near-duplicate scanner.  The token shapes are the ones the docs already
write in backticks: ``tools/filter_info.py`` / ``filter_info.py`` for a module
and ``openapi_converter/`` for a package directory.
"""

from __future__ import annotations

import re

from tests.helpers.import_graph import production_modules, source_dir

MODULE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")
PACKAGE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_]+/)`")


def module_tokens(text: str) -> set[str]:
    """Backticked ``*.py`` paths named in *text*."""
    return set(MODULE_TOKEN_RE.findall(text))


def package_tokens(text: str) -> set[str]:
    """Backticked package-directory tokens (``openapi_converter/``) named in *text*."""
    return set(PACKAGE_TOKEN_RE.findall(text))


def production_rel_to_basename() -> dict[str, str]:
    """Map each production module's package-relative path to its basename."""
    return {
        "/".join(path.relative_to(source_dir()).parts): path.name
        for path in production_modules().values()
    }


def dangling_modules(tokens: set[str]) -> list[str]:
    """Tokens that resolve to no production module (package-relative or basename)."""
    rel_paths = production_rel_to_basename()
    basenames = set(rel_paths.values())
    return sorted(token for token in tokens if token not in rel_paths and token not in basenames)


def dangling_packages(tokens: set[str]) -> list[str]:
    """Tokens that resolve to no package directory under the source root."""
    root = source_dir()
    return sorted(token for token in tokens if not (root / token / "__init__.py").exists())


__all__ = [
    "MODULE_TOKEN_RE",
    "PACKAGE_TOKEN_RE",
    "dangling_modules",
    "dangling_packages",
    "module_tokens",
    "package_tokens",
    "production_rel_to_basename",
]
