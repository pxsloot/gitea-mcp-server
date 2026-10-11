"""Cross-cutting invariant: every third-party import is a declared dependency.

The external dependency surface is derived from the import graph of both shipped
packages (``tests/helpers/import_graph.third_party_import_surface``); there is no
curated list to drift.  Every top-level third-party import must be backed by a
distribution in ``pyproject.toml`` ``[project].dependencies``, so an accidental
transitive reach (``mcp`` / ``starlette`` before #830) fails the build.

The one irreducible residue is the import-name -> distribution-name alias (PEP
503 cannot equate ``yaml`` and ``pyyaml``); it is validated both ways below, so a
stale alias fails too.  Upgrade verification is executable (``mypy`` + the suite
+ the guards), not a stored inventory (#830 DoD #3).
"""

from __future__ import annotations

import re
import tomllib

from tests.helpers.import_graph import project_root, third_party_import_surface

# Import name -> distribution name, only where they differ.  Validated both ways
# in ``test_distribution_aliases_are_not_stale``.
_DISTRIBUTION_ALIASES: dict[str, str] = {"yaml": "pyyaml"}

# A PEP 508 requirement's name ends at its first version/extras/marker delimiter.
_REQUIREMENT_NAME = re.compile(r"[<>=!~\[; (]")


def _normalise(name: str) -> str:
    """Normalise a distribution name per PEP 503 (lowercase, runs of ``-_.`` -> ``-``)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _declared() -> set[str]:
    """Normalised names of ``[project].dependencies``."""
    data = tomllib.loads((project_root() / "pyproject.toml").read_text())
    return {
        _normalise(_REQUIREMENT_NAME.split(requirement, maxsplit=1)[0])
        for requirement in data["project"]["dependencies"]
    }


def _undeclared(surface: dict[str, set[str]], declared: set[str]) -> dict[str, list[str]]:
    """Third-party top-level imports with no backing declared distribution."""
    undeclared: dict[str, list[str]] = {}
    for top, paths in sorted(surface.items()):
        distribution = _DISTRIBUTION_ALIASES.get(top, top)
        if _normalise(distribution) not in declared:
            undeclared[top] = sorted(paths)
    return undeclared


def test_every_third_party_import_is_declared() -> None:
    """Each third-party top-level import maps to a declared dependency."""
    found = _undeclared(third_party_import_surface(), _declared())
    assert not found, (
        "These third-party top-level imports are not declared in "
        f"[project].dependencies: {found}. Declare them (or add a documented "
        "alias) -- do not rely on a transitive dependency by accident."
    )


def test_distribution_aliases_are_not_stale() -> None:
    """Every alias names a real import top-level and a real declared dependency."""
    surface = third_party_import_surface()
    declared = _declared()
    for imported, distribution in _DISTRIBUTION_ALIASES.items():
        assert imported in surface, f"Alias {imported!r} is not an imported top-level"
        assert _normalise(distribution) in declared, (
            f"Alias {imported!r} -> {distribution!r} is not a declared dependency"
        )


def test_scanner_detects_planted_violation() -> None:
    """The check is not vacuous: an undeclared top-level import is flagged."""
    surface = {"fastmcp": {"fastmcp"}, "totally_unknown_pkg": {"totally_unknown_pkg"}}
    assert _undeclared(surface, {"fastmcp"}) == {"totally_unknown_pkg": ["totally_unknown_pkg"]}
